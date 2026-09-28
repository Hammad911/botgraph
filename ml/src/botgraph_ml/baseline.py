"""XGBoost on per-window node features, with no graph message passing.

This is the bar the GNNs must beat: anything they add over it comes from the graph.

Do not import this module in a process that also imports torch: on macOS XGBoost's
OpenMP runtime deadlocks against torch's (see ml/tests/test_baseline.py). The DVC
stages run as separate processes, so the pipeline itself is unaffected.

    python -m botgraph_ml.baseline
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from botgraph_core import NODE_FEATURES
from botgraph_ml.config import (
    LabelFile,
    alert_rule,
    load_labels,
    load_params,
    output_dirs,
    repo_path,
)
from botgraph_ml.metrics import (
    AlertRule,
    best_f1_threshold,
    evaluate_scores,
    holdout_report,
    json_safe,
)

FEATURES = list(NODE_FEATURES)


def load_nodes(nodes_dir: Path, scenarios: list[int]) -> pd.DataFrame:
    frames = []
    for sid in scenarios:
        path = nodes_dir / f"scenario={sid}.parquet"
        if not path.exists():
            raise SystemExit(
                f"{path} missing; run `python -m botgraph_ml.build_graphs ctu13` first"
            )
        frames.append(pd.read_parquet(path).assign(scenario=sid))
    return pd.concat(frames, ignore_index=True)


def train(train_df: pd.DataFrame, val_df: pd.DataFrame, p: dict[str, Any]) -> XGBClassifier:
    pos = int((train_df["y"] == 1).sum())
    neg = len(train_df) - pos
    if pos == 0 or neg == 0:
        raise ValueError("training data needs both bot and benign rows")
    model = XGBClassifier(
        n_estimators=p["n_estimators"],
        max_depth=p["max_depth"],
        learning_rate=p["learning_rate"],
        subsample=p["subsample"],
        colsample_bytree=p["colsample_bytree"],
        early_stopping_rounds=p["early_stopping_rounds"],
        scale_pos_weight=neg / pos,
        eval_metric="aucpr",
        random_state=p["seed"],
        n_jobs=-1,
    )
    model.fit(
        train_df[FEATURES],
        train_df["y"],
        eval_set=[(val_df[FEATURES], val_df["y"])],
        verbose=False,
    )
    return model


def score(model: XGBClassifier, df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(score=model.predict_proba(df[FEATURES])[:, 1])


def run(
    labels: LabelFile, nodes_dir: Path, p: dict[str, Any], rule: AlertRule | None = None
) -> tuple[XGBClassifier, dict[str, Any]]:
    split = labels.split(p["split"])
    train_df = load_nodes(nodes_dir, split["train"])
    val_df = load_nodes(nodes_dir, split["val"])
    test_df = load_nodes(nodes_dir, split["test"])

    model = train(train_df, val_df, p)
    val_scored = score(model, val_df)
    threshold = best_f1_threshold(val_scored["y"].to_numpy(), val_scored["score"].to_numpy())
    test_scored = score(model, test_df)

    report = {
        "model": "xgboost_node_features",
        "split": p["split"],
        "threshold": threshold,
        "rows": {"train": len(train_df), "val": len(val_df), "test": len(test_df)},
        "best_iteration": int(model.best_iteration),
        "val": evaluate_scores(val_scored, threshold)["window"],
        "test": holdout_report(test_scored, threshold, labels.families(), rule),
        "feature_importance": dict(
            sorted(
                zip(FEATURES, model.feature_importances_.astype(float).tolist(), strict=True),
                key=lambda kv: -kv[1],
            )
        ),
    }
    return model, report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--split", help="overrides baseline.split in params.yaml")
    args = parser.parse_args(argv)

    params = load_params()
    p = dict(params["baseline"])
    p["split"] = args.split or p["split"]
    np.random.seed(p["seed"])
    labels = load_labels(repo_path(params["ctu13"]["labels"]))
    nodes_dir = repo_path(params["data"]["processed_dir"]) / "ctu13" / "nodes"

    model, report = run(labels, nodes_dir, p, alert_rule(params))

    reports_dir, models_dir = output_dirs(
        params, "baseline", p["split"], params["baseline"]["split"]
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "metrics.json").write_text(json.dumps(json_safe(report), indent=2))
    model.save_model(models_dir / "xgb.json")

    w = report["test"]["window"]
    print(
        f"test (window level): precision={w['precision']:.3f} recall={w['recall']:.3f} "
        f"f1={w['f1']:.3f} pr_auc={w['pr_auc']:.3f}"
    )
    print(f"report: {reports_dir / 'metrics.json'}")


if __name__ == "__main__":
    main()
