from __future__ import annotations

import json

import numpy as np
import pytest
from prometheus_client import REGISTRY
from stream_fixtures import INTERNAL, synthetic_flows

from botgraph_core import GraphConfig, WindowSpec
from botgraph_ml.drift import DRIFT_FEATURES, Reference, psi
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import FLOWS, LocalBus
from botgraph_stream.detector import Detector
from botgraph_stream.drift import DriftMonitor
from botgraph_stream.service import DetectorService
from botgraph_stream.store import Store

F = len(DRIFT_FEATURES)


def _hosts(rng: np.random.Generator, n: int, shift: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    feats = rng.normal(0.0, 1.0, size=(n, F))
    feats[:, 0] += shift
    feats[:, 1] = 0.0  # a constant feature (e.g. frac_irc on a quiet network)
    scores = np.clip(rng.beta(1, 8, size=n) + (0.5 if shift else 0.0), 0, 1)
    return feats, scores


def test_psi_is_zero_for_the_same_distribution_and_grows_with_shift() -> None:
    rng = np.random.default_rng(0)
    ref = Reference.build(*_hosts(rng, 5000))
    same = ref.compare(*_hosts(rng, 5000))
    shifted = ref.compare(*_hosts(rng, 5000, shift=2.0))
    assert same["max_psi"] < 0.05 and same["score_psi"] < 0.05
    assert shifted["max_feature"] == DRIFT_FEATURES[0]
    assert shifted["max_psi"] > 1.0 and shifted["score_psi"] > 0.25
    assert shifted["features"][DRIFT_FEATURES[1]] == 0.0  # constant stays constant
    assert psi(np.array([0.5, 0.5]), np.array([1.0, 0.0])) > 0  # empty bins stay finite


def test_reference_round_trips_and_checks_layout() -> None:
    ref = Reference.build(*_hosts(np.random.default_rng(1), 100))
    back = Reference.from_dict(json.loads(json.dumps(ref.to_dict())))
    assert back.n_hosts == 100
    assert all(np.array_equal(a, b) for a, b in zip(ref.edges, back.edges, strict=True))
    with pytest.raises(ValueError, match="feature layout"):
        Reference.from_dict({**ref.to_dict(), "features": ["old"]})
    with pytest.raises(ValueError):
        Reference.build(np.empty((0, F)), np.empty(0))


def test_monitor_learns_a_baseline_then_reports_drift(caplog) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(2)
    training = Reference.build(*_hosts(rng, 2000))
    monitor = DriftMonitor(training, baseline_windows=5, recent_windows=5, every=5, min_samples=100)
    reports = [monitor.observe("net", float(i), *_hosts(rng, 200)) for i in range(5)]
    assert reports[:4] == [None] * 4
    first = reports[4]
    assert first is not None and "baseline" in first and "training" in first
    assert first["baseline"]["max_psi"] < 0.1
    assert set(monitor.drain_new_baselines()) == {"net"}
    assert monitor.drain_new_baselines() == {}

    with caplog.at_level("WARNING", logger="botgraph.drift"):
        for i in range(5, 10):
            report = monitor.observe("net", float(i), *_hosts(rng, 200, shift=3.0))
    assert report is not None and report["baseline"]["max_psi"] > 0.25
    assert [r.getMessage() for r in caplog.records] == ["significant drift since calibration"]
    assert REGISTRY.get_sample_value(
        "botgraph_feature_psi",
        {"sensor": "net", "reference": "baseline", "feature": DRIFT_FEATURES[0]},
    ) == pytest.approx(report["baseline"]["max_psi"])
    assert REGISTRY.get_sample_value(
        "botgraph_score_psi", {"sensor": "net", "reference": "training"}
    )


def test_monitor_uses_a_stored_baseline_immediately() -> None:
    rng = np.random.default_rng(3)
    stored = Reference.build(*_hosts(rng, 1000))
    monitor = DriftMonitor(
        None, {"net": stored}, baseline_windows=50, recent_windows=3, every=3, min_samples=100
    )
    for i in range(3):
        report = monitor.observe("net", float(i), *_hosts(rng, 100))
    assert report is not None and "baseline" in report and "training" not in report
    assert monitor.drain_new_baselines() == {}


def test_small_networks_wait_for_enough_samples() -> None:
    rng = np.random.default_rng(5)
    training = Reference.build(*_hosts(rng, 1000))

    def run(max_windows: int) -> tuple[DriftMonitor, list[dict | None]]:  # type: ignore[type-arg]
        monitor = DriftMonitor(
            training,
            baseline_windows=5,
            recent_windows=5,
            every=5,
            min_samples=40,
            max_windows=max_windows,
        )
        # One device: 1 host row per window, so 40 rows need 40 windows.
        return monitor, [monitor.observe("iot", float(i), *_hosts(rng, 1)) for i in range(60)]

    # Too small to ever reach the sample size within max_windows: silence, not noise.
    tiny, reports = run(max_windows=30)
    assert reports == [None] * 60 and tiny.drain_new_baselines() == {}

    _, reports = run(max_windows=50)
    assert all(r is None for r in reports[:39])
    first = reports[39]
    assert first is not None and first["windows"] == 40  # grew past recent_windows=5
    assert "baseline" in first and "training" in first
    assert all(r["windows"] <= 50 for r in reports[40:] if r is not None)

    busy = DriftMonitor(None, baseline_windows=5, recent_windows=5, every=5, min_samples=40)
    for i in range(5):
        report = busy.observe("lab", float(i), *_hosts(rng, 20))
    assert report is not None and report["windows"] == 5 and report["hosts"] == 100


def test_service_persists_baselines_and_reports(bundle_dir, tmp_path) -> None:  # type: ignore[no-untyped-def]
    flows = synthetic_flows(np.random.default_rng(4), minutes=20)
    store = Store(f"sqlite:///{tmp_path / 'drift.db'}")
    det = Detector(
        bundle_dir, GraphConfig(internal_nets=INTERNAL), WindowSpec(300, 60), allowed_lateness_s=0
    )
    monitor = DriftMonitor(None, baseline_windows=4, recent_windows=4, every=4, min_samples=1)
    service = DetectorService(
        LocalBus(), det, AlertEngine(det.threshold), store, explain_steps=0, drift=monitor
    )
    records = json.loads(flows.to_json(orient="records"))
    service.bus.publish(FLOWS, "lab", {"sensor_id": "lab", "flows": records})
    service.step()
    service.flush()

    assert set(store.drift_baselines()) == {"lab"}
    Reference.from_dict(store.drift_baselines()["lab"])  # a valid reference
    history = store.drift_reports("lab")
    assert len(history) >= 2 and history[0].baseline_psi is not None
    assert [r.window_start for r in history] == sorted(r.window_start for r in history)
    assert [r.sensor_id for r in store.latest_drift()] == ["lab"]
    assert store.latest_drift()[0].id == history[-1].id

    store.record_event(
        {
            "type": "calibrated",
            "sensor_id": "lab",
            "window_start": 0.0,
            "threshold": 0.5,
            "model_threshold": 0.5,
            "baseline_scores": 1,
        }
    )
    assert store.recalibrate("lab")
    assert store.drift_baselines() == {}  # relearned with the threshold
