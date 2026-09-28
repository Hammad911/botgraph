from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from botgraph_core import build_window_graph, validate_frame
from botgraph_ml.graph_io import load_graph, save_graph


def _graph():  # type: ignore[no-untyped-def]
    flows = validate_frame(
        pd.DataFrame(
            {
                "ts": [0.0, 1.0],
                "duration": [1.0, 1.0],
                "proto": ["tcp", "udp"],
                "src_ip": ["10.0.0.1", "10.0.0.2"],
                "src_port": [1000, 1001],
                "dst_ip": ["10.0.0.2", "10.0.0.3"],
                "dst_port": [80, 53],
                "src_bytes": [10, 20],
                "dst_bytes": [30, 0],
                "pkts": [2, 1],
                "label": ["unknown", "unknown"],
                "sensor_id": ["t", "t"],
            }
        )
    )
    return build_window_graph(flows, "0-300", host_labels={"10.0.0.1": "botnet"})


def test_round_trip(tmp_path: Path) -> None:
    g = _graph()
    save_graph(tmp_path / "g.npz", g)
    loaded = load_graph(tmp_path / "g.npz")

    assert loaded.window_id == g.window_id
    assert loaded.nodes == g.nodes
    for field in ("x", "edge_index", "edge_attr", "y"):
        np.testing.assert_array_equal(getattr(loaded, field), getattr(g, field))


def test_stale_feature_set_rejected(tmp_path: Path) -> None:
    g = _graph()
    np.savez_compressed(
        tmp_path / "old.npz",
        window_id=np.array(g.window_id),
        nodes=np.array(g.nodes),
        x=g.x,
        edge_index=g.edge_index,
        edge_attr=g.edge_attr,
        y=g.y,
        node_features=np.array(["old_feature"]),
        edge_features=np.array(["old"]),
    )
    with pytest.raises(ValueError, match="different feature set"):
        load_graph(tmp_path / "old.npz")
