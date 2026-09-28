from __future__ import annotations

import pandas as pd

from botgraph_ml.alert_tuning import Fold, choose_rule, evaluate
from botgraph_ml.metrics import AlertRule


def _scores(bot_flags: list[int], noisy_flags: list[int]) -> pd.DataFrame:
    """One scenario: a bot and a normal host, one row per 1-minute window."""
    rows = [
        {
            "scenario": 1,
            "window_id": f"w{i}",
            "window_start": 60.0 * i,
            "ip": ip,
            "y": y,
            "score": float(f),
        }
        for ip, y, flags in (("bot", 1, bot_flags), ("pc", 0, noisy_flags))
        for i, f in enumerate(flags)
    ]
    return pd.DataFrame(rows)


def test_choose_rule_prefers_fewest_false_alerts_at_full_recall() -> None:
    # The bot is flagged continuously; the normal host has one short 2-window burst.
    val = _scores(bot_flags=[1] * 30, noisy_flags=[0] * 10 + [1, 1] + [0] * 18)
    fold = Fold(family="X", threshold=0.5, scores={"val": val, "test": val})

    chosen, outcomes = choose_rule([fold])

    assert outcomes[AlertRule(k=1, n=5)].benign == 1  # loose rules alert on the burst
    assert outcomes[chosen].recall == 1.0
    assert outcomes[chosen].benign == 0
    assert chosen.k >= 3  # needs more than the 2-window burst to fire


def test_evaluate_counts_hosts_per_scenario() -> None:
    a = _scores([1] * 5, [0] * 5)
    b = _scores([0] * 5, [1] * 5).assign(scenario=2)
    fold = Fold(family="X", threshold=0.5, scores={"val": pd.concat([a, b]), "test": a})
    out = evaluate([fold], "val", AlertRule(k=3, n=5))
    assert (out.bots, out.bots_total, out.benign, out.benign_total) == (1, 2, 1, 2)
    assert out.median_minutes == 2.0  # alert fires on the 3rd window
