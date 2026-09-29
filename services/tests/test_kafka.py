"""End-to-end over a real broker (and Postgres when BOTGRAPH_TEST_DB_URL is set).

Runs in CI against Redpanda + Postgres; skipped locally unless BOTGRAPH_KAFKA_BOOTSTRAP is set:
    BOTGRAPH_KAFKA_BOOTSTRAP=localhost:19092 uv run pytest -m kafka services
"""

from __future__ import annotations

import json
import os
import time
import uuid

import numpy as np
import pytest
from stream_fixtures import INTERNAL, synthetic_flows

from botgraph_core import GraphConfig, WindowSpec, sliding_windows
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import DETECTIONS, FLOWS_RAW, KafkaBus
from botgraph_stream.detector import Detector
from botgraph_stream.ingest import IngestStats, ingest_step
from botgraph_stream.service import DetectorService
from botgraph_stream.store import Store

BOOTSTRAP = os.environ.get("BOTGRAPH_KAFKA_BOOTSTRAP")
pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(not BOOTSTRAP, reason="set BOTGRAPH_KAFKA_BOOTSTRAP to run"),
]
SPEC = WindowSpec(size_s=300, hop_s=60)


def test_kafka_pipeline_end_to_end(bundle_dir, tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert BOOTSTRAP is not None
    prefix = f"t{uuid.uuid4().hex[:8]}."  # isolated topics per run
    sensor = f"lab-{prefix.rstrip('.')}"
    flows = synthetic_flows(np.random.default_rng(7), minutes=12)
    expected_windows = len(list(sliding_windows(flows, SPEC, 1)))

    source = KafkaBus(BOOTSTRAP, "source", prefix)
    source.ensure_topics(partitions=1)
    records = json.loads(flows.to_json(orient="records"))
    for i in range(0, len(records), 40):
        source.publish(FLOWS_RAW, sensor, {"sensor_id": sensor, "flows": records[i : i + 40]})
    source.flush()

    ingest_bus = KafkaBus(BOOTSTRAP, "ingest", prefix)
    detector_bus = KafkaBus(BOOTSTRAP, "detector", prefix)
    store = Store(os.environ.get("BOTGRAPH_TEST_DB_URL") or f"sqlite:///{tmp_path / 'k.db'}")
    detector = Detector(bundle_dir, GraphConfig(internal_nets=INTERNAL), SPEC, 1, 0.0)
    service = DetectorService(
        detector_bus, detector, AlertEngine(detector.threshold), store, explain_steps=0
    )

    stats, deadline = IngestStats(), time.monotonic() + 90
    while time.monotonic() < deadline:
        ingest_step(ingest_bus, stats, timeout=0.5)
        service.step(timeout=0.5)
        if stats.flows_ok == len(flows) and detector.stats.flows == len(flows):
            break
    assert stats.flows_ok == len(flows), f"ingest saw {stats.flows_ok}/{len(flows)} flows"
    assert detector.stats.flows == len(flows)
    service.flush()
    detector_bus.flush()

    check = KafkaBus(BOOTSTRAP, "check", prefix)
    detections: list[dict] = []  # type: ignore[type-arg]
    deadline = time.monotonic() + 60
    while len(detections) < expected_windows and time.monotonic() < deadline:
        detections += [m.value for m in check.poll(DETECTIONS, 1000, timeout=1.0)]
    assert len(detections) == expected_windows
    assert {d["sensor_id"] for d in detections} == {sensor}
    assert sorted(d["window_start"] for d in detections) == [
        w.start for w in sliding_windows(flows, SPEC, 1)
    ]
    assert store.summary()["windows"] >= expected_windows

    for bus in (source, ingest_bus, detector_bus, check):
        bus.close()
