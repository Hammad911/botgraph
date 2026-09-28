"""WindowGraph -> PyG ``Data`` conversion, feature scaling and lazy window datasets.

``to_data`` is the single conversion used by training, evaluation and (later) the live
inference service, so a model always sees identically prepared inputs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset
from torch_geometric.data import Data

from botgraph_core import EDGE_FEATURES, NODE_FEATURES, WindowGraph
from botgraph_ml.graph_io import load_graph

# Each flow edge is also added reversed so a host aggregates both who it talks to and
# who talks to it; this extra column tells the model which direction a message travels.
EDGE_DIM = len(EDGE_FEATURES) + 1
NODE_DIM = len(NODE_FEATURES)

_WINDOW_START = re.compile(r"^(-?\d+)-")


def window_start_from_id(window_id: str) -> float:
    match = _WINDOW_START.match(window_id)
    if match is None:
        raise ValueError(f"malformed window id {window_id!r}")
    return float(match.group(1))


@dataclass(frozen=True)
class Scaler:
    """Per-feature standardisation fitted on training graphs only."""

    x_mean: np.ndarray
    x_std: np.ndarray
    e_mean: np.ndarray
    e_std: np.ndarray

    @classmethod
    def fit(cls, graphs: Iterable[WindowGraph]) -> Scaler:
        x_sum = np.zeros(NODE_DIM)
        x_sq = np.zeros(NODE_DIM)
        e_sum = np.zeros(len(EDGE_FEATURES))
        e_sq = np.zeros(len(EDGE_FEATURES))
        n_x = n_e = 0
        for g in graphs:
            x = g.x.astype(np.float64)
            e = g.edge_attr.astype(np.float64)
            x_sum += x.sum(0)
            x_sq += (x**2).sum(0)
            e_sum += e.sum(0)
            e_sq += (e**2).sum(0)
            n_x += len(x)
            n_e += len(e)
        if n_x == 0 or n_e == 0:
            raise ValueError("cannot fit a scaler without nodes and edges")

        def stats(total: np.ndarray, sq: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
            mean = total / n
            std = np.sqrt(np.maximum(sq / n - mean**2, 0.0))
            return mean, np.where(std < 1e-6, 1.0, std)  # constant features pass through

        x_mean, x_std = stats(x_sum, x_sq, n_x)
        e_mean, e_std = stats(e_sum, e_sq, n_e)
        return cls(x_mean, x_std, e_mean, e_std)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_features": list(NODE_FEATURES),
            "edge_features": list(EDGE_FEATURES),
            **{k: getattr(self, k).tolist() for k in ("x_mean", "x_std", "e_mean", "e_std")},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scaler:
        if (
            tuple(raw["node_features"]) != NODE_FEATURES
            or tuple(raw["edge_features"]) != EDGE_FEATURES
        ):
            raise ValueError("scaler was fitted on a different feature set")
        return cls(*(np.asarray(raw[k]) for k in ("x_mean", "x_std", "e_mean", "e_std")))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: Path) -> Scaler:
        return cls.from_dict(json.loads(path.read_text()))


def to_data(graph: WindowGraph, scaler: Scaler, scenario: int = -1) -> Data:
    x = (graph.x - scaler.x_mean) / scaler.x_std
    e = (graph.edge_attr - scaler.e_mean) / scaler.e_std
    n_edges = graph.num_edges

    edge_index = np.concatenate([graph.edge_index, graph.edge_index[::-1]], axis=1)
    direction = np.concatenate([np.zeros((n_edges, 1)), np.ones((n_edges, 1))])
    edge_attr = np.concatenate([np.concatenate([e, e]), direction], axis=1)

    return Data(
        x=torch.from_numpy(x.astype(np.float32)),
        edge_index=torch.from_numpy(np.ascontiguousarray(edge_index, dtype=np.int64)),
        edge_attr=torch.from_numpy(edge_attr.astype(np.float32)),
        y=torch.from_numpy(graph.y.astype(np.int64)),
        num_nodes=graph.num_nodes,
        ips=graph.nodes,
        window_id=graph.window_id,
        window_start=torch.tensor([window_start_from_id(graph.window_id)], dtype=torch.float64),
        scenario=torch.tensor([scenario]),
    )


def list_windows(graphs_dir: Path, scenarios: Iterable[int]) -> list[tuple[int, Path]]:
    """All window files for ``scenarios`` that contain at least one labelled host."""
    found: list[tuple[int, Path]] = []
    for sid in scenarios:
        scenario_dir = graphs_dir / f"scenario={sid}"
        if not scenario_dir.is_dir():
            raise SystemExit(
                f"{scenario_dir} missing; run `python -m botgraph_ml.build_graphs ctu13`"
            )
        for path in sorted(scenario_dir.glob("*.npz")):
            with np.load(path, allow_pickle=False) as data:
                if (data["y"] >= 0).any():
                    found.append((sid, path))
    return found


@dataclass(frozen=True)
class LabelCounts:
    pos: int
    neg: int


def fit_scaler(windows: list[tuple[int, Path]]) -> tuple[Scaler, LabelCounts]:
    counts = {"pos": 0, "neg": 0}

    def graphs() -> Iterable[WindowGraph]:
        for _, path in windows:
            g = load_graph(path)
            counts["pos"] += int((g.y == 1).sum())
            counts["neg"] += int((g.y == 0).sum())
            yield g

    scaler = Scaler.fit(graphs())
    return scaler, LabelCounts(**counts)


class WindowDataset(Dataset[Data]):
    """Loads window graphs from disk on demand, so a split never has to fit in memory."""

    def __init__(self, windows: list[tuple[int, Path]], scaler: Scaler) -> None:
        self.windows = windows
        self.scaler = scaler

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, idx: int) -> Data:
        scenario, path = self.windows[idx]
        return to_data(load_graph(path), self.scaler, scenario)


def labelled_rows(batch: Data, scores: np.ndarray) -> pd.DataFrame:
    """One row per labelled node in ``batch``: window_id, window_start, scenario, ip, y, score."""
    y = batch.y.cpu().numpy()
    idx = np.flatnonzero(y >= 0)
    graph_of = batch.batch.cpu().numpy()[idx]
    local = idx - batch.ptr.cpu().numpy()[graph_of]
    return pd.DataFrame(
        {
            "window_id": [batch.window_id[g] for g in graph_of],
            "window_start": batch.window_start.cpu().numpy()[graph_of],
            "scenario": batch.scenario.cpu().numpy()[graph_of],
            "ip": [batch.ips[g][i] for g, i in zip(graph_of, local, strict=True)],
            "y": y[idx],
            "score": scores[idx],
        }
    )
