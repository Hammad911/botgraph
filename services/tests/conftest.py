from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from botgraph_core import GraphConfig, build_window_graph, validate_frame
from botgraph_ml.gnn.data import Scaler
from botgraph_ml.gnn.models import ModelConfig, NodeClassifier, save_bundle, write_metadata

INTERNAL = ("10.0.0.0/8",)


def synthetic_flows(
    rng: np.random.Generator, minutes: int = 30, start: float = 1.7e9
) -> pd.DataFrame:
    """A small network: 5 internal hosts browsing, one of them beaconing every 60 s."""
    rows = []
    for t in range(0, minutes * 60, 7):
        src = f"10.0.0.{rng.integers(1, 6)}"
        rows.append(
            {
                "ts": start + t + rng.random(),
                "src_ip": src,
                "dst_ip": f"93.184.216.{rng.integers(1, 30)}",
            }
        )
    for t in range(0, minutes * 60, 60):
        rows.append(
            {"ts": start + t, "src_ip": "10.0.0.66", "dst_ip": "203.0.113.9", "dst_port": 6667}
        )
    frame = pd.DataFrame(rows).sort_values("ts")
    defaults = {
        "duration": 1.0,
        "proto": "tcp",
        "src_port": 40000,
        "dst_port": 443,
        "src_bytes": 300,
        "dst_bytes": 900,
        "pkts": 6,
        "label": "unknown",
        "sensor_id": "lab",
    }
    for col, value in defaults.items():
        if col not in frame:
            frame[col] = value
        else:
            frame[col] = frame[col].fillna(value)
    return validate_frame(frame)


@pytest.fixture(scope="session")
def bundle_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tiny, randomly initialised GATv2 saved as a real bundle (plumbing, not accuracy)."""
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    flows = synthetic_flows(rng, minutes=10)
    graph = build_window_graph(flows, "0-300", GraphConfig(internal_nets=INTERNAL))
    directory = tmp_path_factory.mktemp("bundle")
    save_bundle(
        directory, NodeClassifier(ModelConfig(kind="gatv2", hidden=16)).eval(), Scaler.fit([graph])
    )
    write_metadata(directory, threshold=0.5, split="test", model_kind="gatv2")
    return directory
