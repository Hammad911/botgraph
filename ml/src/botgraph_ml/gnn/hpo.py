"""Hyperparameter search with Optuna, optimising validation PR-AUC (test data is never used).

    python -m botgraph_ml.gnn.hpo --model e_graphsage --trials 30

Writes the best parameters to ml/reports/hpo/<model>.json; copy the ones you accept
into the ``gnn`` section of params.yaml so the tuned run stays reproducible via DVC.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import optuna

from botgraph_ml.config import load_labels, load_params, repo_path
from botgraph_ml.gnn.data import WindowDataset, fit_scaler, list_windows
from botgraph_ml.gnn.models import MODEL_KINDS
from botgraph_ml.gnn.train import fit, resolve_device


def suggest(trial: optuna.Trial, base: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(base)
    cfg["hidden"] = trial.suggest_categorical("hidden", [32, 64, 128])
    cfg["layers"] = trial.suggest_int("layers", 1, 3)
    cfg["dropout"] = trial.suggest_float("dropout", 0.0, 0.5)
    cfg["lr"] = trial.suggest_float("lr", 1e-4, 1e-2, log=True)
    cfg["weight_decay"] = trial.suggest_float("weight_decay", 1e-6, 1e-2, log=True)
    return cfg


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", choices=MODEL_KINDS, default="e_graphsage")
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--epochs", type=int, default=20, help="per-trial epoch budget")
    args = parser.parse_args(argv)

    params = load_params()
    base: dict[str, Any] = {**params["gnn"], "model": args.model, "epochs": args.epochs}
    device = resolve_device(base["device"])
    split = load_labels(repo_path(params["ctu13"]["labels"])).split(base["split"])
    graphs_dir = repo_path(params["data"]["processed_dir"]) / "ctu13" / "graphs"

    train_windows = list_windows(graphs_dir, split["train"])
    scaler, counts = fit_scaler(train_windows)
    train = WindowDataset(train_windows, scaler)
    val = WindowDataset(list_windows(graphs_dir, split["val"]), scaler)

    def objective(trial: optuna.Trial) -> float:
        def report(epoch: int, stats: dict[str, float]) -> None:
            trial.report(stats["val_pr_auc"], epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()

        result = fit(
            train,
            val,
            suggest(trial, base),
            device,
            pos_weight=counts.neg / counts.pos,
            on_epoch=report,
        )
        return result.best_val_ap

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=int(base["seed"])),
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=5),
    )
    study.optimize(objective, n_trials=args.trials)

    out = repo_path(params["data"]["reports_dir"]) / "hpo" / f"{args.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"best_val_pr_auc": study.best_value, "params": study.best_params}, indent=2)
    )
    print(f"best val PR-AUC {study.best_value:.4f} with {study.best_params} -> {out}")


if __name__ == "__main__":
    main()
