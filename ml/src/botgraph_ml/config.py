"""Pipeline parameters and dataset label files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from botgraph_core import GraphConfig, Label, WindowSpec

REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_path(relative: str | Path) -> Path:
    return REPO_ROOT / relative


def load_params(path: Path | None = None) -> dict[str, Any]:
    with (path or repo_path("params.yaml")).open() as fh:
        params: dict[str, Any] = yaml.safe_load(fh)
    return params


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

    def split(self, name: str) -> dict[str, list[int]]:
        if name not in self.splits:
            raise KeyError(f"unknown split {name!r}; available: {sorted(self.splits)}")
        return self.splits[name]


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
