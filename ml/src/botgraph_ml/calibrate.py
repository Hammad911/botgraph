"""Calibrate the CTU-13 GATv2 to IoT networks with a little benign IoT traffic.

    python -m botgraph_ml.calibrate                    # threshold (default, recommended)
    python -m botgraph_ml.calibrate --method finetune  # documented negative result

The CTU-13 model flags normal IoT devices because their periodic cloud heartbeats look like C2
beaconing. Two ways to adapt it to a new network with a short benign baseline:

* ``threshold``: keep the model; raise the alert threshold to a high quantile of the benign
  baseline's scores ("learning mode"). Needs the scores from ``cross_dataset score``.
* ``finetune``: fine-tune on benign IoT windows mixed with CTU-13 training windows. This made
  things worse: the re-selected threshold collapsed and the model partly forgot CTU-13.

Protocol (no test data is used for calibration):
* rotate over the 3 benign IoT honeypot captures: calibrate on 2, hold out the 3rd;
* test on the held-out honeypot and on every IoT-23 malware capture;
* check forgetting on the CTU-13 test families; recompute the threshold on CTU-13 validation.
The uncalibrated model is scored on exactly the same test data for comparison.
"""

from __future__ import annotations

import argparse
import json
import random
from typing import Any

import pandas as pd
from torch.utils.data import ConcatDataset, Subset
from torch_geometric.data import Data
from torch_geometric.loader import DataLoader

from botgraph_ml.config import graph_config, load_labels, load_params, repo_path, window_spec
from botgraph_ml.cross_dataset import LEVELS, alert_outcome, labelled_windows, score_captures
from botgraph_ml.gnn.data import WindowDataset, list_windows, to_data
from botgraph_ml.gnn.models import load_bundle
from botgraph_ml.gnn.train import fit, predict, resolve_device
from botgraph_ml.metrics import best_f1_threshold, binary_metrics, host_alerts, json_safe

MODEL = "gatv2"
HONEYPOTS = [
    "CTU-Honeypot-Capture-4-1",
    "CTU-Honeypot-Capture-5-1",
    "CTU-Honeypot-Capture-7-1_Somfy-01",
]


def _ctu(params: dict[str, Any], scaler: Any) -> dict[str, WindowDataset]:
    labels = load_labels(repo_path(params["ctu13"]["labels"]))
    split = labels.split(params["gnn"]["split"])
    graphs_dir = repo_path(params["data"]["processed_dir"]) / "ctu13" / "graphs"
    return {part: WindowDataset(list_windows(graphs_dir, split[part]), scaler) for part in split}


def _summarise(scores: pd.DataFrame, col: str, threshold: float) -> dict[str, Any]:
    y = scores["y"].to_numpy()
    out: dict[str, Any] = {}
    if 0 < y.sum() < len(y):
        out["window"] = binary_metrics(y, scores[col].to_numpy(), threshold)
    out["flagged_benign_windows"] = float((scores.loc[y == 0, col] >= threshold).mean())
    out["alerts"] = {lvl: alert_outcome(scores, col, threshold, r) for lvl, r in LEVELS.items()}
    return out


