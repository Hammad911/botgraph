"""Build a directed host communication graph (with node and edge features) from one window.

Output is plain NumPy so this package stays free of PyTorch; the ML code converts a
``WindowGraph`` into a PyG ``Data`` object. All heavy-tailed counts are ``log1p``
transformed here; standard scaling happens in the ML pipeline with train-only stats.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_address, ip_network

import numpy as np
import pandas as pd

from botgraph_core.schema import Label

EDGE_FEATURES: tuple[str, ...] = (
    "log_flows",
    "log_bytes",
    "log_mean_bytes",
    "log_pkts",
    "log_mean_duration",
    "log_dst_ports",
    "frac_tcp",
    "frac_udp",
    "frac_icmp",
    "failed_ratio",
    "log_iat_mean",
    "periodicity",
)

NODE_FEATURES: tuple[str, ...] = (
    "is_internal",
    "log_out_degree",
    "log_in_degree",
    "log_out_flows",
    "log_in_flows",
    "log_unique_dst_ports",
    "log_bytes_sent",
    "log_bytes_recv",
    "sent_ratio",
    "failed_ratio",
    "frac_dns",
    "frac_smtp",
    "frac_irc",
    "periodicity",
    "dst_entropy",
)

LABEL_TO_INT: dict[Label, int] = {Label.BOTNET: 1, Label.BENIGN: 0, Label.UNKNOWN: -1}

_SMTP_PORTS = (25, 465, 587)
_IRC_PORTS = (*range(6660, 6670), 6697)


@dataclass(frozen=True, slots=True)
class GraphConfig:
    internal_nets: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    # Minimum flows on an edge before its inter-arrival regularity is trusted.
    min_flows_for_periodicity: int = 3


@dataclass(frozen=True, slots=True)
class WindowGraph:
    window_id: str
    nodes: list[str]  # node index -> IP
    x: np.ndarray  # (N, len(NODE_FEATURES)) float32
    edge_index: np.ndarray  # (2, E) int64, row 0 = src, row 1 = dst
    edge_attr: np.ndarray  # (E, len(EDGE_FEATURES)) float32
    y: np.ndarray  # (N,) int8: 1 botnet, 0 benign, -1 unknown (excluded from loss)

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def num_edges(self) -> int:
        return int(self.edge_index.shape[1])


@lru_cache(maxsize=16)
def _parsed_nets(nets: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(ip_network(n) for n in nets)


def is_internal(ip: str, nets: tuple[str, ...]) -> bool:
    addr = ip_address(ip)
    return any(addr in net for net in _parsed_nets(nets))  # type: ignore[operator]


def _periodicity(
    iat_mean: pd.Series, iat_std: pd.Series, n_flows: pd.Series, min_flows: int
) -> pd.Series:
    """1 / (1 + coefficient of variation) of inter-arrival times; 1.0 = perfectly regular beacon."""
    cv = (iat_std.fillna(0.0) / iat_mean.where(iat_mean > 0)).fillna(0.0)
    score = 1.0 / (1.0 + cv)
    return score.where(n_flows >= min_flows, 0.0)


def build_window_graph(
    flows: pd.DataFrame,
    window_id: str,
    config: GraphConfig | None = None,
    host_labels: Mapping[str, Label | str] | None = None,
) -> WindowGraph:
    if flows.empty:
        raise ValueError("cannot build a graph from an empty window")
    config = config or GraphConfig()

    df = flows[
        [
            "ts",
            "duration",
            "proto",
            "src_ip",
            "dst_ip",
            "dst_port",
            "src_bytes",
            "dst_bytes",
            "pkts",
        ]
    ].copy()

    nodes = pd.Index(pd.unique(pd.concat([df["src_ip"], df["dst_ip"]], ignore_index=True)))
    n = len(nodes)
    df["s"] = nodes.get_indexer(pd.Index(df["src_ip"]))
    df["d"] = nodes.get_indexer(pd.Index(df["dst_ip"]))

    port = df["dst_port"].fillna(-1).astype("int64")
    proto = df["proto"].astype(str)
    df["failed"] = (df["dst_bytes"] == 0).astype("float64")
    df["total_bytes"] = df["src_bytes"] + df["dst_bytes"]
    df["is_tcp"] = (proto == "tcp").astype("float64")
    df["is_udp"] = (proto == "udp").astype("float64")
    df["is_icmp"] = (proto == "icmp").astype("float64")
    df["is_dns"] = (port == 53).astype("float64")
    df["is_smtp"] = port.isin(_SMTP_PORTS).astype("float64")
    df["is_irc"] = port.isin(_IRC_PORTS).astype("float64")

    df = df.sort_values(["s", "d", "ts"], kind="stable")
    df["iat"] = df.groupby(["s", "d"], sort=False)["ts"].diff()

    # ------------------------------------------------------------------ edges
    edges = df.groupby(["s", "d"], sort=True).agg(
        n_flows=("ts", "size"),
        bytes=("total_bytes", "sum"),
        pkts=("pkts", "sum"),
        mean_duration=("duration", "mean"),
        n_dst_ports=("dst_port", "nunique"),
        frac_tcp=("is_tcp", "mean"),
        frac_udp=("is_udp", "mean"),
        frac_icmp=("is_icmp", "mean"),
        failed_ratio=("failed", "mean"),
        iat_mean=("iat", "mean"),
        iat_std=("iat", "std"),
    )
    edges["periodicity"] = _periodicity(
        edges["iat_mean"], edges["iat_std"], edges["n_flows"], config.min_flows_for_periodicity
    )
    edge_attr = np.column_stack(
        [
            np.log1p(edges["n_flows"]),
            np.log1p(edges["bytes"]),
            np.log1p(edges["bytes"] / edges["n_flows"]),
            np.log1p(edges["pkts"]),
            np.log1p(edges["mean_duration"].fillna(0.0)),
            np.log1p(edges["n_dst_ports"]),
            edges["frac_tcp"],
            edges["frac_udp"],
            edges["frac_icmp"],
            edges["failed_ratio"],
            np.log1p(edges["iat_mean"].fillna(0.0)),
            edges["periodicity"],
        ]
    ).astype(np.float32)
    src_of_edge = edges.index.get_level_values("s").to_numpy()
    dst_of_edge = edges.index.get_level_values("d").to_numpy()
    edge_index = np.vstack([src_of_edge, dst_of_edge]).astype(np.int64)

    # ------------------------------------------------------------------ nodes
    idx = pd.RangeIndex(n)

    def per_node(series: pd.Series) -> np.ndarray:
        return series.reindex(idx, fill_value=0).to_numpy(dtype=np.float64)

    out_g = df.groupby("s")
    out_flows = per_node(out_g.size())
    in_flows = per_node(df.groupby("d").size())
    out_degree = per_node(pd.Series(src_of_edge).value_counts())
    in_degree = per_node(pd.Series(dst_of_edge).value_counts())
    unique_ports = per_node(out_g["dst_port"].nunique())

    # A host sends src_bytes when it initiates and dst_bytes when it responds.
    sent = per_node(out_g["src_bytes"].sum()) + per_node(df.groupby("d")["dst_bytes"].sum())
    recv = per_node(out_g["dst_bytes"].sum()) + per_node(df.groupby("d")["src_bytes"].sum())

    edge_frame = pd.DataFrame(
        {"s": src_of_edge, "n": edges["n_flows"].to_numpy(), "per": edges["periodicity"].to_numpy()}
    )
    periodicity = per_node(edge_frame.groupby("s")["per"].max())

    # Normalised Shannon entropy of a host's outgoing flows across destinations.
    p = edge_frame["n"] / edge_frame["s"].map(pd.Series(out_flows))
    edge_frame["plogp"] = -(p * np.log(p))
    raw_entropy = per_node(edge_frame.groupby("s")["plogp"].sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        dst_entropy = np.where(out_degree > 1, raw_entropy / np.log(out_degree), 0.0)

    internal = np.array([is_internal(ip, config.internal_nets) for ip in nodes], dtype=np.float64)

    x = np.column_stack(
        [
            internal,
            np.log1p(out_degree),
            np.log1p(in_degree),
            np.log1p(out_flows),
            np.log1p(in_flows),
            np.log1p(unique_ports),
            np.log1p(sent),
            np.log1p(recv),
            sent / np.maximum(sent + recv, 1.0),
            per_node(out_g["failed"].mean()),
            per_node(out_g["is_dns"].mean()),
            per_node(out_g["is_smtp"].mean()),
            per_node(out_g["is_irc"].mean()),
            periodicity,
            dst_entropy,
        ]
    ).astype(np.float32)

    labels = host_labels or {}
    y = np.array(
        [LABEL_TO_INT[Label(labels.get(ip, Label.UNKNOWN))] for ip in nodes], dtype=np.int8
    )

    return WindowGraph(
        window_id=window_id,
        nodes=[str(ip) for ip in nodes],
        x=x,
        edge_index=edge_index,
        edge_attr=edge_attr,
        y=y,
    )
