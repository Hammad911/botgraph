"""Parsers that turn raw dataset formats into the unified flow frame.

Supported sources:

* CTU-13 labelled bidirectional NetFlow (``.binetflow``, Argus CSV export)
* Zeek ``conn.log`` / IoT-23 ``conn.log.labeled`` (TSV with ``#fields`` header)
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO

import numpy as np
import pandas as pd

from botgraph_core.schema import FLOW_COLUMNS, Label, Proto, is_ip, validate_frame

DEFAULT_CHUNKSIZE = 500_000

_KNOWN_PROTOS = {p.value for p in Proto}


def _ip_flows_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop non-IP flows. Argus (CTU-13) also records ARP and other L2 traffic, whose
    "addresses" are MACs; they are ~0.001% of rows and never labelled."""
    return frame[is_ip(frame["src_ip"]) & is_ip(frame["dst_ip"])]


def _concat(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    parts = list(frames)
    if not parts:
        return validate_frame(pd.DataFrame(columns=FLOW_COLUMNS))
    return pd.concat(parts, ignore_index=True)


def _normalize_proto(series: pd.Series) -> pd.Series:
    lowered = series.astype("string").str.lower().str.strip()
    return lowered.where(lowered.isin(_KNOWN_PROTOS), Proto.OTHER.value)


def _parse_port(series: pd.Series) -> pd.Series:
    """Ports may be decimal, hex (``0x0303`` for ICMP type/code in Argus) or empty."""

    def conv(value: object) -> int | None:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        text = str(value).strip()
        if not text or text == "-":
            return None
        try:
            port = int(text, 0)
        except ValueError:
            return None
        return port if 0 <= port <= 65535 else None

    return series.map(conv).astype("Int32")


# --------------------------------------------------------------------------- CTU-13


def ctu13_flow_label(raw: str) -> Label:
    """Map CTU-13 labels such as ``flow=From-Botnet-V42-TCP-CC6`` to the unified label."""
    if "Botnet" in raw:
        return Label.BOTNET
    if "Normal" in raw:
        return Label.BENIGN
    return Label.UNKNOWN  # "Background" traffic is unlabelled


def iter_ctu13_binetflow(
    source: str | Path | IO[str], sensor_id: str = "ctu13", chunksize: int = DEFAULT_CHUNKSIZE
) -> Iterator[pd.DataFrame]:
    """Stream a ``.binetflow`` file as validated flow frames of at most ``chunksize`` rows."""
    for raw in pd.read_csv(source, dtype=str, keep_default_na=False, chunksize=chunksize):
        yield _ctu13_frame(raw, sensor_id)


def read_ctu13_binetflow(source: str | Path | IO[str], sensor_id: str = "ctu13") -> pd.DataFrame:
    return _concat(iter_ctu13_binetflow(source, sensor_id))


def _ctu13_frame(raw: pd.DataFrame, sensor_id: str) -> pd.DataFrame:
    raw.columns = [c.strip() for c in raw.columns]

    ts = pd.to_datetime(raw["StartTime"], format="%Y/%m/%d %H:%M:%S.%f", utc=True)
    tot_bytes = pd.to_numeric(raw["TotBytes"], errors="coerce").fillna(0).astype("int64")
    src_bytes = pd.to_numeric(raw["SrcBytes"], errors="coerce").fillna(0).astype("int64")

    frame = pd.DataFrame(
        {
            # Resolution-independent (pandas 2 may store microseconds, not nanoseconds).
            "ts": (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds(),
            "duration": pd.to_numeric(raw["Dur"], errors="coerce").fillna(0.0).clip(lower=0),
            "proto": _normalize_proto(raw["Proto"]),
            "src_ip": raw["SrcAddr"].str.strip(),
            "src_port": _parse_port(raw["Sport"]),
            "dst_ip": raw["DstAddr"].str.strip(),
            "dst_port": _parse_port(raw["Dport"]),
            "src_bytes": src_bytes,
            "dst_bytes": (tot_bytes - src_bytes).clip(lower=0),
            "pkts": pd.to_numeric(raw["TotPkts"], errors="coerce").fillna(0).astype("int64"),
            "label": raw["Label"].map(lambda s: ctu13_flow_label(s).value),
            "sensor_id": sensor_id,
        }
    )
    return validate_frame(_ip_flows_only(frame))


# --------------------------------------------------------------------------- Zeek / IoT-23


def _zeek_fields(lines: Iterable[str]) -> list[str]:
    for line in lines:
        if line.startswith("#fields"):
            # IoT-23 separates its trailing label columns with spaces instead of tabs.
            return [name for token in line.rstrip("\n").split("\t")[1:] for name in token.split()]
    raise ValueError("no #fields header found in Zeek log")


def iot23_flow_label(raw: str) -> Label:
    lowered = raw.strip().lower()
    if lowered == "malicious":
        return Label.BOTNET
    if lowered == "benign":
        return Label.BENIGN
    return Label.UNKNOWN


def iter_zeek_conn(
    path: str | Path, sensor_id: str = "zeek", chunksize: int = DEFAULT_CHUNKSIZE
) -> Iterator[pd.DataFrame]:
    """Stream a Zeek ``conn.log`` as validated flow frames of at most ``chunksize`` rows."""
    path = Path(path)
    with path.open() as fh:
        fields = _zeek_fields(fh)

    reader = pd.read_csv(
        path,
        sep="\t",
        comment="#",
        header=None,
        dtype=str,
        keep_default_na=False,
        chunksize=chunksize,
    )
    for raw in reader:
        yield _zeek_frame(raw, fields, sensor_id)


def read_zeek_conn(path: str | Path, sensor_id: str = "zeek") -> pd.DataFrame:
    return _concat(iter_zeek_conn(path, sensor_id))


def _zeek_frame(raw: pd.DataFrame, fields: list[str], sensor_id: str) -> pd.DataFrame:
    if raw.shape[1] < len(fields):
        # Expand the space-separated trailing columns (IoT-23 quirk).
        head = raw.iloc[:, :-1]
        tail = raw.iloc[:, -1].str.split(r"\s+", n=len(fields) - raw.shape[1], expand=True)
        raw = pd.concat([head, tail], axis=1)
    raw.columns = fields[: raw.shape[1]]

    def num(col: str, integer: bool = False) -> pd.Series:
        values = pd.to_numeric(raw[col].replace("-", np.nan), errors="coerce").fillna(0)
        return values.astype("int64") if integer else values.astype("float64")

    labels = raw["label"] if "label" in raw.columns else pd.Series("-", index=raw.index)

    frame = pd.DataFrame(
        {
            "ts": num("ts"),
            "duration": num("duration").clip(lower=0),
            "proto": _normalize_proto(raw["proto"]),
            "src_ip": raw["id.orig_h"],
            "src_port": _parse_port(raw["id.orig_p"]),
            "dst_ip": raw["id.resp_h"],
            "dst_port": _parse_port(raw["id.resp_p"]),
            "src_bytes": num("orig_bytes", integer=True),
            "dst_bytes": num("resp_bytes", integer=True),
            "pkts": num("orig_pkts", integer=True) + num("resp_pkts", integer=True),
            "label": labels.map(lambda s: iot23_flow_label(s).value),
            "sensor_id": sensor_id,
        }
    )
    return validate_frame(_ip_flows_only(frame))
