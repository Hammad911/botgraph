"""Live drift monitoring: recent internal-host features and scores against two references.

For each sensor the monitor keeps the last ``recent_windows`` windows of active internal hosts'
features and scores. Every ``every`` windows it computes PSI against

* the model's ``training`` reference (optional; from the bundle's ``drift_reference.json``)
* the sensor's own ``baseline``: its first ``baseline_windows`` windows (the traffic its
  threshold was calibrated on), persisted in the store so a restart does not relearn it

and exports ``botgraph_feature_psi`` / ``botgraph_score_psi`` gauges plus a report the store
keeps for the console. A sensor put back into learning mode (``recalibrate``) also relearns its
drift baseline.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from prometheus_client import Gauge

from botgraph_ml.drift import PSI_SIGNIFICANT, Reference

FEATURE_PSI = Gauge(
    "botgraph_feature_psi",
    "Population stability index of an internal-host feature vs a reference",
    ["sensor", "reference", "feature"],
)
SCORE_PSI = Gauge(
    "botgraph_score_psi", "PSI of internal-host scores vs a reference", ["sensor", "reference"]
)

log = logging.getLogger("botgraph.drift")


@dataclass
class _Rows:
    """A sliding run of windows' host rows, with a running row count."""

    windows: deque[tuple[np.ndarray, np.ndarray]] = field(default_factory=deque)
    rows: int = 0

    def append(self, features: np.ndarray, scores: np.ndarray) -> None:
        self.windows.append((features, scores))
        self.rows += len(features)

    def popleft(self) -> None:
        features, _ = self.windows.popleft()
        self.rows -= len(features)

    def trim(self, min_windows: int, min_rows: int, max_windows: int) -> None:
        """Drop the oldest windows beyond ``min_windows``, but keep at least ``min_rows`` rows
        (small networks need more windows for a meaningful sample), never beyond
        ``max_windows``."""
        while len(self.windows) > min_windows and (
            len(self.windows) > max_windows or self.rows - len(self.windows[0][0]) >= min_rows
        ):
            self.popleft()

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.concatenate([f for f, _ in self.windows]),
            np.concatenate([s for _, s in self.windows]),
        )


@dataclass
class _SensorDrift:
    baseline: Reference | None = None
    pending: _Rows = field(default_factory=_Rows)
    recent: _Rows = field(default_factory=_Rows)
    since_report: int = 0
    significant: bool = False


class DriftMonitor:
    """PSI needs a real sample: each side must hold at least ``min_samples`` host rows.

    Windows overlap (5-minute windows every minute), so 60 windows of a single IoT device are
    only ~12 independent looks at it, and decile PSI on so little data is noise (it reported
    PSI 3.1 on a benign IoT honeypot that did not change). The baseline and the recent sample
    therefore grow past their window counts until they hold ``min_samples`` rows, up to
    ``max_windows``; until then nothing is reported.
    """

    def __init__(
        self,
        training: Reference | None = None,
        baselines: dict[str, Reference] | None = None,
        baseline_windows: int = 60,
        recent_windows: int = 60,
        every: int = 10,
        min_samples: int = 1000,
        max_windows: int = 1440,
    ) -> None:
        self.training = training
        self.baseline_windows = baseline_windows
        self.recent_windows = recent_windows
        self.every = every
        self.min_samples = min_samples
        self.max_windows = max_windows
        self._sensors: dict[str, _SensorDrift] = {
            s: _SensorDrift(baseline=ref) for s, ref in (baselines or {}).items()
        }
        self.new_baselines: dict[str, Reference] = {}  # built since the last drain, to persist

    def observe(
        self, sensor: str, window_start: float, features: np.ndarray, scores: np.ndarray
    ) -> dict[str, Any] | None:
        """Add one window's active internal hosts; returns a drift report every ``every``
        windows once the recent sample is large enough."""
        state = self._sensors.setdefault(sensor, _SensorDrift())
        if state.baseline is None:
            pending = state.pending
            pending.append(features, scores)
            while len(pending.windows) > self.max_windows:  # too small a network: keep sliding
                pending.popleft()
            if len(pending.windows) >= self.baseline_windows and pending.rows >= self.min_samples:
                state.baseline = Reference.build(*pending.arrays())
                self.new_baselines[sensor] = state.baseline
                state.pending = _Rows()

        state.recent.append(features, scores)
        state.recent.trim(self.recent_windows, self.min_samples, self.max_windows)
        state.since_report += 1
        if state.since_report < self.every:
            return None
        state.since_report = 0
        if state.recent.rows < self.min_samples:
            return None
        feats, sc = state.recent.arrays()

        references = {"training": self.training, "baseline": state.baseline}
        report: dict[str, Any] = {
            "sensor_id": sensor,
            "window_start": window_start,
            "windows": len(state.recent.windows),
            "hosts": len(feats),
        }
        for name, ref in references.items():
            if ref is None:
                continue
            result = ref.compare(feats, sc)
            report[name] = result
            for feature, value in result["features"].items():
                FEATURE_PSI.labels(sensor, name, feature).set(value)
            SCORE_PSI.labels(sensor, name).set(result["score_psi"])

        if "baseline" in report:
            drifted = max(report["baseline"]["max_psi"], report["baseline"]["score_psi"])
            significant = drifted > PSI_SIGNIFICANT
            if significant and not state.significant:
                log.warning(
                    "significant drift since calibration",
                    extra={
                        "sensor_id": sensor,
                        "feature": report["baseline"]["max_feature"],
                        "psi": report["baseline"]["max_psi"],
                        "score_psi": report["baseline"]["score_psi"],
                    },
                )
            state.significant = significant
        return report

    def drain_new_baselines(self) -> dict[str, Reference]:
        new, self.new_baselines = self.new_baselines, {}
        return new
