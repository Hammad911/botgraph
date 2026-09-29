from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request

import numpy as np
from prometheus_client import REGISTRY
from stream_fixtures import INTERNAL, synthetic_flows

from botgraph_core import GraphConfig, WindowSpec
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import FLOWS, LocalBus
from botgraph_stream.detector import Detector
from botgraph_stream.logs import JsonFormatter
from botgraph_stream.metrics import Health, serve_metrics
from botgraph_stream.service import DetectorService

SPEC = WindowSpec(size_s=300, hop_s=60)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_probe_server_reports_liveness_readiness_and_metrics() -> None:
    port = _free_port()
    health = serve_metrics(port, Health(stale_after_s=60), host="127.0.0.1")
    base = f"http://127.0.0.1:{port}"

    assert _get(f"{base}/healthz")[0] == 200
    assert _get(f"{base}/readyz")[0] == 503  # not ready until the service loop starts
    health.set_ready()
    assert _get(f"{base}/readyz")[0] == 200
    code, body = _get(f"{base}/metrics")
    assert code == 200 and "botgraph_windows_scored_total" in body
    assert _get(f"{base}/nope")[0] == 404

    health.stale_after_s = -1.0  # a loop that stopped beating
    assert _get(f"{base}/healthz")[0] == 503
    assert _get(f"{base}/readyz")[0] == 503


def test_json_logs_carry_extra_fields() -> None:
    record = logging.makeLogRecord(
        {
            "name": "botgraph.detector",
            "levelno": logging.WARNING,
            "levelname": "WARNING",
            "msg": "alert",
            "sensor_id": "lab",
            "ip": "10.0.0.66",
            "score": 0.99,
        }
    )
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "alert" and entry["level"] == "warning"
    assert (entry["sensor_id"], entry["ip"], entry["score"]) == ("lab", "10.0.0.66", 0.99)
    assert "args" not in entry and "levelno" not in entry


def test_detector_service_exports_sensor_gauges(bundle_dir, caplog) -> None:  # type: ignore[no-untyped-def]
    flows = synthetic_flows(np.random.default_rng(3), minutes=12)
    sensor = "obs-lab"
    det = Detector(bundle_dir, GraphConfig(internal_nets=INTERNAL), SPEC, allowed_lateness_s=0.0)
    # Threshold 0: every host is flagged, so warnings fire and get logged.
    service = DetectorService(LocalBus(), det, AlertEngine(0.0), explain_steps=0)
    records = json.loads(flows.to_json(orient="records"))
    service.bus.publish(FLOWS, sensor, {"sensor_id": sensor, "flows": records})
    with caplog.at_level(logging.INFO, logger="botgraph"):
        service.step()
        service.flush()

    sample = REGISTRY.get_sample_value
    assert sample("botgraph_sensor_learning", {"sensor": sensor}) == 0
    assert sample("botgraph_sensor_threshold", {"sensor": sensor}) == 0.0
    hosts = sample("botgraph_window_hosts", {"sensor": sensor})
    assert hosts and sample("botgraph_flagged_hosts", {"sensor": sensor}) == hosts
    assert sample("botgraph_host_score_count", {"sensor": sensor}) >= hosts
    assert sample("botgraph_last_window_processed_timestamp_seconds", {"sensor": sensor}) > 0
    assert (
        sample(
            "botgraph_model_info",
            {
                "model": "gatv2",
                "git_sha": det.metadata["git_sha"],
                "created_at": det.metadata["created_at"],
            },
        )
        == 1
    )
    warnings = [r for r in caplog.records if r.getMessage() == "warning"]
    assert warnings and vars(warnings[0])["sensor_id"] == sensor
