from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from botgraph_ml.metrics import (
    best_f1_threshold,
    binary_metrics,
    evaluate_scores,
    fpr_at_recall,
    host_scores,
    json_safe,
)


def test_perfect_separation() -> None:
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.2, 0.8, 0.9])
    m = binary_metrics(y, s, threshold=0.5)
    assert m["precision"] == m["recall"] == m["f1"] == m["pr_auc"] == 1.0
    assert m["fpr_at_95_recall"] == 0.0


def test_fpr_at_recall_counts_false_positives() -> None:
    # To catch both positives we must also flag the benign 0.7.
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.7, 0.6, 0.9])
    assert fpr_at_recall(y, s, 0.95) == pytest.approx(0.5)


def test_single_class_gives_nan_aucs() -> None:
    m = binary_metrics(np.array([1, 1]), np.array([0.9, 0.8]), 0.5)
    assert math.isnan(m["pr_auc"]) and math.isnan(m["roc_auc"])
    assert m["recall"] == 1.0


def test_best_f1_threshold() -> None:
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.4, 0.6, 0.9])
    t = best_f1_threshold(y, s)
    assert 0.4 < t <= 0.6


def test_host_scores_time_to_detect() -> None:
    windows = pd.DataFrame(
        {
            "ip": ["bot", "bot", "bot", "ok", "ok"],
            "y": [1, 1, 1, 0, 0],
            "score": [0.2, 0.3, 0.9, 0.1, 0.2],
            "window_start": [0.0, 60.0, 120.0, 0.0, 60.0],
        }
    )
    hosts = host_scores(windows, threshold=0.5).set_index("ip")
    assert hosts.loc["bot", "time_to_detect_s"] == 120.0
    assert math.isnan(hosts.loc["ok", "time_to_detect_s"])
    assert hosts.loc["bot", "score"] == pytest.approx(0.4667, abs=1e-3)

    summary = evaluate_scores(windows, threshold=0.5)
    assert summary["bots_detected"] == summary["bots_total"] == 1
    assert summary["median_time_to_detect_s"] == 120.0


def test_json_safe() -> None:
    assert json_safe({"a": [math.nan, 1.0], "b": math.inf}) == {"a": [None, 1.0], "b": None}
