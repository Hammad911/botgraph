"""End-to-end baseline run on synthetic node tables (no dataset download needed).

XGBoost runs in a spawned child process: on macOS its OpenMP runtime (Homebrew libomp)
deadlocks against torch's bundled libomp when both load into one process, and pytest
imports every test module into the same interpreter.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from botgraph_core import NODE_FEATURES
from botgraph_ml.config import LabelFile, Scenario

PARAMS = {
    "split": "s",
    "n_estimators": 50,
    "max_depth": 3,
    "learning_rate": 0.3,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "early_stopping_rounds": 10,
    "seed": 0,
}


def _nodes(rng: np.random.Generator, windows: int) -> pd.DataFrame:
    rows = []
    for w in range(windows):
        for ip, y in (("bot-1", 1), ("bot-2", 1), ("pc-1", 0), ("pc-2", 0), ("pc-3", 0)):
            x = rng.normal(0.0, 0.3, len(NODE_FEATURES))
            if y:
                x[NODE_FEATURES.index("periodicity")] += 2.0  # bots beacon
            rows.append(
                {
                    "window_id": f"w{w}",
                    "window_start": 60.0 * w,
                    "ip": ip,
                    "y": y,
                    **dict(zip(NODE_FEATURES, x, strict=True)),
                }
            )
    return pd.DataFrame(rows).astype({"y": "int8"})


def _run_baseline(nodes_dir: str) -> dict[str, Any]:
    from botgraph_ml.baseline import run  # imported only in the child process

    labels = LabelFile(
        scenarios={sid: Scenario(sid, f"fam{sid}", (), ()) for sid in (1, 2, 3)},
        splits={"s": {"train": [1], "val": [2], "test": [3]}},
    )
    _, report, _ = run(labels, Path(nodes_dir), PARAMS)
    return report


def test_baseline_learns_separable_signal(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    for sid in (1, 2, 3):
        _nodes(rng, windows=40).to_parquet(tmp_path / f"scenario={sid}.parquet", index=False)
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        report = pool.apply(_run_baseline, (str(tmp_path),))

    assert report["test"]["window"]["f1"] > 0.95
    alerts = report["test"]["per_scenario"]["3"]["alerts"]
    assert (alerts["bots_alerted"], alerts["benign_alerted"]) == (2, 0)
    assert next(iter(report["feature_importance"])) == "periodicity"
