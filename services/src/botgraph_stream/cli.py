"""``botgraph``: run and inspect the live pipeline.

botgraph run --replay ctu13:6              # all-in-one demo on a held-out botnet family
botgraph run --replay iot23:CTU-IoT-Malware-Capture-34-1 --speed 600
botgraph alerts [--open]                   # alerts in the store
botgraph status                            # sensors, thresholds, counts
botgraph recalibrate --sensor <id>         # back to learning mode on next start
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table

from botgraph_core import GraphConfig, Label
from botgraph_ml.config import load_params, repo_path, window_spec
from botgraph_ml.metrics import AlertRule
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import ALERTS, DETECTIONS, DLQ, FLOWS, FLOWS_RAW, LocalBus
from botgraph_stream.detector import Detector
from botgraph_stream.ingest import IngestStats, ingest_step
from botgraph_stream.replay import Replayer, ReplaySource, load_source
from botgraph_stream.service import DetectorService
from botgraph_stream.store import Store

console = Console()


def default_db_url() -> str:
    path = repo_path("data/botgraph.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- run


class RunView:
    """Live terminal view of an all-in-one run."""

    def __init__(self, source: ReplaySource, replayer: Replayer, service: DetectorService) -> None:
        self.source, self.replayer, self.service = source, replayer, service
        self.events: deque[dict[str, Any]] = deque(maxlen=8)
        self.started = time.monotonic()

    def render(self, ingest: IngestStats) -> Group:
        det = self.service.detector
        elapsed = max(time.monotonic() - self.started, 1e-6)
        total = len(self.source.flows)
        threshold = self.service.engine.threshold(self.source.sensor_id)
        counts = self.service.stats.events
        stats = Table.grid(padding=(0, 2))
        stats.add_column(style="bold")
        stats.add_column()
        stats.add_row("Sensor", self.source.sensor_id)
        stats.add_row("Event time", _utc(self.replayer.event_time))
        stats.add_row(
            "Flows",
            f"{self.replayer.published:,} / {total:,} ({self.replayer.published / total:.0%})",
        )
        stats.add_row(
            "Windows scored", f"{det.stats.windows:,}  ({det.stats.windows / elapsed:.1f}/s)"
        )
        stats.add_row(
            "Mode",
            "[yellow]learning[/]"
            if threshold is None
            else f"[green]active[/] (threshold {threshold:.4f})",
        )
        warnings, alerts = counts.get("warning", 0), counts.get("alert", 0)
        stats.add_row(
            "Events",
            f"[yellow]{warnings} warnings[/]  [red]{alerts} alerts[/]  "
            f"{counts.get('cleared', 0)} cleared",
        )
        stats.add_row("Rejected / late", f"{ingest.flows_rejected} / {det.late_flows()}")

        recent = Table(title="Recent events", expand=True)
        for col in ("Time", "Event", "Host", "Score", "Hits"):
            recent.add_column(col)
        for e in reversed(self.events):
            style = {"alert": "red", "warning": "yellow", "calibrated": "cyan"}.get(e["type"], "")
            hits = f"{e['hits']}/{e['rule']['n']}" if "hits" in e else ""
            recent.add_row(
                _utc(e["window_start"]),
                f"[{style}]{e['type']}[/]" if style else e["type"],
                e.get("ip", ""),
                f"{e['score']:.4f}" if "score" in e else f"{e.get('threshold', 0):.4f}",
                hits,
            )
        return Group(stats, recent)


def _summary(
    source: ReplaySource, service: DetectorService, events: list[dict[str, Any]], wall_s: float
) -> dict[str, Any]:
    sensor = source.sensor_id
    state = service.engine.sensors.get(sensor)
    seen = set(state.hosts) if state else set()
    bots = {ip for ip, lbl in source.truth.items() if lbl == Label.BOTNET and ip in seen}
    benign = {ip for ip, lbl in source.truth.items() if lbl == Label.BENIGN and ip in seen}
    first: dict[str, dict[str, dict[str, Any]]] = {"warning": {}, "alert": {}}
    for e in events:
        if e["type"] in first:
            first[e["type"]].setdefault(e["ip"], e)
    warned = set(first["warning"]) | set(first["alert"])
    alerted = set(first["alert"])
    minutes = [
        (e["window_start"] - e["first_seen"]) / 60 for ip, e in first["alert"].items() if ip in bots
    ]
    lat = service.detector.stats.latencies_ms
    flows = source.flows
    span = float(flows["ts"].iloc[-1] - flows["ts"].iloc[0]) if len(flows) else 0.0
    return {
        "sensor": sensor,
        "flows": len(flows),
        "windows": service.detector.stats.windows,
        "wall_seconds": round(wall_s, 1),
        "flows_per_second": round(len(flows) / max(wall_s, 1e-6)),
        "speed_vs_real_time": round(span / max(wall_s, 1e-6), 1),
        "window_latency_ms": {
            "p50": round(statistics.median(lat), 1) if lat else None,
            "p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        },
        "threshold": service.engine.threshold(sensor),
        "bots": {
            "seen": len(bots),
            "alerted": len(bots & alerted),
            "warned": len(bots & warned),
            "median_minutes_to_alert": round(statistics.median(minutes), 1) if minutes else None,
        },
        "benign": {
            "seen": len(benign),
            "alerted": len(benign & alerted),
            "warned": len(benign & warned),
        },
        "unlabelled_hosts_alerted": len(alerted - bots - benign),
    }


def cmd_run(args: argparse.Namespace) -> None:
    params = load_params()
    sp = params["stream"]
    source = load_source(args.replay, params)
    if args.learning_minutes is not None:
        learning_min = args.learning_minutes
    else:  # the model's home network was calibrated during training
        learning_min = 0 if source.dataset == "ctu13" else sp["learning_minutes"]

    db_url = args.db or default_db_url()
    if args.fresh and db_url.startswith("sqlite:///"):
        Path(db_url.removeprefix("sqlite:///")).unlink(missing_ok=True)
    store = Store(db_url)
    bus = LocalBus()
    detector = Detector(
        repo_path(params["data"]["models_dir"]) / (args.model or sp["model"]),
        GraphConfig(
            internal_nets=source.internal_nets,
            min_flows_for_periodicity=int(params["graph"]["min_flows_for_periodicity"]),
        ),
        window_spec(params),
        min_flows=int(sp["min_flows"]),
        allowed_lateness_s=float(sp["allowed_lateness_s"]),
    )
    engine = AlertEngine(
        detector.threshold,
        {name: AlertRule(**rule) for name, rule in sp["levels"].items()},
        learning_s=60.0 * learning_min,
        baseline_quantile=float(sp["baseline_quantile"]),
        thresholds=store.thresholds(),
    )
    epochs = sp["explain_epochs"] if args.explain_epochs is None else args.explain_epochs
    service = DetectorService(bus, detector, engine, store, explain_epochs=int(epochs))
    replayer = Replayer(bus, source, speed=args.speed, batch_flows=int(sp["batch_flows"]))
    view, ingest, events = RunView(source, replayer, service), IngestStats(), []

    console.print(
        f"[bold]BotGraph[/] replaying [cyan]{args.replay}[/] ({len(source.flows):,} flows, "
        f"labels hidden) | model {args.model or sp['model']} | learning {learning_min} min | "
        f"store {db_url}"
    )
    live = (
        Live(view.render(ingest), console=console, refresh_per_second=4) if not args.quiet else None
    )
    started = time.monotonic()
    with live or contextlib.nullcontext():
        while not replayer.done or bus.pending(FLOWS_RAW) or bus.pending(FLOWS):
            replayer.step()
            ingest_step(bus, ingest)
            service.step()
            for msg in bus.poll(ALERTS, 10_000):
                events.append(msg.value)
                view.events.append(msg.value)
            bus.poll(DETECTIONS, 10_000)  # nothing else consumes them locally
            bus.poll(DLQ, 10_000)
            if live is not None:
                live.update(view.render(ingest))
        service.flush()
        for msg in bus.poll(ALERTS, 10_000):
            events.append(msg.value)
            view.events.append(msg.value)
        if live is not None:
            live.update(view.render(ingest))

    summary = _summary(source, service, events, time.monotonic() - started)
    _print_summary(summary)
    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary, indent=2))


def _print_summary(s: dict[str, Any]) -> None:
    t = Table(title=f"Replay summary: {s['sensor']}", show_header=False)
    t.add_column(style="bold")
    t.add_column()
    b, g = s["bots"], s["benign"]
    ttd = "n/a" if b["median_minutes_to_alert"] is None else f"{b['median_minutes_to_alert']} min"
    t.add_row(
        "Infected hosts alerted", f"{b['alerted']}/{b['seen']} (warned {b['warned']}/{b['seen']})"
    )
    t.add_row(
        "Normal hosts alerted", f"{g['alerted']}/{g['seen']} (warned {g['warned']}/{g['seen']})"
    )
    t.add_row("Unlabelled hosts alerted", str(s["unlabelled_hosts_alerted"]))
    t.add_row("Median time to alert", ttd)
    t.add_row("Threshold", "n/a" if s["threshold"] is None else f"{s['threshold']:.4f}")
    t.add_row(
        "Throughput",
        f"{s['flows_per_second']:,} flows/s, {s['speed_vs_real_time']}x real time "
        f"({s['windows']:,} windows in {s['wall_seconds']} s)",
    )
    lat = s["window_latency_ms"]
    t.add_row("Window latency", f"p50 {lat['p50']} ms, p95 {lat['p95']} ms")
    console.print(t)


# --------------------------------------------------------------------------- inspect


def cmd_alerts(args: argparse.Namespace) -> None:
    rows = Store(args.db or default_db_url()).alerts(open_only=args.open)
    t = Table(title="Alerts" + (" (open)" if args.open else ""))
    for col in (
        "Sensor",
        "Level",
        "Host",
        "Raised (event time)",
        "Score",
        "Hits",
        "Cleared",
        "Why",
    ):
        t.add_column(col)
    for r in rows:
        why = ""
        if r.explanation and r.explanation.get("top_features"):
            why = ", ".join(f["feature"] for f in r.explanation["top_features"][:3])
        t.add_row(
            r.sensor_id,
            r.level,
            r.ip,
            _utc(r.window_start),
            f"{r.score:.4f}",
            f"{r.hits}/{r.rule_n}",
            "" if r.cleared_window_start is None else _utc(r.cleared_window_start),
            why,
        )
    console.print(t)


def cmd_status(args: argparse.Namespace) -> None:
    store = Store(args.db or default_db_url())
    console.print(store.summary())
    for sensor, threshold in store.thresholds().items():
        console.print(f"  {sensor}: active, threshold {threshold:.4f}")


def cmd_recalibrate(args: argparse.Namespace) -> None:
    ok = Store(args.db or default_db_url()).recalibrate(args.sensor)
    console.print("back to learning mode on next start" if ok else f"unknown sensor {args.sensor}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="botgraph", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--db", help="SQLAlchemy URL (default: sqlite:///data/botgraph.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="all-in-one pipeline on a replayed capture")
    run.add_argument("--replay", required=True, help="ctu13:<scenario> or iot23:<capture>")
    run.add_argument(
        "--speed", type=float, default=0.0, help="x real time (0 = as fast as possible)"
    )
    run.add_argument("--model", help="bundle name under ml/models (default: stream.model)")
    run.add_argument(
        "--learning-minutes", type=float, help="0 on CTU-13, stream.learning_minutes otherwise"
    )
    run.add_argument("--explain-epochs", type=int, help="GNNExplainer epochs per alert (0 = off)")
    run.add_argument("--fresh", action="store_true", help="start from an empty SQLite store")
    run.add_argument("--quiet", action="store_true", help="no live view, summary only")
    run.add_argument("--summary-json", help="also write the summary to this file")
    run.set_defaults(func=cmd_run)

    alerts = sub.add_parser("alerts", help="list alerts")
    alerts.add_argument("--open", action="store_true", help="only alerts not yet cleared")
    alerts.set_defaults(func=cmd_alerts)
    sub.add_parser("status", help="store summary").set_defaults(func=cmd_status)
    recal = sub.add_parser("recalibrate", help="put a sensor back into learning mode")
    recal.add_argument("--sensor", required=True)
    recal.set_defaults(func=cmd_recalibrate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
