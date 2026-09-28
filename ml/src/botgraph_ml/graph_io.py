"""Persist window graphs as compressed ``.npz`` files (no pickle)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from botgraph_core import EDGE_FEATURES, NODE_FEATURES, WindowGraph


def save_graph(path: Path, graph: WindowGraph) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        window_id=np.array(graph.window_id),
        nodes=np.array(graph.nodes, dtype=str),
        x=graph.x,
        edge_index=graph.edge_index,
        edge_attr=graph.edge_attr,
        y=graph.y,
        node_features=np.array(NODE_FEATURES),
        edge_features=np.array(EDGE_FEATURES),
    )


def load_graph(path: Path) -> WindowGraph:
    with np.load(path, allow_pickle=False) as data:
        if (
            tuple(data["node_features"]) != NODE_FEATURES
            or tuple(data["edge_features"]) != EDGE_FEATURES
        ):
            raise ValueError(
                f"{path} was built with a different feature set; rebuild graphs "
                "(dvc repro build_graphs_ctu13)"
            )
        return WindowGraph(
            window_id=str(data["window_id"]),
            nodes=data["nodes"].tolist(),
            x=data["x"],
            edge_index=data["edge_index"],
            edge_attr=data["edge_attr"],
            y=data["y"],
        )
