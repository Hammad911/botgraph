"""``botgraph``: run and inspect the live pipeline.

All-in-one (in-process bus, no broker needed):
    botgraph run --replay ctu13:6              # demo on a held-out botnet family
    botgraph run --replay iot23:CTU-IoT-Malware-Capture-34-1 --speed 600

As separate services over Kafka/Redpanda (BOTGRAPH_KAFKA_BOOTSTRAP, default localhost:19092):
    botgraph ingest
    botgraph detect --internal-nets 147.32.0.0/16 --learning-minutes 0
    botgraph replay --replay ctu13:6 --speed 60

Inspect:
    botgraph alerts [--open] | botgraph status | botgraph recalibrate --sensor <id>
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import statistics
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table

from botgraph_core import GraphConfig, Label
from botgraph_ml.config import load_params, repo_path, window_spec
from botgraph_ml.drift import REFERENCE_FILE, Reference
from botgraph_ml.metrics import AlertRule
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import ALERTS, DETECTIONS, DLQ, FLOWS, FLOWS_RAW, Bus, KafkaBus, LocalBus
from botgraph_stream.detector import Detector
from botgraph_stream.drift import DriftMonitor
from botgraph_stream.ingest import IngestStats, ingest_step
from botgraph_stream.logs import configure_logging
from botgraph_stream.metrics import CONSUMER_LAG, Health, serve_metrics
from botgraph_stream.replay import Replayer, ReplaySource, load_source
from botgraph_stream.service import DetectorService
from botgraph_stream.store import Store

console = Console()
log = logging.getLogger("botgraph.cli")
LAG_EVERY_S = 15.0


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


def build_service(
    params: dict[str, Any],
    bus: Bus,
    store: Store,
    internal_nets: tuple[str, ...],
    learning_minutes: float,
    model: str | None = None,
    explain_steps: int | None = None,
) -> DetectorService:
    """Detector + alert engine + store, configured from params.yaml's ``stream`` section."""
    sp = params["stream"]
    bundle = repo_path(params["data"]["models_dir"]) / (model or sp["model"])
    detector = Detector(
        bundle,
        GraphConfig(
            internal_nets=internal_nets,
            min_flows_for_periodicity=int(params["graph"]["min_flows_for_periodicity"]),
        ),
        window_spec(params),
        min_flows=int(sp["min_flows"]),
        allowed_lateness_s=float(sp["allowed_lateness_s"]),
    )
    engine = AlertEngine(
        detector.threshold,
        {name: AlertRule(**rule) for name, rule in sp["levels"].items()},
        learning_s=60.0 * learning_minutes,
        baseline_quantile=float(sp["baseline_quantile"]),
        thresholds=store.thresholds(),
    )
    steps = sp["explain_steps"] if explain_steps is None else explain_steps
    return DetectorService(
        bus, detector, engine, store, explain_steps=int(steps), drift=build_drift(sp, bundle, store)
    )


def build_drift(sp: dict[str, Any], bundle: Path, store: Store) -> DriftMonitor | None:
    cfg = sp.get("drift") or {}
    if not cfg.get("enabled", True):
        return None
    training = None
    if (bundle / REFERENCE_FILE).exists():
        training = Reference.load(bundle / REFERENCE_FILE)
    else:
        log.info(
            "no training drift reference; run `python -m botgraph_ml.drift`",
            extra={"bundle": str(bundle)},
        )
    baselines = {}
    for sensor, raw in store.drift_baselines().items():
        try:
            baselines[sensor] = Reference.from_dict(raw)
        except (KeyError, ValueError):  # built for another feature layout: relearn it
            log.warning("discarding incompatible drift baseline", extra={"sensor_id": sensor})
    return DriftMonitor(
        training,
        baselines,
        baseline_windows=int(cfg.get("baseline_windows", 60)),
        recent_windows=int(cfg.get("recent_windows", 60)),
        every=int(cfg.get("every", 10)),
        min_samples=int(cfg.get("min_samples", 1000)),
        max_windows=int(cfg.get("max_windows", 1440)),
    )


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
    service = build_service(
        params, bus, store, source.internal_nets, learning_min, args.model, args.explain_steps
    )
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


# --------------------------------------------------------------------------- kafka services


def _kafka(args: argparse.Namespace, group: str) -> KafkaBus:
    bootstrap = args.bootstrap or os.environ.get("BOTGRAPH_KAFKA_BOOTSTRAP", "localhost:19092")
    bus = KafkaBus(bootstrap, group_id=group, prefix=args.topic_prefix)
    bus.ensure_topics()
    return bus


