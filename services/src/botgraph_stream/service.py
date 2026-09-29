"""The detector service: one step of the live pipeline for the sensors this instance owns.

    flows.normalized -> Detector (windows, graph, scores) -> detections
                     -> AlertEngine (learning mode, warning/alert/cleared) -> alerts
                     -> Integrated Gradients on alert-level events -> stored with the alert

Alert state is per sensor and the detector already owns each sensor's stream (Kafka partition
key), so both run in the same process; the recent window graphs needed for explanations are
available without shipping graphs over the bus.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from botgraph_core import FLOW_COLUMNS, FLOW_DTYPES
from botgraph_ml.drift import active, internal_rows
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import ALERTS, DETECTIONS, FLOWS, Bus
from botgraph_stream.detector import Detector
from botgraph_stream.drift import DriftMonitor
from botgraph_stream.metrics import (
    ALERT_EVENTS,
    EXPLAIN_LATENCY,
    FLAGGED_HOSTS,
    HOST_SCORES,
    LAST_WINDOW_EVENT_TIME,
    LAST_WINDOW_PROCESSED,
    LATE_FLOWS,
    MODEL_INFO,
    SENSOR_LEARNING,
    SENSOR_THRESHOLD,
    STORE_WRITE_LATENCY,
    WINDOW_HOSTS,
    WINDOW_LATENCY,
    WINDOWS_SCORED,
)
from botgraph_stream.store import Store

log = logging.getLogger("botgraph.detector")


@dataclass
class ServiceStats:
    events: dict[str, int] = field(default_factory=dict)
    last_alert: dict[str, Any] | None = None


class DetectorService:
    def __init__(
        self,
        bus: Bus,
        detector: Detector,
        engine: AlertEngine,
        store: Store | None = None,
        explain_steps: int = 50,
        drift: DriftMonitor | None = None,
    ) -> None:
        self.bus = bus
        self.detector = detector
        self.engine = engine
        self.store = store
        self.explain_steps = explain_steps
        self.drift = drift
        self.stats = ServiceStats()
        self._late_seen: dict[str, int] = {}
        meta = detector.metadata
        MODEL_INFO.labels(
            str(meta.get("model_kind", "unknown")),
            str(meta.get("git_sha", "unknown")),
            str(meta.get("created_at", "unknown")),
        ).set(1)

    def _observe(self, sensor: str, detection: dict[str, Any]) -> None:
        WINDOWS_SCORED.labels(sensor).inc()
        WINDOW_LATENCY.observe(detection["latency_ms"] / 1000)
        LAST_WINDOW_PROCESSED.labels(sensor).set(time.time())
        LAST_WINDOW_EVENT_TIME.labels(sensor).set(float(detection["window_end"]))
        scores = [float(h["score"]) for h in detection["hosts"]]
        WINDOW_HOSTS.labels(sensor).set(len(scores))
        histogram = HOST_SCORES.labels(sensor)
        for score in scores:
            histogram.observe(score)
        threshold = self.engine.threshold(sensor)
        SENSOR_LEARNING.labels(sensor).set(int(threshold is None))
        if threshold is not None:
            SENSOR_THRESHOLD.labels(sensor).set(threshold)
            FLAGGED_HOSTS.labels(sensor).set(sum(s >= threshold for s in scores))
        late = self.detector.late_flows_for(sensor)
        if late > self._late_seen.get(sensor, 0):
            LATE_FLOWS.labels(sensor).inc(late - self._late_seen.get(sensor, 0))
            self._late_seen[sensor] = late

    def _handle(self, detection: dict[str, Any]) -> None:
        sensor = str(detection["sensor_id"])
        self.bus.publish(DETECTIONS, sensor, detection)
        if self.store is not None:
            with STORE_WRITE_LATENCY.labels("window").time():
                self.store.record_window(detection)
        for event in self.engine.process(detection):
            if event["type"] == "alert" and self.explain_steps > 0:
                with EXPLAIN_LATENCY.time():
                    event["explanation"] = self.detector.explain(
                        sensor, event["window_id"], event["ip"], self.explain_steps
                    )
            self.stats.events[event["type"]] = self.stats.events.get(event["type"], 0) + 1
            ALERT_EVENTS.labels(event["type"]).inc()
            if event["type"] in ("warning", "alert"):
                self.stats.last_alert = event
            self._log_event(event)
            if self.store is not None:
                with STORE_WRITE_LATENCY.labels("event").time():
                    self.store.record_event(event)
            self.bus.publish(ALERTS, sensor, event)
        # After the engine ran, so a sensor that just calibrated reports its new threshold.
        self._observe(sensor, detection)
        if self.drift is not None:
            self._drift(sensor, detection)

    def _drift(self, sensor: str, detection: dict[str, Any]) -> None:
        assert self.drift is not None
        graph = self.detector.window_graph(sensor, detection["window_id"])
        if graph is None:
            return
        # Detections list internal hosts in graph order, so rows and scores line up.
        scores = np.array([h["score"] for h in detection["hosts"]], dtype=float)
        rows = internal_rows(graph.x)
        keep = active(rows)
        report = self.drift.observe(
            sensor, float(detection["window_start"]), rows[keep], scores[keep]
        )
        if self.store is None:
            self.drift.drain_new_baselines()
            return
        for name, reference in self.drift.drain_new_baselines().items():
            self.store.save_drift_baseline(name, reference.to_dict())
        if report is not None:
            with STORE_WRITE_LATENCY.labels("drift").time():
                self.store.record_drift(report)

    @staticmethod
    def _log_event(event: dict[str, Any]) -> None:
        fields: dict[str, Any] = {
            k: event[k]
            for k in ("sensor_id", "ip", "window_id", "score", "threshold", "hits")
            if k in event
        }
        if event["type"] == "calibrated":
            fields["baseline_scores"] = event["baseline_scores"]
        level = logging.WARNING if event["type"] in ("warning", "alert") else logging.INFO
        log.log(level, event["type"], extra={"event": event["type"], **fields})

    def step(self, max_messages: int = 100, timeout: float = 0.0) -> int:
        messages = self.bus.poll(FLOWS, max_messages, timeout)
        by_sensor: dict[str, list[dict[str, Any]]] = {}
        for msg in messages:
            sensor = str(msg.value.get("sensor_id") or msg.key)
            by_sensor.setdefault(sensor, []).extend(msg.value.get("flows", []))
        for sensor, rows in by_sensor.items():
            # Rows on flows.normalized were validated by ingest; only restore column dtypes.
            frame = pd.DataFrame(rows, columns=FLOW_COLUMNS).astype(FLOW_DTYPES)
            for detection in self.detector.process(sensor, frame):
                self._handle(detection)
        return len(messages)

    def flush(self) -> None:
        for detection in self.detector.flush():
            self._handle(detection)
