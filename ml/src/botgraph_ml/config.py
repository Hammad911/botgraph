"""Pipeline parameters and dataset label files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from botgraph_core import GraphConfig, Label, WindowSpec
from botgraph_ml.metrics import AlertRule

REPO_ROOT = Path(__file__).resolve().parents[3]
LOFO_PREFIX = "lofo:"


def repo_path(relative: str | Path) -> Path:
    return REPO_ROOT / relative


def load_params(path: Path | None = None) -> dict[str, Any]:
    with (path or repo_path("params.yaml")).open() as fh:
        params: dict[str, Any] = yaml.safe_load(fh)
    return params


def alert_rule(params: dict[str, Any]) -> AlertRule:
    return AlertRule(k=int(params["alert"]["k"]), n=int(params["alert"]["n"]))


def output_dirs(
    params: dict[str, Any], model: str, split: str, default_split: str
) -> tuple[Path, Path]:
    """(reports_dir, models_dir) for a run. Runs on the default split own the top-level
    ``<model>`` dirs; any other split (e.g. a CV fold) goes under ``cv/<model>/<split>``."""
    reports = repo_path(params["data"]["reports_dir"])
    models = repo_path(params["data"]["models_dir"])
    if split == default_split:
        return reports / model, models / model
    fold = split.replace(":", "_")
    return reports / "cv" / model / fold, models / "cv" / model / fold


def window_spec(params: dict[str, Any]) -> WindowSpec:
    return WindowSpec(
        size_s=float(params["window"]["size_s"]), hop_s=float(params["window"]["hop_s"])
    )


def graph_config(params: dict[str, Any], dataset: str) -> GraphConfig:
    return GraphConfig(
        internal_nets=tuple(params[dataset]["internal_nets"]),
        min_flows_for_periodicity=int(params["graph"]["min_flows_for_periodicity"]),
    )


@dataclass(frozen=True, slots=True)
class Scenario:
    id: int
    family: str
    bots: tuple[str, ...]
    normal: tuple[str, ...]

    def host_labels(self) -> dict[str, Label]:
        labels = dict.fromkeys(self.normal, Label.BENIGN)
        labels.update(dict.fromkeys(self.bots, Label.BOTNET))  # bot wins if listed twice
        return labels


@dataclass(frozen=True, slots=True)
class LabelFile:
    scenarios: dict[int, Scenario]
    splits: dict[str, dict[str, list[int]]]

    def families(self) -> dict[int, str]:
        return {sid: s.family for sid, s in self.scenarios.items()}

    def family_names(self) -> list[str]:
        return sorted({s.family for s in self.scenarios.values()})

    def lofo_split_names(self) -> list[str]:
        return [f"{LOFO_PREFIX}{family}" for family in self.family_names()]

    def split(self, name: str) -> dict[str, list[int]]:
        """A named split from the label file, or ``lofo:<Family>`` (leave one family out).

        ``lofo:F`` tests on family F, validates on the next family in alphabetical order
        (wrapping around) and trains on all remaining families.
        """
        if name.startswith(LOFO_PREFIX):
            return self._lofo(name.removeprefix(LOFO_PREFIX))
        if name not in self.splits:
            raise KeyError(
                f"unknown split {name!r}; available: {sorted(self.splits)} or lofo:<Family>"
            )
        return self.splits[name]

    def _lofo(self, test_family: str) -> dict[str, list[int]]:
        families = self.family_names()
        if test_family not in families:
            raise KeyError(f"unknown family {test_family!r}; available: {families}")
        val_family = families[(families.index(test_family) + 1) % len(families)]
        by_family: dict[str, list[int]] = {}
        for sid, s in sorted(self.scenarios.items()):
            by_family.setdefault(s.family, []).append(sid)
        return {
            "train": [
                sid
                for fam, sids in by_family.items()
                if fam not in (test_family, val_family)
                for sid in sids
            ],
            "val": by_family[val_family],
            "test": by_family[test_family],
        }


def load_labels(path: Path) -> LabelFile:
    with path.open() as fh:
        raw = yaml.safe_load(fh)
    scenarios = {
        int(sid): Scenario(
            id=int(sid),
            family=str(spec["family"]),
            bots=tuple(str(ip) for ip in spec["bots"]),
            normal=tuple(str(ip) for ip in spec.get("normal", [])),
        )
        for sid, spec in raw["scenarios"].items()
    }
    splits = {
        name: {part: [int(s) for s in ids] for part, ids in parts.items()}
        for name, parts in raw.get("splits", {}).items()
    }
    for name, parts in splits.items():
        unknown = {s for ids in parts.values() for s in ids} - scenarios.keys()
        if unknown:
            raise ValueError(f"split {name!r} references unknown scenarios {sorted(unknown)}")
    return LabelFile(scenarios=scenarios, splits=splits)
