from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from botgraph_ml.metrics import (
    AlertRule,
    best_f1_threshold,
    binary_metrics,
    evaluate_scores,
    fpr_at_recall,
    host_alerts,
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


def test_alert_rule_needs_k_hits_in_last_n_windows() -> None:
    windows = pd.DataFrame(
        {
            "ip": ["bot"] * 5 + ["noisy"] * 5,
            "y": [1] * 5 + [0] * 5,
            # bot: 3 hits within its last 5 windows by t=240; noisy: a single one-off hit
            "score": [0.2, 0.9, 0.9, 0.2, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1],
            "window_start": [0.0, 60.0, 120.0, 180.0, 240.0] * 2,
        }
    )
    hosts = host_alerts(windows, threshold=0.5, rule=AlertRule(k=3, n=5)).set_index("ip")
    assert hosts.loc["bot", "alerted"]
    assert hosts.loc["bot", "time_to_alert_s"] == 240.0
    assert not hosts.loc["noisy", "alerted"]
    assert math.isnan(hosts.loc["noisy", "time_to_alert_s"])
    assert hosts.loc["noisy", "flagged_frac"] == pytest.approx(0.2)

    # k=1 alerts on any single hit, so the noisy host now alerts too
    loose = host_alerts(windows, threshold=0.5, rule=AlertRule(k=1, n=5)).set_index("ip")
    assert loose["alerted"].all()

    summary = evaluate_scores(windows, threshold=0.5, rule=AlertRule(k=3, n=5))["alerts"]
    assert (summary["bots_alerted"], summary["bots_total"]) == (1, 1)
    assert (summary["benign_alerted"], summary["benign_total"]) == (0, 1)
    assert summary["precision"] == 1.0
    assert summary["median_time_to_alert_s"] == 240.0


def test_alert_rule_window_only_looks_back_n() -> None:
    # Three hits spread so that no 3 fall within any 3 consecutive windows.
    windows = pd.DataFrame(
        {
            "ip": ["h"] * 7,
            "y": [1] * 7,
            "score": [0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.9],
            "window_start": [60.0 * i for i in range(7)],
        }
    )
    assert not host_alerts(windows, 0.5, AlertRule(k=2, n=3))["alerted"].iloc[0]
    assert host_alerts(windows, 0.5, AlertRule(k=2, n=4))["alerted"].iloc[0]


def test_invalid_alert_rule() -> None:
    with pytest.raises(ValueError):
        AlertRule(k=4, n=3)


def test_json_safe() -> None:
    assert json_safe({"a": [math.nan, 1.0], "b": math.inf}) == {"a": [None, 1.0], "b": None}
