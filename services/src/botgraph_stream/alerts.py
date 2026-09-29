"""Alert engine: per-host k-of-n rules at two levels, with per-sensor learning mode.

Consumes detections (one per closed window) and emits events:

* ``calibrated``  a sensor finished learning; its threshold is now fixed
* ``warning``     a host was flagged in >= k of its last n windows (default 3 of 5)
* ``alert``       a host was flagged in >= k of its last n windows (default 12 of 15);
                  supersedes an open warning
* ``cleared``     a host with an open warning/alert was unflagged for the longest rule's n
                  windows in a row

The k-of-n logic matches ``botgraph_ml.metrics.host_alerts`` (the offline evaluation) exactly:
a host's history is its own last n appearances, and a level first fires at the window where
its hit count reaches k.

**Learning mode.** A new sensor starts learning: for ``learning_s`` seconds of event time no
host state is updated and every internal host's score is kept as a baseline. Then

    threshold = max(model threshold, quantile(baseline, q))

which is the per-network calibration validated on IoT-23 (botgraph_ml.calibrate).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from botgraph_ml.metrics import AlertRule

DEFAULT_LEVELS: dict[str, AlertRule] = {
    "warning": AlertRule(k=3, n=5),
    "alert": AlertRule(k=12, n=15),
}
ORDER = ["warning", "alert"]  # ascending severity


@dataclass
class HostState:
    flags: deque[int]
    level: str | None = None
    clean_streak: int = 0


@dataclass
class SensorState:
    started_at: float | None = None
    threshold: float | None = None  # None while learning
    baseline: list[float] = field(default_factory=list)
    hosts: dict[str, HostState] = field(default_factory=dict)


class AlertEngine:
    def __init__(
        self,
        model_threshold: float,
        levels: dict[str, AlertRule] | None = None,
        learning_s: float = 0.0,
        baseline_quantile: float = 0.95,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        self.levels = levels or DEFAULT_LEVELS
        unknown = set(self.levels) - set(ORDER)
        if unknown:
            raise ValueError(f"unknown alert levels {sorted(unknown)}; use {ORDER}")
        self.model_threshold = model_threshold
        self.learning_s = learning_s
        self.baseline_quantile = baseline_quantile
        self.history = max(rule.n for rule in self.levels.values())
        # Sensors calibrated in an earlier run (from the store) skip learning.
        self.sensors: dict[str, SensorState] = {
            s: SensorState(threshold=t) for s, t in (thresholds or {}).items()
        }

    def threshold(self, sensor: str) -> float | None:
        state = self.sensors.get(sensor)
        return None if state is None else state.threshold

    def process(self, detection: dict[str, Any]) -> list[dict[str, Any]]:
        sensor = str(detection["sensor_id"])
        start = float(detection["window_start"])
        state = self.sensors.setdefault(sensor, SensorState())
        if state.started_at is None:
            state.started_at = start
        events: list[dict[str, Any]] = []

        if state.threshold is None:
            if start < state.started_at + self.learning_s:
                state.baseline.extend(h["score"] for h in detection["hosts"])
                return events
            events.append(self._calibrate(sensor, state, start))

        threshold = state.threshold
        assert threshold is not None
        for host in detection["hosts"]:
            events += self._update_host(sensor, state, host, threshold, detection)
        return events

    def _calibrate(self, sensor: str, state: SensorState, start: float) -> dict[str, Any]:
        baseline_threshold = (
            float(np.quantile(state.baseline, self.baseline_quantile)) if state.baseline else None
        )
        state.threshold = max(self.model_threshold, baseline_threshold or self.model_threshold)
        event = {
            "type": "calibrated",
            "sensor_id": sensor,
            "window_start": start,
            "threshold": state.threshold,
            "model_threshold": self.model_threshold,
            "baseline_threshold": baseline_threshold,
            "baseline_scores": len(state.baseline),
        }
        state.baseline = []
        return event

    def _update_host(
        self,
        sensor: str,
        state: SensorState,
        host: dict[str, Any],
        threshold: float,
        detection: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ip, score = str(host["ip"]), float(host["score"])
        hs = state.hosts.setdefault(ip, HostState(flags=deque(maxlen=self.history)))
        flagged = int(score >= threshold)
        hs.flags.append(flagged)
        hs.clean_streak = 0 if flagged else hs.clean_streak + 1

        base = {
            "sensor_id": sensor,
            "ip": ip,
            "window_id": detection["window_id"],
            "window_start": float(detection["window_start"]),
            "score": score,
            "threshold": threshold,
        }
        flags = list(hs.flags)
        current = ORDER.index(hs.level) if hs.level else -1
        for name in reversed(ORDER):  # most severe first
            rule = self.levels.get(name)
            if rule is None or ORDER.index(name) <= current:
                continue
            hits = sum(flags[-rule.n :])
            if hits >= rule.k:
                hs.level = name
                return [{**base, "type": name, "hits": hits, "rule": {"k": rule.k, "n": rule.n}}]

        if hs.level is not None and hs.clean_streak >= self.history:
            hs.level = None
            return [{**base, "type": "cleared", "clean_windows": hs.clean_streak}]
        return []
