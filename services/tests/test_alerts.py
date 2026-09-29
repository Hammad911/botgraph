from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from stream_fixtures import INTERNAL, synthetic_flows

from botgraph_core import GraphConfig, WindowSpec
from botgraph_ml.metrics import AlertRule, host_alerts
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import ALERTS, FLOWS, LocalBus
from botgraph_stream.detector import Detector
from botgraph_stream.service import DetectorService
from botgraph_stream.store import Store


def _detections(rng: np.random.Generator, windows: int = 120) -> list[dict]:  # type: ignore[type-arg]
    """Hosts appear in random windows with bursty scores (like real traffic)."""
    out = []
    hosts = [f"10.0.0.{i}" for i in range(8)]
    burst = {h: rng.uniform(0.2, 0.8) for h in hosts}
    for w in range(windows):
        present = [h for h in hosts if rng.random() < 0.8]
        out.append(
            {
                "sensor_id": "s",
                "window_id": f"w{w}",
                "window_start": 60.0 * w,
                "hosts": [
                    {"ip": h, "score": float(rng.random() < burst[h]) * rng.uniform(0.5, 1.0)}
                    for h in present
                ],
            }
        )
    return out


def _first(events: list[dict], kind: str) -> dict[str, float]:  # type: ignore[type-arg]
    first: dict[str, float] = {}
    for e in events:
        if e["type"] == kind:
            first.setdefault(e["ip"], e["window_start"])
    return first


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize(
    ("name", "rule"), [("warning", AlertRule(3, 5)), ("alert", AlertRule(12, 15))]
)
def test_first_alert_times_match_offline_evaluation(seed: int, name: str, rule: AlertRule) -> None:
    detections = _detections(np.random.default_rng(seed))
    engine = AlertEngine(model_threshold=0.5, levels={name: rule})
    events = [e for d in detections for e in engine.process(d)]

    rows = pd.DataFrame(
        [
            {"ip": h["ip"], "y": 0, "score": h["score"], "window_start": d["window_start"]}
            for d in detections
            for h in d["hosts"]
        ]
    )
    offline = host_alerts(rows, threshold=0.5, rule=rule)
    expected = {r.ip: r.first_seen + r.time_to_alert_s for r in offline.itertuples() if r.alerted}
    assert _first(events, name) == expected


def _stream(scores: list[float], ip: str = "10.0.0.1") -> list[dict]:  # type: ignore[type-arg]
    return [
        {
            "sensor_id": "s",
            "window_id": f"w{i}",
            "window_start": 60.0 * i,
            "hosts": [{"ip": ip, "score": s}],
        }
        for i, s in enumerate(scores)
    ]


def test_escalation_then_clear_then_rewarn() -> None:
    engine = AlertEngine(model_threshold=0.5)
    scores = [0.9] * 14 + [0.1] * 15 + [0.9] * 3
    kinds = [(e["type"], e["window_start"]) for d in _stream(scores) for e in engine.process(d)]
    assert kinds == [
        ("calibrated", 0.0),
        ("warning", 60.0 * 2),  # 3rd flagged window
        ("alert", 60.0 * 11),  # 12th flagged window
        ("cleared", 60.0 * 28),  # 15th clean window after the last flag
        ("warning", 60.0 * 31),  # 3 flagged windows again
    ]


def test_learning_mode_sets_threshold_from_baseline_and_stays_silent() -> None:
    engine = AlertEngine(model_threshold=0.5, learning_s=600.0, baseline_quantile=0.95)
    rng = np.random.default_rng(0)
    learning = [float(x) for x in rng.uniform(0.6, 0.9, size=10)]  # noisy but benign network
    events = [e for d in _stream(learning + [0.85] * 20) for e in engine.process(d)]

    calibrated = [e for e in events if e["type"] == "calibrated"]
    assert len(calibrated) == 1 and calibrated[0]["window_start"] == 600.0
    assert calibrated[0]["threshold"] == pytest.approx(np.quantile(learning, 0.95))
    assert calibrated[0]["baseline_scores"] == 10
    # 0.85 is below the calibrated threshold (~0.89), so the noisy host never alerts.
    assert [e["type"] for e in events] == ["calibrated"]


def test_model_threshold_is_a_floor() -> None:
    engine = AlertEngine(model_threshold=0.95, learning_s=120.0)
    events = [e for d in _stream([0.1, 0.1, 0.2]) for e in engine.process(d)]
    assert events[0]["type"] == "calibrated" and events[0]["threshold"] == 0.95


def test_store_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = Store(f"sqlite:///{tmp_path / 'b.db'}")
    engine = AlertEngine(model_threshold=0.5, learning_s=60.0)
    for d in _stream([0.2] + [0.9] * 14 + [0.1] * 15):
        store.record_window(
            {
                **d,
                "window_end": d["window_start"] + 300,
                "n_flows": 1,
                "n_nodes": 2,
                "n_edges": 1,
                "latency_ms": 1.0,
            }
        )
        for e in engine.process(d):
            store.record_event(e)

    assert store.thresholds() == {"s": pytest.approx(0.5)}
    rows = store.alerts()
    assert [r.level for r in rows] == ["warning", "alert"]
    assert all(r.cleared_window_start is not None for r in rows)
    assert store.summary() == {"sensors": 1, "windows": 30, "warnings": 1, "alerts": 1}
    assert store.recalibrate("s") and store.thresholds() == {}


def test_service_explains_alerts(bundle_dir, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    flows = synthetic_flows(np.random.default_rng(3), minutes=25)
    bus = LocalBus()
    det = Detector(bundle_dir, GraphConfig(internal_nets=INTERNAL), WindowSpec(300, 60), 1, 0.0)
    # threshold 0: everything is flagged, so alert-level events (and explanations) must appear
    service = DetectorService(
        bus,
        det,
        AlertEngine(model_threshold=0.0),
        Store(f"sqlite:///{tmp_path / 's.db'}"),
        explain_steps=5,
    )
    bus.publish(
        FLOWS, "lab", {"sensor_id": "lab", "flows": json.loads(flows.to_json(orient="records"))}
    )
    service.step()
    service.flush()

    alerts = [m.value for m in bus.poll(ALERTS) if m.value["type"] == "alert"]
    assert alerts, "expected alert-level events with a zero threshold"
    explanation = alerts[0]["explanation"]
    assert explanation["ip"] == alerts[0]["ip"] and explanation["top_features"]
    assert service.store is not None and service.store.alerts()[-1].explanation is not None