def serve(
    step: Callable[[], int],
    idle_exit_s: float | None = None,
    health: Health | None = None,
    every: Callable[[], None] | None = None,
    every_s: float = LAG_EVERY_S,
) -> None:
    """Run ``step`` until SIGINT/SIGTERM, or until nothing arrived for ``idle_exit_s``.

    Each iteration is a liveness heartbeat; ``every`` runs at most once per ``every_s``
    (consumer-lag sampling).
    """
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if health is not None:
        health.set_ready()
    last_work = last_every = time.monotonic()
    while not stop:
        if step():
            last_work = time.monotonic()
        elif idle_exit_s is not None and time.monotonic() - last_work >= idle_exit_s:
            break
        if health is not None:
            health.beat()
        if every is not None and time.monotonic() - last_every >= every_s:
            last_every = time.monotonic()
            every()
    if health is not None:
        health.set_ready(False)


def _lag_sampler(bus: KafkaBus, topic: str) -> Callable[[], None]:
    def sample() -> None:
        try:
            CONSUMER_LAG.labels(topic).set(bus.lag(topic))
        except Exception:  # metrics must never take the service down
            log.warning("consumer lag sample failed", exc_info=True)

    return sample


def cmd_replay(args: argparse.Namespace) -> None:
    params = load_params()
    source = load_source(args.replay, params)
    bus = _kafka(args, "replay")
    replayer = Replayer(
        bus, source, speed=args.speed, batch_flows=int(params["stream"]["batch_flows"])
    )
    while not replayer.done:
        replayer.step()
    bus.close()
    console.print(f"replayed {replayer.published:,} flows as sensor {source.sensor_id}")


def _health(args: argparse.Namespace) -> Health | None:
    configure_logging()
    return serve_metrics(args.metrics_port) if args.metrics_port else None


def cmd_ingest(args: argparse.Namespace) -> None:
    health = _health(args)
    bus, stats = _kafka(args, "ingest"), IngestStats()
    log.info(
        "ingest started",
        extra={"bootstrap": bus.bootstrap, "source": f"{args.topic_prefix}{FLOWS_RAW}"},
    )
    serve(
        lambda: ingest_step(bus, stats, timeout=1.0),
        args.idle_exit,
        health,
        _lag_sampler(bus, FLOWS_RAW),
    )
    bus.close()
    log.info("ingest stopped", extra={"stats": vars(stats)})


def cmd_detect(args: argparse.Namespace) -> None:
    health = _health(args)
    params = load_params()
    bus = _kafka(args, "detector")
    nets = tuple(n.strip() for n in args.internal_nets.split(","))
    learning = (
        params["stream"]["learning_minutes"]
        if args.learning_minutes is None
        else args.learning_minutes
    )
    service = build_service(
        params,
        bus,
        Store(args.db or default_db_url()),
        nets,
        learning,
        args.model,
        args.explain_steps,
    )
    log.info(
        "detector started",
        extra={
            "bootstrap": bus.bootstrap,
            "source": f"{args.topic_prefix}{FLOWS}",
            "model": service.detector.metadata.get("model_kind"),
            "internal_nets": nets,
        },
    )
    serve(
        lambda: service.step(timeout=1.0),
        args.idle_exit,
        health,
        _lag_sampler(bus, FLOWS),
    )
    if args.idle_exit is not None:
        service.flush()  # finite input (tests, batch replays): close the remaining windows
    bus.close()
    log.info(
        "detector stopped",
        extra={"windows": service.detector.stats.windows, "events": service.stats.events},
    )


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
    run.add_argument(
        "--explain-steps", type=int, help="Integrated Gradients steps per alert (0 = off)"
    )
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

    def kafka_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--bootstrap", help="Kafka bootstrap servers (env BOTGRAPH_KAFKA_BOOTSTRAP)")
        p.add_argument("--topic-prefix", default="", help="namespace for topic names")
        p.add_argument(
            "--metrics-port",
            type=int,
            default=int(os.environ.get("BOTGRAPH_METRICS_PORT", "0")) or None,
            help="serve /metrics, /healthz and /readyz on this port (env BOTGRAPH_METRICS_PORT)",
        )
        return p

    rep = kafka_args(sub.add_parser("replay", help="publish a recorded capture to flows.raw"))
    rep.add_argument("--replay", required=True, help="ctu13:<scenario> or iot23:<capture>")
    rep.add_argument(
        "--speed", type=float, default=0.0, help="x real time (0 = as fast as possible)"
    )
    rep.set_defaults(func=cmd_replay)

    ing = kafka_args(sub.add_parser("ingest", help="validate flows.raw -> flows.normalized"))
    ing.add_argument("--idle-exit", type=float, help="stop after this many idle seconds")
    ing.set_defaults(func=cmd_ingest)

    det = kafka_args(sub.add_parser("detect", help="windows, GNN scores and alerts"))
    det.add_argument(
        "--internal-nets",
        default="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        help="comma-separated CIDRs of the monitored network",
    )
    det.add_argument("--model", help="bundle name under ml/models (default: stream.model)")
    det.add_argument("--learning-minutes", type=float, help="default: stream.learning_minutes")
    det.add_argument(
        "--explain-steps", type=int, help="Integrated Gradients steps per alert (0 = off)"
    )
    det.add_argument(
        "--idle-exit", type=float, help="stop (and flush) after this many idle seconds"
    )
    det.set_defaults(func=cmd_detect)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
