"""Evaluation metrics shared by every model (baseline and GNNs), so results are comparable."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class AlertRule:
    """Alert on a host once it is flagged in at least ``k`` of its last ``n`` windows.

    Windows overlap (5-minute windows every minute), so one burst of bot traffic spans
    several consecutive windows; requiring ``k`` hits filters out one-off false positives.
    """

    k: int = 3
    n: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.k <= self.n:
            raise ValueError("alert rule needs 1 <= k <= n")


def host_alerts(windows: pd.DataFrame, threshold: float, rule: AlertRule) -> pd.DataFrame:
    """Apply ``rule`` to each host's time-ordered window scores; one row per host.

    ``windows`` needs columns: ip, y, score, window_start. The last ``n`` windows are the
    host's own last ``n`` appearances. ``time_to_alert_s`` is measured from the host's first
    window to the window that triggered the alert (NaN if it never alerted).
    """
    ordered = windows.sort_values(["ip", "window_start"], kind="stable")
    flagged = (ordered["score"] >= threshold).astype(int)
    hits = flagged.groupby(ordered["ip"]).transform(
        lambda f: f.rolling(rule.n, min_periods=1).sum()
    )
    alert_starts = ordered.loc[hits >= rule.k].groupby("ip")["window_start"].min()

    hosts = ordered.groupby("ip").agg(
        y=("y", "max"), first_seen=("window_start", "min"), windows=("score", "size")
    )
    hosts["flagged_frac"] = flagged.groupby(ordered["ip"]).mean()
    hosts["alerted"] = hosts.index.isin(alert_starts.index)
    hosts["time_to_alert_s"] = (alert_starts - hosts["first_seen"]).reindex(hosts.index)
    return hosts.reset_index()


def alert_metrics(hosts: pd.DataFrame) -> dict[str, Any]:
    bots, benign = hosts[hosts["y"] == 1], hosts[hosts["y"] == 0]
    tp = int(bots["alerted"].sum())
    fp = int(benign["alerted"].sum())
    ttd = bots["time_to_alert_s"].dropna()
    return {
        "bots_alerted": tp,
        "bots_total": len(bots),
        "benign_alerted": fp,
        "benign_total": len(benign),
        "precision": tp / (tp + fp) if tp + fp else math.nan,
        "recall": tp / len(bots) if len(bots) else math.nan,
        "bot_windows_flagged": float(bots["flagged_frac"].mean()) if len(bots) else math.nan,
        "benign_windows_flagged": float(benign["flagged_frac"].mean()) if len(benign) else math.nan,
        "median_time_to_alert_s": float(ttd.median()) if len(ttd) else math.nan,
    }


def evaluate_scores(
    windows: pd.DataFrame, threshold: float, rule: AlertRule | None = None
) -> dict[str, Any]:
    """Window-level metrics plus host-level alert outcomes under ``rule``."""
    rule = rule or AlertRule()
    return {
        "window": binary_metrics(windows["y"].to_numpy(), windows["score"].to_numpy(), threshold),
        "alerts": alert_metrics(host_alerts(windows, threshold, rule)),
    }


def holdout_report(
    scored: pd.DataFrame,
    threshold: float,
    families: Mapping[int, str],
    rule: AlertRule | None = None,
) -> dict[str, Any]:
    """Overall window metrics plus a per-scenario (per botnet family) breakdown.

    ``scored`` needs columns: scenario, ip, y, score, window_start. Host IPs repeat across
    CTU-13 scenarios, so alerts are evaluated within each scenario.
    """
    rule = rule or AlertRule()
    per_scenario = {
        str(sid): {"family": families[int(sid)], **evaluate_scores(part, threshold, rule)}
        for sid, part in scored.groupby("scenario")
    }
    return {
        "alert_rule": {"k": rule.k, "n": rule.n},
        "window": evaluate_scores(scored, threshold, rule)["window"],
        "per_scenario": per_scenario,
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
