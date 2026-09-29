"""Prometheus metrics for the live pipeline (scrape with --metrics-port on ingest/detect)."""

from __future__ import annotations

from prometheus_client import Counter, Histogram, start_http_server

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


def serve_metrics(port: int) -> None:
    """Expose /metrics on ``port`` from a background thread."""
    start_http_server(port)
