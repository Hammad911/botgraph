"""Prometheus metrics and health probes for the live pipeline.

Each long-running service (ingest, detect, the API) serves one small HTTP endpoint on
``--metrics-port``:

    /metrics   Prometheus exposition format
    /healthz   liveness: the service loop ran within ``stale_after_s`` (a hung loop fails it)
    /readyz    readiness: the service finished starting (model loaded, broker and store reached)

Kubernetes probes and Prometheus both use this port, so the public API never exposes metrics.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

FLOWS_INGESTED = Counter(
    "botgraph_flows_ingested_total", "Flows seen by ingest, by validation result", ["result"]
)
WINDOWS_SCORED = Counter("botgraph_windows_scored_total", "Windows scored", ["sensor"])
WINDOW_LATENCY = Histogram(
    "botgraph_window_latency_seconds",
    "Graph build + GNN scoring time per window",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
ALERT_EVENTS = Counter(
    "botgraph_alert_events_total",
    "Alert engine events (calibrated/warning/alert/cleared)",
    ["type"],
)
LATE_FLOWS = Counter(
    "botgraph_late_flows_total", "Flows dropped for arriving after their window closed", ["sensor"]
)
EXPLAIN_LATENCY = Histogram(
    "botgraph_explain_latency_seconds",
    "Integrated Gradients time per alert",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
STORE_WRITE_LATENCY = Histogram(
    "botgraph_store_write_seconds",
    "Store write time per window or event",
    ["op"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1.0),
)
HOST_SCORES = Histogram(
    "botgraph_host_score",
    "Internal-host scores per window (the score distribution drifts before alerts do)",
    ["sensor"],
    buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99),
)
WINDOW_HOSTS = Gauge("botgraph_window_hosts", "Internal hosts in the latest window", ["sensor"])
FLAGGED_HOSTS = Gauge(
    "botgraph_flagged_hosts",
    "Internal hosts at or above the threshold in the latest window",
    ["sensor"],
)
LAST_WINDOW_PROCESSED = Gauge(
    "botgraph_last_window_processed_timestamp_seconds",
    "Wall-clock time the sensor's latest window was scored (staleness alerts)",
    ["sensor"],
)
LAST_WINDOW_EVENT_TIME = Gauge(
    "botgraph_last_window_event_timestamp_seconds",
    "Event time (traffic time) of the end of the sensor's latest window",
    ["sensor"],
)
SENSOR_THRESHOLD = Gauge(
    "botgraph_sensor_threshold", "Calibrated alert threshold (absent while learning)", ["sensor"]
)
SENSOR_LEARNING = Gauge(
    "botgraph_sensor_learning", "1 while the sensor is in learning mode", ["sensor"]
)
MODEL_INFO = Gauge(
    "botgraph_model_info",
    "The deployed model bundle (value is always 1)",
    ["model", "git_sha", "created_at"],
)
CONSUMER_LAG = Gauge(
    "botgraph_consumer_lag_messages", "Messages not yet consumed, per topic", ["topic"]
)


class Health:
    """Liveness and readiness state shared between a service loop and the probe server."""

    def __init__(self, stale_after_s: float = 120.0) -> None:
        self.stale_after_s = stale_after_s
        self._beat = time.monotonic()
        self._ready = False

    def beat(self) -> None:
        self._beat = time.monotonic()

    def set_ready(self, ready: bool = True) -> None:
        self._ready = ready
        self.beat()

    @property
    def alive(self) -> bool:
        return time.monotonic() - self._beat <= self.stale_after_s

    @property
    def ready(self) -> bool:
        return self._ready and self.alive


def _handler(health: Health) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/metrics":
                self._send(200, generate_latest(REGISTRY), CONTENT_TYPE_LATEST)
            elif path == "/healthz":
                ok = health.alive
                self._send(200 if ok else 503, b"ok\n" if ok else b"stale\n")
            elif path == "/readyz":
                ok = health.ready
                self._send(200 if ok else 503, b"ready\n" if ok else b"not ready\n")
            else:
                self._send(404, b"not found\n")

        def _send(self, code: int, body: bytes, content_type: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:  # probes every few seconds: keep logs clean
            return

    return Handler


def serve_metrics(
    port: int,
    health: Health | None = None,
    host: str = "0.0.0.0",  # noqa: S104 (containers: scraped from outside the pod)
) -> Health:
    """Serve /metrics, /healthz and /readyz on ``port`` from a daemon thread."""
    health = health or Health()
    server = ThreadingHTTPServer((host, port), _handler(health))
    threading.Thread(target=server.serve_forever, name="metrics", daemon=True).start()
    return health
