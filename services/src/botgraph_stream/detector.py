"""Detector: flow stream -> sliding windows -> host graph -> GNN scores, per sensor.

Graph building and scoring happen in the same process on purpose: IoT scanning windows produce
multi-megabyte graphs, so only the small per-host scores go on the bus. Scale out by giving each
detector instance a subset of sensors (Kafka partitions keyed by sensor_id).

    flows.normalized {"sensor_id", "flows": [...]}
      -> detections {"sensor_id", "window_id", "window_start", "window_end", "n_flows",
                     "n_nodes", "n_edges", "latency_ms", "hosts": [{"ip", "score"}, ...]}

Only internal hosts are reported: external addresses are context in the graph, not assets.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch_geometric.data import Data

from botgraph_core import (
    EDGE_FEATURES,
    NODE_FEATURES,
    GraphConfig,
    Window,
    WindowGraph,
    WindowSpec,
    build_window_graph,
)
from botgraph_ml.gnn.data import to_data
from botgraph_ml.gnn.explain import explain_node
from botgraph_ml.gnn.models import load_bundle, load_metadata
from botgraph_stream.windower import StreamingWindower

_INTERNAL = NODE_FEATURES.index("is_internal")
RECENT_GRAPHS = 3


@dataclass
class DetectorStats:
    flows: int = 0
    windows: int = 0
    host_scores: int = 0
    latencies_ms: list[float] = field(default_factory=list)


def ego_graph(graph: WindowGraph, ip: str, hops: int) -> tuple[WindowGraph, int]:
    """Induced subgraph of every host within ``hops`` of ``ip``, ignoring edge direction
    (to_data adds reverse edges, so messages flow both ways). Node features are copied from
    the full window, not recomputed, so the host's score is unchanged."""
    src, dst = graph.edge_index
    keep = np.zeros(graph.num_nodes, dtype=bool)
    keep[graph.nodes.index(ip)] = True
    for _ in range(hops):
        reached = keep[src] | keep[dst]
        keep[src[reached]] = True
        keep[dst[reached]] = True
    new_index = np.cumsum(keep) - 1
    edges = keep[src] & keep[dst]
    sub = WindowGraph(
        window_id=graph.window_id,
        nodes=[n for n, k in zip(graph.nodes, keep, strict=True) if k],
        x=graph.x[keep],
        edge_index=np.vstack([new_index[src[edges]], new_index[dst[edges]]]).astype(np.int64),
        edge_attr=graph.edge_attr[edges],
        y=graph.y[keep],
    )
    return sub, int(new_index[graph.nodes.index(ip)])


_FLOWS = EDGE_FEATURES.index("log_flows")
_BYTES = EDGE_FEATURES.index("log_bytes")
_PERIODICITY = EDGE_FEATURES.index("periodicity")


def graph_snapshot(
    graph: WindowGraph,
    scores: np.ndarray,
    threshold: float,
    focus: int = 60,
    max_nodes: int = 300,
    max_edges: int = 800,
) -> dict[str, Any]:
    """A compact, drawable view of one window for the live map.

    A busy window has thousands of hosts; the map shows the ``focus`` riskiest internal hosts
    (every flagged host first) and their busiest flows, capped at ``max_nodes`` / ``max_edges``.
    """
    internal = graph.x[:, _INTERNAL] == 1.0
    candidates = np.flatnonzero(internal)
    ranked = candidates[np.argsort(-scores[candidates], kind="stable")][:focus]
    in_focus = np.zeros(graph.num_nodes, dtype=bool)
    in_focus[ranked] = True

    src, dst = graph.edge_index
    touching = np.flatnonzero(in_focus[src] | in_focus[dst])
    order = touching[np.argsort(-graph.edge_attr[touching, _FLOWS], kind="stable")]
    keep_nodes = in_focus.copy()
    edges: list[int] = []
    for e in order:
        if len(edges) >= max_edges:
            break
        new = int(not keep_nodes[src[e]]) + int(not keep_nodes[dst[e]])
        if keep_nodes.sum() + new > max_nodes:
            continue
        keep_nodes[src[e]] = keep_nodes[dst[e]] = True
        edges.append(int(e))

    degree = np.bincount(np.concatenate([src, dst]), minlength=graph.num_nodes)
    return {
        "nodes": [
            {
                "id": graph.nodes[i],
                "score": round(float(scores[i]), 4),
                "internal": bool(internal[i]),
                "flagged": bool(internal[i] and scores[i] >= threshold),
                "degree": int(degree[i]),
            }
            for i in np.flatnonzero(keep_nodes)
        ],
        "edges": [
            {
                "source": graph.nodes[int(src[e])],
                "target": graph.nodes[int(dst[e])],
                "flows": round(float(np.expm1(graph.edge_attr[e, _FLOWS]))),
                "bytes": round(float(np.expm1(graph.edge_attr[e, _BYTES]))),
                "periodicity": round(float(graph.edge_attr[e, _PERIODICITY]), 3),
            }
            for e in edges
        ],
        "total_nodes": graph.num_nodes,
        "total_edges": graph.num_edges,
    }


