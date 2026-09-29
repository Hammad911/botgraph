"""The detector service: one step of the live pipeline for the sensors this instance owns.

    flows.normalized -> Detector (windows, graph, scores) -> detections
                     -> AlertEngine (learning mode, warning/alert/cleared) -> alerts
                     -> GNNExplainer on alert-level events -> stored with the alert

Alert state is per sensor and the detector already owns each sensor's stream (Kafka partition
key), so both run in the same process; the recent window graphs needed for explanations are
available without shipping graphs over the bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from botgraph_core import validate_frame
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import ALERTS, DETECTIONS, FLOWS, Bus
from botgraph_stream.detector import Detector
from botgraph_stream.store import Store


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
        explain_epochs: int = 50,
    ) -> None:
        self.bus = bus
        self.detector = detector
        self.engine = engine
        self.store = store
        self.explain_epochs = explain_epochs
        self.stats = ServiceStats()

    def _handle(self, detection: dict[str, Any]) -> None:
        sensor = str(detection["sensor_id"])
        self.bus.publish(DETECTIONS, sensor, detection)
        if self.store is not None:
            self.store.record_window(detection)
        for event in self.engine.process(detection):
            if event["type"] == "alert" and self.explain_epochs > 0:
                event["explanation"] = self.detector.explain(
                    sensor, event["window_id"], event["ip"], self.explain_epochs
                )
            self.stats.events[event["type"]] = self.stats.events.get(event["type"], 0) + 1
            if event["type"] in ("warning", "alert"):
                self.stats.last_alert = event
            if self.store is not None:
                self.store.record_event(event)
            self.bus.publish(ALERTS, sensor, event)

    def step(self, max_messages: int = 100) -> int:
        messages = self.bus.poll(FLOWS, max_messages)
        by_sensor: dict[str, list[dict[str, Any]]] = {}
        for msg in messages:
            sensor = str(msg.value.get("sensor_id") or msg.key)
            by_sensor.setdefault(sensor, []).extend(msg.value.get("flows", []))
        for sensor, rows in by_sensor.items():
            for detection in self.detector.process(sensor, validate_frame(pd.DataFrame(rows))):
                self._handle(detection)
        return len(messages)

    def flush(self) -> None:
        for detection in self.detector.flush():
            self._handle(detection)
