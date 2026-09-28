"""Evaluation metrics shared by every model (baseline and GNNs), so results are comparable."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)


def fpr_at_recall(y_true: np.ndarray, scores: np.ndarray, target: float = 0.95) -> float:
    """False positive rate at the first threshold that reaches ``target`` recall."""
    if y_true.min() == y_true.max():
        return math.nan
    fpr, tpr, _ = roc_curve(y_true, scores)
    return float(fpr[np.argmax(tpr >= target)])


def best_f1_threshold(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Threshold maximising F1. Pick it on validation data, never on test data."""
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    with np.errstate(divide="ignore", invalid="ignore"):
        f1 = np.nan_to_num(2 * precision * recall / (precision + recall))
    return float(thresholds[int(np.argmax(f1[:-1]))])


def binary_metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, float]:
    y_true = np.asarray(y_true).astype(int)
    scores = np.asarray(scores, dtype=float)
    pred = (scores >= threshold).astype(int)
    both = y_true.min() != y_true.max()
    return {
        "n": len(y_true),
        "n_pos": int(y_true.sum()),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "pr_auc": float(average_precision_score(y_true, scores)) if both else math.nan,
        "roc_auc": float(roc_auc_score(y_true, scores)) if both else math.nan,
        "fpr_at_95_recall": fpr_at_recall(y_true, scores, 0.95),
    }


def host_scores(windows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Aggregate per-window scores into one row per host.

    ``windows`` needs columns: ip, y, score, window_start. A host's score is the mean of
    its window scores. ``time_to_detect_s`` is the delay between the host's first window
    and its first window scored at or above ``threshold`` (NaN if never detected).
    """
    ordered = windows.sort_values("window_start")
    first_seen = ordered.groupby("ip")["window_start"].min()
    first_hit = ordered[ordered["score"] >= threshold].groupby("ip")["window_start"].min()
    hosts = ordered.groupby("ip").agg(
        y=("y", "max"), score=("score", "mean"), windows=("score", "size")
    )
    hosts["time_to_detect_s"] = (first_hit - first_seen).reindex(hosts.index)
    return hosts.reset_index()


def evaluate_scores(windows: pd.DataFrame, threshold: float) -> dict[str, Any]:
    """Window-level and host-level metrics plus median time-to-detect for bots."""
    hosts = host_scores(windows, threshold)
    bot_ttd = hosts.loc[hosts["y"] == 1, "time_to_detect_s"]
    return {
        "window": binary_metrics(windows["y"].to_numpy(), windows["score"].to_numpy(), threshold),
        "host": binary_metrics(hosts["y"].to_numpy(), hosts["score"].to_numpy(), threshold),
        "bots_detected": int(bot_ttd.notna().sum()),
        "bots_total": len(bot_ttd),
        "median_time_to_detect_s": float(bot_ttd.median()) if bot_ttd.notna().any() else math.nan,
    }


def json_safe(value: Any) -> Any:
    """Replace NaN/inf with None so reports are strict JSON."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
