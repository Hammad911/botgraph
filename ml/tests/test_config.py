from __future__ import annotations

from pathlib import Path

import pytest

from botgraph_core import Label
from botgraph_ml.config import load_labels, load_params, repo_path, window_spec


def test_repo_ctu13_labels_are_consistent() -> None:
    labels = load_labels(repo_path("ml/labels/ctu13.yaml"))

    assert sorted(labels.scenarios) == list(range(1, 14))
    assert len(labels.scenarios[9].bots) == 10
    split = labels.split("holdout_families")
    assert not set(split["train"]) & set(split["test"])

    train_families = {labels.scenarios[s].family for s in split["train"]}
    test_families = {labels.scenarios[s].family for s in split["test"]}
    assert not train_families & test_families  # no family leaks from train into test

    host_labels = labels.scenarios[1].host_labels()
    assert host_labels["147.32.84.165"] is Label.BOTNET
    assert host_labels["147.32.84.170"] is Label.BENIGN


def test_split_with_unknown_scenario_rejected(tmp_path: Path) -> None:
    path = tmp_path / "labels.yaml"
    path.write_text(
        "scenarios:\n  1: {family: A, bots: [10.0.0.1]}\nsplits:\n  s: {train: [1], test: [99]}\n"
    )
    with pytest.raises(ValueError, match="unknown scenarios"):
        load_labels(path)


def test_repo_params_load() -> None:
    params = load_params()
    spec = window_spec(params)
    assert spec.size_s >= spec.hop_s