class Detector:
    def __init__(
        self,
        bundle_dir: Path,
        config: GraphConfig,
        spec: WindowSpec,
        min_flows: int = 1,
        allowed_lateness_s: float = 30.0,
    ) -> None:
        self.metadata = load_metadata(bundle_dir)  # refuses a mismatched feature layout
        self.model, self.scaler = load_bundle(bundle_dir)
        self.threshold = float(self.metadata["threshold"])
        self.config = config
        self.spec = spec
        self.min_flows = min_flows
        self.allowed_lateness_s = allowed_lateness_s
        self._windowers: dict[str, StreamingWindower] = {}
        # Recent graphs per sensor, so an alert raised on a window can still be explained.
        self._recent: dict[str, OrderedDict[str, tuple[WindowGraph, Data]]] = {}
        self.stats = DetectorStats()

    def _windower(self, sensor: str) -> StreamingWindower:
        if sensor not in self._windowers:
            self._windowers[sensor] = StreamingWindower(
                self.spec, self.allowed_lateness_s, self.min_flows
            )
        return self._windowers[sensor]

    def late_flows(self) -> int:
        return sum(w.late_flows for w in self._windowers.values())

    @torch.no_grad()
    def score_window(self, sensor: str, window: Window) -> dict[str, Any]:
        started = time.perf_counter()
        graph = build_window_graph(window.flows, window.window_id, self.config)
        data = to_data(graph, self.scaler)
        recent = self._recent.setdefault(sensor, OrderedDict())
        recent[window.window_id] = (graph, data)
        while len(recent) > RECENT_GRAPHS:
            recent.popitem(last=False)
        scores = torch.sigmoid(self.model(data.x, data.edge_index, data.edge_attr)).numpy()
        internal = graph.x[:, _INTERNAL] == 1.0
        hosts = [
            {"ip": ip, "score": round(float(s), 6)}
            for ip, s, keep in zip(graph.nodes, scores, internal, strict=True)
            if keep
        ]
        latency_ms = 1000 * (time.perf_counter() - started)
        self.stats.windows += 1
        self.stats.host_scores += len(hosts)
        self.stats.latencies_ms.append(latency_ms)
        return {
            "sensor_id": sensor,
            "window_id": window.window_id,
            "window_start": window.start,
            "window_end": window.end,
            "n_flows": len(window.flows),
            "n_nodes": graph.num_nodes,
            "n_edges": graph.num_edges,
            "latency_ms": round(latency_ms, 2),
            "hosts": hosts,
            "graph": graph_snapshot(graph, scores, self.threshold),
        }

    def explain(
        self, sensor: str, window_id: str, ip: str, steps: int = 32, top_k: int = 5
    ) -> dict[str, Any] | None:
        """Integrated Gradients: the flows and features behind ``ip``'s score in that window."""
        cached = self._recent.get(sensor, {}).get(window_id)
        if cached is None or ip not in cached[0].nodes:
            return None
        # An L-layer GNN's output for a host depends only on its L-hop neighbourhood, so
        # explaining on that ego graph is exact and far cheaper than on the whole window.
        ego, node = ego_graph(cached[0], ip, hops=self.model.cfg.layers)
        return explain_node(self.model, ego, to_data(ego, self.scaler), node, steps, top_k)

    def process(self, sensor: str, flows: pd.DataFrame) -> Iterator[dict[str, Any]]:
        """Yield one detection per window closed by these flows.

        Lazy on purpose: each detection is handled (alerts, explanations) before the next
        window is scored, so the window graph an alert needs is still in the recent cache.
        """
        self.stats.flows += len(flows)
        for window in self._windower(sensor).add(flows):
            yield self.score_window(sensor, window)

    def flush(self) -> Iterator[dict[str, Any]]:
        for sensor, windower in self._windowers.items():
            for window in windower.flush():
                yield self.score_window(sensor, window)
