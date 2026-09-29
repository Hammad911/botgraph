from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch
from stream_fixtures import INTERNAL, synthetic_flows

from botgraph_core import GraphConfig, build_window_graph
from botgraph_ml.gnn.data import Scaler
from botgraph_ml.gnn.models import ModelConfig, NodeClassifier, save_bundle, write_metadata


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
