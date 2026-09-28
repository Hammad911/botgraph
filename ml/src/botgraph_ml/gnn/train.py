"""Train and evaluate a GNN on CTU-13 window graphs.

    python -m botgraph_ml.gnn.train --model e_graphsage
    MLFLOW_TRACKING_URI=http://localhost:5000 python -m botgraph_ml.gnn.train --model gatv2

Uses the same family-held-out split, threshold selection (validation only) and report
format as the XGBoost baseline, so ``ml/reports/*/metrics.json`` are directly comparable.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F  # noqa: N812
from sklearn.metrics import average_precision_score
from torch_geometric.data import Data
from torch_geometric.loader import DataLoader
from tqdm import tqdm

from botgraph_ml.config import alert_rule, load_labels, load_params, output_dirs, repo_path
from botgraph_ml.gnn.data import WindowDataset, fit_scaler, labelled_rows, list_windows
from botgraph_ml.gnn.models import (
    MODEL_KINDS,
    ModelConfig,
    NodeClassifier,
    load_bundle,
    save_bundle,
)
from botgraph_ml.metrics import (
    best_f1_threshold,
    evaluate_scores,
    holdout_report,
    json_safe,
    save_scores,
)
from botgraph_ml.tracking import tracked_run

EpochLogger = Callable[[int, dict[str, float]], None]


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        # MPS lacks some scatter kernels PyG relies on, so Apple silicon trains on CPU.
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


def model_config(cfg: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        kind=cfg["model"],
        hidden=int(cfg["hidden"]),
        layers=int(cfg["layers"]),
        dropout=float(cfg["dropout"]),
        heads=int(cfg.get("heads", 4)),
    )


@torch.no_grad()
def predict(
    model: NodeClassifier,
    loader: DataLoader,
    device: torch.device,
    timings: list[float] | None = None,
) -> pd.DataFrame:
    model.eval()
    frames = []
    for batch in loader:
        batch = batch.to(device)
        start = time.perf_counter()
        logits = model(batch.x, batch.edge_index, batch.edge_attr)
        scores = torch.sigmoid(logits).cpu().numpy()
        if timings is not None:
            timings.append(time.perf_counter() - start)
        frames.append(labelled_rows(batch, scores))
    return pd.concat(frames, ignore_index=True)


def _average_precision(df: pd.DataFrame) -> float:
    if df["y"].nunique() < 2:
        return 0.0
    return float(average_precision_score(df["y"], df["score"]))


@dataclass
class FitResult:
    model: NodeClassifier
    best_epoch: int
    best_val_ap: float
    history: list[dict[str, float]] = field(default_factory=list)


def fit(
    train: Sequence[Data],
    val: Sequence[Data],
    cfg: dict[str, Any],
    device: torch.device,
    pos_weight: float,
    on_epoch: EpochLogger | None = None,
    progress: bool = False,
) -> FitResult:
    """Train with early stopping on validation PR-AUC; returns the best checkpoint."""
    seed_everything(int(cfg["seed"]))
    model = NodeClassifier(model_config(cfg)).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(cfg["lr"]), weight_decay=float(cfg["weight_decay"])
    )
    train_loader = DataLoader(train, batch_size=int(cfg["batch_graphs"]), shuffle=True)  # type: ignore[arg-type]
    val_loader = DataLoader(val, batch_size=int(cfg["batch_graphs"]))  # type: ignore[arg-type]
    weight = torch.tensor(pos_weight, device=device)

    best_ap, best_epoch, stale = -1.0, 0, 0
    best_state = copy.deepcopy(model.state_dict())
    history: list[dict[str, float]] = []

    for epoch in range(1, int(cfg["epochs"]) + 1):
        model.train()
        total, count = 0.0, 0
        bar = tqdm(
            train_loader, desc=f"epoch {epoch}", unit="batch", leave=False, disable=not progress
        )
        for batch in bar:
            batch = batch.to(device)
            mask = batch.y >= 0  # unknown hosts give structure but no loss
            if not mask.any():
                continue
            logits = model(batch.x, batch.edge_index, batch.edge_attr)
            loss = F.binary_cross_entropy_with_logits(
                logits[mask], batch.y[mask].float(), pos_weight=weight
            )
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            total += loss.item() * int(mask.sum())
            count += int(mask.sum())

        val_ap = _average_precision(predict(model, val_loader, device))
        stats = {"train_loss": total / max(count, 1), "val_pr_auc": val_ap}
        history.append({"epoch": epoch, **stats})
        if progress:
            marker = "  *best*" if val_ap > best_ap + 1e-4 else ""
            tqdm.write(
                f"epoch {epoch:>3}: train_loss={stats['train_loss']:.4f} "
                f"val_pr_auc={val_ap:.4f}{marker}"
            )
        if on_epoch:
            on_epoch(epoch, stats)

        if val_ap > best_ap + 1e-4:
            best_ap, best_epoch, stale = val_ap, epoch, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            stale += 1
            if stale >= int(cfg["patience"]):
                break

    model.load_state_dict(best_state)
    return FitResult(model=model, best_epoch=best_epoch, best_val_ap=best_ap, history=history)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", choices=MODEL_KINDS, help="overrides gnn.model in params.yaml")
    parser.add_argument("--epochs", type=int, help="override epochs (quick experiments)")
    parser.add_argument("--split", help="overrides gnn.split, e.g. lofo:Menti for a CV fold")
    parser.add_argument("--patience", type=int, help="override early-stopping patience")
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="re-evaluate the saved bundle for this model/split instead of training",
    )
    args = parser.parse_args(argv)

    params = load_params()
    cfg: dict[str, Any] = dict(params["gnn"])
    cfg["model"] = args.model or cfg["model"]
    cfg["split"] = args.split or cfg["split"]
    if args.patience:
        cfg["patience"] = args.patience
    if args.epochs:
        cfg["epochs"] = args.epochs
    device = resolve_device(cfg["device"])

    labels = load_labels(repo_path(params["ctu13"]["labels"]))
    split = labels.split(cfg["split"])
    graphs_dir = repo_path(params["data"]["processed_dir"]) / "ctu13" / "graphs"
    windows = {part: list_windows(graphs_dir, split[part]) for part in ("train", "val", "test")}
    reports_dir, models_dir = output_dirs(
        params, cfg["model"], cfg["split"], params["gnn"]["split"]
    )

    with tracked_run(cfg["model"], tags={"split": cfg["split"]}) as tracker:
        tracker.log_params(cfg)
        if args.eval_only:
            model, scaler = load_bundle(models_dir, device=str(device))
            previous = json.loads((reports_dir / "metrics.json").read_text())
            training = {k: previous.get(k) for k in ("best_epoch", "train_time_s", "history")}
            training["train_rows"] = previous.get("rows", {}).get("train")
        else:
            scaler, counts = fit_scaler(windows["train"])
            if counts.pos == 0 or counts.neg == 0:
                raise SystemExit("training windows need both bot and benign hosts")
            started = time.perf_counter()
            result = fit(
                WindowDataset(windows["train"], scaler),
                WindowDataset(windows["val"], scaler),
                cfg,
                device,
                pos_weight=counts.neg / counts.pos,
                on_epoch=lambda epoch, stats: tracker.log_metrics(stats, step=epoch),
                progress=True,
            )
            model = result.model
            training = {
                "best_epoch": result.best_epoch,
                "train_time_s": round(time.perf_counter() - started, 1),
                "history": result.history,
                "train_rows": counts.pos + counts.neg,
            }
            save_bundle(models_dir, model, scaler)

        batch = int(cfg["batch_graphs"])
        val_ds = WindowDataset(windows["val"], scaler)
        val_scored = predict(model, DataLoader(val_ds, batch_size=batch), device)  # type: ignore[arg-type]
        threshold = best_f1_threshold(val_scored["y"].to_numpy(), val_scored["score"].to_numpy())

        timings: list[float] = []  # batch_size=1 so each timing is one window
        test_ds = WindowDataset(windows["test"], scaler)
        test_scored = predict(model, DataLoader(test_ds, batch_size=1), device, timings)  # type: ignore[arg-type]

        report = {
            "model": cfg["model"],
            "split": cfg["split"],
            "threshold": threshold,
            "rows": {
                "train": training["train_rows"],
                "val": len(val_scored),
                "test": len(test_scored),
            },
            "windows": {part: len(w) for part, w in windows.items()},
            "best_epoch": training["best_epoch"],
            "train_time_s": training["train_time_s"],
            "latency_ms_per_window": {
                "mean": 1000 * float(np.mean(timings)),
                "p95": 1000 * float(np.percentile(timings, 95)),
            },
            "val": evaluate_scores(val_scored, threshold)["window"],
            "test": holdout_report(test_scored, threshold, labels.families(), alert_rule(params)),
            "history": training["history"],
        }

        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "metrics.json").write_text(json.dumps(json_safe(report), indent=2))
        save_scores(reports_dir, val=val_scored, test=test_scored)
        tracker.log_metrics({"test": report["test"]["window"], "threshold": threshold})
        tracker.log_artifacts(models_dir)
        tracker.log_artifacts(reports_dir)

    w = report["test"]["window"]
    print(
        f"{cfg['model']} [{cfg['split']}] test (window level): precision={w['precision']:.3f} "
        f"recall={w['recall']:.3f} f1={w['f1']:.3f} pr_auc={w['pr_auc']:.3f}"
    )
    print(f"report: {reports_dir / 'metrics.json'}   bundle: {models_dir}")


if __name__ == "__main__":
    main()
