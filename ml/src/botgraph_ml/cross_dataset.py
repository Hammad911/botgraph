"""Cross-dataset test: models trained on CTU-13, applied to IoT-23 with no retraining.

    python -m botgraph_ml.cross_dataset score     # build graphs on the fly, score with the GNNs
    python -m botgraph_ml.cross_dataset xgboost   # add XGBoost scores (separate process: on
                                                  # macOS XGBoost and torch must not share one)
    python -m botgraph_ml.cross_dataset report    # -> ml/reports/iot23/report.{md,json}

Graphs are never written to disk: IoT-23 malware scans thousands of addresses a minute, so
its window graphs are far larger than CTU-13's. Each window is built, scored by every model
and discarded, keeping only labelled hosts' features and scores (the same streaming shape as
live inference). Thresholds come from each model's CTU-13 validation set, unchanged.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

from botgraph_core import NODE_FEATURES, build_window_graph, sliding_windows
from botgraph_ml.config import graph_config, load_params, repo_path, window_spec
from botgraph_ml.flow_labels import window_host_labels
from botgraph_ml.metrics import AlertRule, binary_metrics, host_alerts, json_safe

GNN_MODELS = ["graphsage", "e_graphsage", "gatv2"]
ALL_MODELS = ["baseline", *GNN_MODELS]
NAMES = {
    "baseline": "XGBoost",
    "graphsage": "GraphSAGE",
    "e_graphsage": "E-GraphSAGE",
    "gatv2": "GATv2",
}
LEVELS = {"warning": AlertRule(k=3, n=5), "alert": AlertRule(k=12, n=15)}


def _paths(params: dict[str, Any]) -> tuple[Path, Path]:
    flows_dir = repo_path(params["data"]["processed_dir"]) / "iot23" / "flows"
    out_dir = repo_path(params["data"]["reports_dir"]) / "iot23"
    return flows_dir, out_dir


# --------------------------------------------------------------------------- score (torch)


def score(params: dict[str, Any], max_windows: int | None, min_flows: int) -> Path:
    import torch  # only this step needs torch

    from botgraph_ml.gnn.data import to_data
    from botgraph_ml.gnn.models import load_bundle

    flows_dir, out_dir = _paths(params)
    captures = sorted(flows_dir.glob("*.parquet"))
    if not captures:
        raise SystemExit(f"no flows in {flows_dir}; run download + prepare iot23 first")
    models_dir = repo_path(params["data"]["models_dir"])
    bundles = {m: load_bundle(models_dir / m) for m in GNN_MODELS if (models_dir / m).is_dir()}
    config = graph_config(params, "iot23")
    spec = window_spec(params)

    parts: list[pd.DataFrame] = []
    timings: dict[str, list[float]] = {m: [] for m in bundles}
    for path in captures:
        flows = pd.read_parquet(path)
        capture_rows = 0
        windows = sliding_windows(flows, spec, min_flows=min_flows)
        for i, window in enumerate(tqdm(windows, desc=path.stem, unit="win", leave=False)):
            if max_windows is not None and i >= max_windows:
                break
            labels = window_host_labels(window.flows, config.internal_nets)
            if not labels:
                continue
            graph = build_window_graph(window.flows, window.window_id, config, labels)
            keep = graph.y >= 0
            part = pd.DataFrame(graph.x[keep], columns=list(NODE_FEATURES))
            part.insert(0, "y", graph.y[keep])
            part.insert(0, "ip", [ip for ip, k in zip(graph.nodes, keep, strict=True) if k])
            part.insert(0, "window_start", window.start)
            part.insert(0, "window_id", window.window_id)
            part.insert(0, "capture", path.stem)
            part["graph_nodes"] = graph.num_nodes
            for name, (model, scaler) in bundles.items():
                data = to_data(graph, scaler)
                started = time.perf_counter()
                with torch.no_grad():
                    logits = model(data.x, data.edge_index, data.edge_attr)
                timings[name].append(time.perf_counter() - started)
                part[f"score_{name}"] = torch.sigmoid(logits).numpy()[keep]
            parts.append(part)
            capture_rows += len(part)
        tqdm.write(f"{path.stem}: {len(flows):,} flows -> {capture_rows:,} labelled host-windows")

    out_dir.mkdir(parents=True, exist_ok=True)
    scores = pd.concat(parts, ignore_index=True)
    scores.to_parquet(out_dir / "scores.parquet", index=False)
    latency = {
        m: {"mean_ms": 1000 * float(np.mean(t)), "p95_ms": 1000 * float(np.percentile(t, 95))}
        for m, t in timings.items()
        if t
    }
    (out_dir / "latency.json").write_text(json.dumps(latency, indent=2))
    return out_dir / "scores.parquet"


# --------------------------------------------------------------------------- xgboost


def score_xgboost(params: dict[str, Any]) -> None:
    from xgboost import XGBClassifier

    _, out_dir = _paths(params)
    scores = pd.read_parquet(out_dir / "scores.parquet")
    model = XGBClassifier()
    model.load_model(repo_path(params["data"]["models_dir"]) / "baseline" / "xgb.json")
    scores["score_baseline"] = model.predict_proba(scores[list(NODE_FEATURES)])[:, 1]
    scores.to_parquet(out_dir / "scores.parquet", index=False)


# --------------------------------------------------------------------------- report


def _threshold(params: dict[str, Any], model: str) -> float:
    path = repo_path(params["data"]["reports_dir"]) / model / "metrics.json"
    return float(json.loads(path.read_text())["threshold"])


def _alerts(scores: pd.DataFrame, col: str, threshold: float, rule: AlertRule) -> dict[str, Any]:
    bots = benign = bots_total = benign_total = 0
    times: list[float] = []
    for _, cap in scores.groupby("capture"):
        hosts = host_alerts(cap.rename(columns={col: "score"}), threshold, rule)
        b, g = hosts[hosts["y"] == 1], hosts[hosts["y"] == 0]
        bots += int(b["alerted"].sum())
        bots_total += len(b)
        benign += int(g["alerted"].sum())
        benign_total += len(g)
        times += b["time_to_alert_s"].dropna().tolist()
    return {
        "bots_alerted": bots,
        "bots_total": bots_total,
        "benign_alerted": benign,
        "benign_total": benign_total,
        "median_time_to_alert_min": float(np.median(times)) / 60 if times else None,
    }


def report(params: dict[str, Any]) -> str:
    _, out_dir = _paths(params)
    scores = pd.read_parquet(out_dir / "scores.parquet")
    models = [m for m in ALL_MODELS if f"score_{m}" in scores.columns]
    latency = json.loads((out_dir / "latency.json").read_text())
    y = scores["y"].to_numpy()

    results: dict[str, Any] = {
        "rows": len(scores),
        "bot_rows": int((y == 1).sum()),
        "benign_rows": int((y == 0).sum()),
        "windows": int(scores["window_id"].nunique()),
        "captures": sorted(scores["capture"].unique().tolist()),
        "models": {},
    }
    lines = [
        "# Cross-dataset test: CTU-13 models on IoT-23 (no retraining)",
        "",
        f"{results['rows']:,} labelled host-windows from {len(results['captures'])} captures "
        f"({results['bot_rows']:,} bot, {results['benign_rows']:,} benign). Thresholds are "
        "each model's CTU-13 validation threshold, unchanged.",
        "",
        "| Model | PR-AUC | Precision | Recall | FPR @ 95% recall | Latency p95 (ms/window) |",
        "|---|---|---|---|---|---|",
    ]
    alert_lines = [
        "",
        "## Alerts per device",
        "",
        "| Model | Level | Rule | Infected devices alerted | Benign devices alerted "
        "| Median time to alert |",
        "|---|---|---|---|---|---|",
    ]
    for m in models:
        col = f"score_{m}"
        thr = _threshold(params, m)
        w = binary_metrics(y, scores[col].to_numpy(), thr)
        lat = latency.get(m, {}).get("p95_ms")
        lines.append(
            f"| {NAMES[m]} | {w['pr_auc']:.3f} | {w['precision']:.3f} | {w['recall']:.3f} "
            f"| {w['fpr_at_95_recall']:.3f} | {'n/a' if lat is None else f'{lat:.1f}'} |"
        )
        levels = {}
        for level, rule in LEVELS.items():
            a = _alerts(scores, col, thr, rule)
            levels[level] = a
            ttd = a["median_time_to_alert_min"]
            alert_lines.append(
                f"| {NAMES[m]} | {level} | {rule.k} of {rule.n} "
                f"| {a['bots_alerted']}/{a['bots_total']} "
                f"| {a['benign_alerted']}/{a['benign_total']} "
                f"| {'n/a' if ttd is None else f'{ttd:.0f} min'} |"
            )
        results["models"][m] = {"threshold": thr, "window": w, "alerts": levels}

    per_capture = [
        "",
        "## Per capture (window-level PR-AUC)",
        "",
        "| Capture | Bot windows | Benign windows | " + " | ".join(NAMES[m] for m in models) + " |",
        "|---|---|---|" + "---|" * len(models),
    ]
    for cap, part in scores.groupby("capture"):
        yy = part["y"].to_numpy()
        both = 0 < yy.sum() < len(yy)
        cells = [
            f"{binary_metrics(yy, part[f'score_{m}'].to_numpy(), 0.5)['pr_auc']:.3f}"
            if both
            else "n/a"
            for m in models
        ]
        per_capture.append(
            f"| {cap} | {int((yy == 1).sum())} | {int((yy == 0).sum())} | "
            + " | ".join(cells)
            + " |"
        )

    text = "\n".join(lines + alert_lines + per_capture) + "\n"
    (out_dir / "report.md").write_text(text)
    (out_dir / "report.json").write_text(json.dumps(json_safe(results), indent=2))
    return text


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("step", choices=["score", "xgboost", "report"])
    parser.add_argument("--max-windows", type=int, help="cap windows per capture (score step)")
    parser.add_argument(
        "--min-flows",
        type=int,
        default=1,
        help="skip windows with fewer flows (default 1: IoT-23's benign honeypots are so quiet "
        "that CTU-13's training minimum of 10 would discard almost all their windows)",
    )
    args = parser.parse_args(argv)
    params = load_params()
    if args.step == "score":
        print(f"scores: {score(params, args.max_windows, args.min_flows)}")
    elif args.step == "xgboost":
        score_xgboost(params)
        print("added XGBoost scores")
    else:
        print(report(params))


if __name__ == "__main__":
    main()