def calibrate_fold(
    params: dict[str, Any],
    held_out: str,
    args: argparse.Namespace,
    base: tuple[Any, Any],
    base_threshold: float,
    ctu: dict[str, WindowDataset],
) -> dict[str, Any]:
    model, scaler = base
    device = resolve_device(params["gnn"]["device"])
    config = graph_config(params, "iot23")
    spec = window_spec(params)
    flows_dir = repo_path(params["data"]["processed_dir"]) / "iot23" / "flows"

    # Benign IoT windows from the two calibration honeypots, oversampled to balance CTU-13.
    calib: list[Data] = [
        to_data(graph, scaler)
        for name in HONEYPOTS
        if name != held_out
        for _, graph in labelled_windows(flows_dir / f"{name}.parquet", config, spec, 1)
    ]
    rng = random.Random(params["gnn"]["seed"])  # noqa: S311 (reproducible sampling, not crypto)
    ctu_idx = rng.sample(range(len(ctu["train"])), min(args.ctu_windows, len(ctu["train"])))
    repeats = max(1, round(args.iot_share * len(ctu_idx) / ((1 - args.iot_share) * len(calib))))
    train = ConcatDataset([Subset(ctu["train"], ctu_idx), *([calib] * repeats)])  # type: ignore[list-item]
    pos = neg = 0
    for i in ctu_idx:
        y = ctu["train"][i].y
        pos += int((y == 1).sum())
        neg += int((y == 0).sum())
    neg += repeats * sum(int((d.y == 0).sum()) for d in calib)

    cfg = {**params["gnn"], "lr": args.lr, "epochs": args.epochs, "patience": args.epochs}
    result = fit(train, ctu["val"], cfg, device, pos_weight=neg / pos, progress=True, init=model)  # type: ignore[arg-type]
    tuned = result.model

    batch = int(params["gnn"]["batch_graphs"])
    val = predict(tuned, DataLoader(ctu["val"], batch_size=batch), device)  # type: ignore[arg-type]
    threshold = best_f1_threshold(val["y"].to_numpy(), val["score"].to_numpy())
    ctu_test_before = predict(model, DataLoader(ctu["test"], batch_size=batch), device)  # type: ignore[arg-type]
    ctu_test_after = predict(tuned, DataLoader(ctu["test"], batch_size=batch), device)  # type: ignore[arg-type]

    # IoT-23 test set: the held-out honeypot plus every malware capture.
    malware = sorted(
        p for p in flows_dir.glob("*.parquet") if not p.stem.startswith("CTU-Honeypot")
    )
    test_paths = [flows_dir / f"{held_out}.parquet", *malware]
    scores, _ = score_captures(
        test_paths,
        {"before": (model, scaler), "after": (tuned, scaler)},
        config,
        spec,
        min_flows=1,
        max_windows=args.max_windows,
    )
    honeypot = scores[scores["capture"] == held_out]
    malware_scores = scores[scores["capture"] != held_out]
    return {
        "held_out": held_out,
        "calibration_windows": len(calib),
        "repeats": repeats,
        "best_epoch": result.best_epoch,
        "threshold": {"before": base_threshold, "after": threshold},
        "ctu13_test_pr_auc": {
            "before": binary_metrics(
                ctu_test_before["y"].to_numpy(), ctu_test_before["score"].to_numpy(), base_threshold
            )["pr_auc"],
            "after": binary_metrics(
                ctu_test_after["y"].to_numpy(), ctu_test_after["score"].to_numpy(), threshold
            )["pr_auc"],
        },
        "held_out_honeypot": {
            "before": _summarise(honeypot, "score_before", base_threshold),
            "after": _summarise(honeypot, "score_after", threshold),
        },
        "malware_captures": {
            "before": _summarise(malware_scores, "score_before", base_threshold),
            "after": _summarise(malware_scores, "score_after", threshold),
        },
    }


def threshold_folds(params: dict[str, Any], quantile: float) -> list[dict[str, Any]]:
    """Recalibrate only the threshold: max(CTU-13 threshold, quantile of baseline scores)."""
    reports = repo_path(params["data"]["reports_dir"])
    scores = pd.read_parquet(reports / "iot23" / "scores.parquet")
    col = f"score_{MODEL}"
    base = float(json.loads((reports / MODEL / "metrics.json").read_text())["threshold"])
    malware = scores[~scores["capture"].isin(HONEYPOTS)]
    folds = []
    for held_out in HONEYPOTS:
        baseline = scores[
            scores["capture"].isin([h for h in HONEYPOTS if h != held_out]) & (scores["y"] == 0)
        ][col]
        threshold = max(base, float(baseline.quantile(quantile)))
        honeypot = scores[scores["capture"] == held_out]
        detected = {}
        for cap, part in malware.groupby("capture"):
            hosts = host_alerts(part.rename(columns={col: "score"}), threshold, LEVELS["alert"])
            detected[str(cap)] = bool(hosts.loc[hosts["y"] == 1, "alerted"].any())
        folds.append(
            {
                "held_out": held_out,
                "method": "threshold",
                "quantile": quantile,
                "threshold": {"before": base, "after": threshold},
                "held_out_honeypot": {
                    "before": _summarise(honeypot, col, base),
                    "after": _summarise(honeypot, col, threshold),
                },
                "malware_captures": {
                    "before": _summarise(malware, col, base),
                    "after": _summarise(malware, col, threshold),
                },
                "infected_device_alerted_after": detected,
            }
        )
    return folds


