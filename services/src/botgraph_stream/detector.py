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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import torch

from botgraph_core import (
    NODE_FEATURES,
    GraphConfig,
    Window,
    WindowSpec,
    build_window_graph,
    validate_frame,
)
from botgraph_ml.gnn.data import to_data
from botgraph_ml.gnn.models import load_bundle, load_metadata
from botgraph_stream.bus import DETECTIONS, FLOWS, Bus
from botgraph_stream.windower import StreamingWindower

_INTERNAL = NODE_FEATURES.index("is_internal")


@dataclass
class DetectorStats:
    flows: int = 0
    windows: int = 0
    host_scores: int = 0
    latencies_ms: list[float] = field(default_factory=list)


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
        }

    def process(self, sensor: str, flows: pd.DataFrame) -> list[dict[str, Any]]:
        self.stats.flows += len(flows)
        return [self.score_window(sensor, w) for w in self._windower(sensor).add(flows)]

    def flush(self) -> list[dict[str, Any]]:
        return [
            self.score_window(sensor, w)
            for sensor, windower in self._windowers.items()
            for w in windower.flush()
        ]


def detector_step(bus: Bus, detector: Detector, max_messages: int = 100) -> int:
    """Consume pending normalised flow batches once; publish a detection per closed window."""
    messages = bus.poll(FLOWS, max_messages)
    by_sensor: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        sensor = str(msg.value.get("sensor_id") or msg.key)
        by_sensor.setdefault(sensor, []).extend(msg.value.get("flows", []))
    for sensor, rows in by_sensor.items():
        frame = validate_frame(pd.DataFrame(rows))
        for detection in detector.process(sensor, frame):
            bus.publish(DETECTIONS, sensor, detection)
    return len(messages)


def detector_flush(bus: Bus, detector: Detector) -> None:
    for detection in detector.flush():
        bus.publish(DETECTIONS, str(detection["sensor_id"]), detection)