def _render(folds: list[dict[str, Any]]) -> str:
    def alerts(d: dict[str, Any], level: str, who: str) -> str:
        a = d["alerts"][level]
        return f"{a[f'{who}_alerted']}/{a[f'{who}_total']}"

    method = folds[0].get("method", "finetune")
    how = (
        f"Alert threshold raised to the {folds[0].get('quantile', 0):.0%} quantile of 2 benign IoT "
        "honeypot captures' scores (model unchanged)"
        if method == "threshold"
        else "Fine-tuned on 2 benign IoT honeypot captures (+ CTU-13 training windows)"
    )
    lines = [
        f"# IoT calibration of the CTU-13 GATv2 ({method})",
        "",
        f"{how}; tested on the held-out honeypot and all IoT-23 malware captures.",
        "Before = uncalibrated CTU-13 model.",
        "",
        "## Held-out benign IoT devices",
        "",
        "| Held out | Benign windows flagged (before → after) | Devices alerted, 12 of 15 "
        "(before → after) | Devices alerted, 3 of 5 (before → after) |",
        "|---|---|---|---|",
    ]
    for f in folds:
        b, a = f["held_out_honeypot"]["before"], f["held_out_honeypot"]["after"]
        lines.append(
            f"| {f['held_out'].replace('CTU-Honeypot-Capture-', 'Honeypot ')} "
            f"| {b['flagged_benign_windows']:.0%} → {a['flagged_benign_windows']:.0%} "
            f"| {alerts(b, 'alert', 'benign')} → {alerts(a, 'alert', 'benign')} "
            f"| {alerts(b, 'warning', 'benign')} → {alerts(a, 'warning', 'benign')} |"
        )
    lines += [
        "",
        "## Malware captures and CTU-13 (did it forget bots?)",
        "",
        "| Held out | IoT window PR-AUC | Infected devices alerted (12 of 15) "
        "| Benign devices alerted (12 of 15) | CTU-13 test PR-AUC |",
        "|---|---|---|---|---|",
    ]
    for f in folds:
        b, a = f["malware_captures"]["before"], f["malware_captures"]["after"]
        ctu = f.get("ctu13_test_pr_auc")
        ctu_cell = f"{ctu['before']:.3f} → {ctu['after']:.3f}" if ctu else "unchanged (same model)"
        lines.append(
            f"| {f['held_out'].replace('CTU-Honeypot-Capture-', 'Honeypot ')} "
            f"| {b['window']['pr_auc']:.3f} → {a['window']['pr_auc']:.3f} "
            f"| {alerts(b, 'alert', 'bots')} → {alerts(a, 'alert', 'bots')} "
            f"| {alerts(b, 'alert', 'benign')} → {alerts(a, 'alert', 'benign')} "
            f"| {ctu_cell} |"
        )
    if method == "threshold":
        caps = sorted(folds[0]["infected_device_alerted_after"])
        lines += [
            "",
            "## Infected device alerted after calibration (12 of 15), per held-out fold",
            "",
            "| Capture | " + " | ".join(f["held_out"].split("Capture-")[1] for f in folds) + " |",
            "|---|" + "---|" * len(folds),
        ]
        for cap in caps:
            cells = ["yes" if f["infected_device_alerted_after"][cap] else "no" for f in folds]
            lines.append(f"| {cap.split('Capture-')[1]} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--method", choices=["threshold", "finetune"], default="threshold")
    parser.add_argument("--quantile", type=float, default=0.95, help="threshold method")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--ctu-windows", type=int, default=1000, help="CTU-13 windows per fold")
    parser.add_argument("--iot-share", type=float, default=0.3, help="share of IoT windows")
    parser.add_argument(
        "--folds", nargs="*", choices=HONEYPOTS, help="only these held-out captures"
    )
    parser.add_argument("--max-windows", type=int, help="cap test windows per capture (smoke runs)")
    args = parser.parse_args(argv)

    params = load_params()
    out_dir = repo_path(params["data"]["reports_dir"]) / "iot23"
    if args.method == "threshold":
        folds = threshold_folds(params, args.quantile)
    else:
        folds = finetune_folds(params, args)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"calibration_{args.method}"
    (out_dir / f"{stem}.json").write_text(json.dumps(json_safe(folds), indent=2))
    text = _render(folds)
    (out_dir / f"{stem}.md").write_text(text)
    print(text)


def finetune_folds(params: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    models_dir = repo_path(params["data"]["models_dir"])
    base = load_bundle(models_dir / MODEL)
    base_threshold = float(
        json.loads((repo_path(params["data"]["reports_dir"]) / MODEL / "metrics.json").read_text())[
            "threshold"
        ]
    )
    ctu = _ctu(params, base[1])

    return [
        calibrate_fold(params, held_out, args, base, base_threshold, ctu)
        for held_out in (args.folds or HONEYPOTS)
    ]


if __name__ == "__main__":
    main()
