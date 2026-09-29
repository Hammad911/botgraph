"""Node classifiers: GraphSAGE, E-GraphSAGE (edge-feature aware) and GATv2.

All three share one skeleton so comparisons isolate the message-passing layer:

    input MLP -> L x (conv -> LayerNorm -> ReLU -> dropout, residual) -> MLP head -> logit

The residual path keeps each host's own features, so a GNN starts from what the
tabular baseline sees and only has to learn what the neighbourhood adds.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import torch
import torch.nn.functional as F  # noqa: N812
from torch import Tensor, nn
from torch_geometric.nn import GATv2Conv, MessagePassing, SAGEConv

from botgraph_core import EDGE_FEATURES, NODE_FEATURES
from botgraph_ml.gnn.data import EDGE_DIM, NODE_DIM, Scaler

ModelKind = Literal["graphsage", "e_graphsage", "gatv2"]
MODEL_KINDS: tuple[ModelKind, ...] = ("graphsage", "e_graphsage", "gatv2")


class EdgeSAGEConv(MessagePassing):
    """E-GraphSAGE layer (Lo et al., 2022): neighbour messages include the flow edge's features.

    h_v' = W_out [ W_root h_v  ||  mean_{u -> v} ReLU(W_msg [h_u || e_uv]) ]
    """

    def __init__(self, channels: int, edge_dim: int) -> None:
        super().__init__(aggr="mean")
        self.msg = nn.Linear(channels + edge_dim, channels)
        self.root = nn.Linear(channels, channels)
        self.out = nn.Linear(2 * channels, channels)

    def forward(self, x: Tensor, edge_index: Tensor, edge_attr: Tensor) -> Tensor:
        agg = self.propagate(edge_index, x=x, edge_attr=edge_attr)
        return self.out(torch.cat([self.root(x), agg], dim=-1))  # type: ignore[no-any-return]

    def message(self, x_j: Tensor, edge_attr: Tensor) -> Tensor:
        return F.relu(self.msg(torch.cat([x_j, edge_attr], dim=-1)))


@dataclass(frozen=True)
class ModelConfig:
    kind: ModelKind
    hidden: int = 64
    layers: int = 2
    dropout: float = 0.2
    heads: int = 4  # GATv2 only
    node_dim: int = NODE_DIM
    edge_dim: int = EDGE_DIM

    def __post_init__(self) -> None:
        if self.kind not in MODEL_KINDS:
            raise ValueError(f"unknown model kind {self.kind!r}; choose from {MODEL_KINDS}")
        if self.kind == "gatv2" and self.hidden % self.heads:
            raise ValueError("hidden must be divisible by heads for GATv2")


class NodeClassifier(nn.Module):
    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.cfg = cfg
        h = cfg.hidden
        self.encoder = nn.Sequential(nn.Linear(cfg.node_dim, h), nn.ReLU())
        self.convs = nn.ModuleList(self._conv(cfg) for _ in range(cfg.layers))
        self.norms = nn.ModuleList(nn.LayerNorm(h) for _ in range(cfg.layers))
        self.head = nn.Sequential(
            nn.Linear(h, h), nn.ReLU(), nn.Dropout(cfg.dropout), nn.Linear(h, 1)
        )

    @staticmethod
    def _conv(cfg: ModelConfig) -> nn.Module:
        h = cfg.hidden
        if cfg.kind == "graphsage":
            return SAGEConv(h, h)
        if cfg.kind == "e_graphsage":
            return EdgeSAGEConv(h, cfg.edge_dim)
        return GATv2Conv(h, h // cfg.heads, heads=cfg.heads, edge_dim=cfg.edge_dim)

    def forward(self, x: Tensor, edge_index: Tensor, edge_attr: Tensor | None = None) -> Tensor:
        """Return one raw logit per node (positive = botnet)."""
        h = self.encoder(x)
        for conv, norm in zip(self.convs, self.norms, strict=True):
            if self.cfg.kind == "graphsage":
                out = conv(h, edge_index)
            else:
                out = conv(h, edge_index, edge_attr)
            h = h + F.dropout(F.relu(norm(out)), p=self.cfg.dropout, training=self.training)
        return self.head(h).squeeze(-1)  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- bundles
# A bundle is everything needed to score new traffic: weights, architecture, scaler.


def save_bundle(directory: Path, model: NodeClassifier, scaler: Scaler) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), directory / "model.pt")
    (directory / "config.json").write_text(json.dumps(asdict(model.cfg), indent=2))
    scaler.save(directory / "scaler.json")


def load_bundle(directory: Path, device: str = "cpu") -> tuple[NodeClassifier, Scaler]:
    cfg = ModelConfig(**json.loads((directory / "config.json").read_text()))
    model = NodeClassifier(cfg)
    state = torch.load(directory / "model.pt", map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.eval()
    return model.to(device), Scaler.load(directory / "scaler.json")


def write_metadata(directory: Path, threshold: float, split: str, **extra: Any) -> None:
    """Record what a deployed model needs besides its weights: the decision threshold chosen
    on validation data and the feature layout it was trained with."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 (git from PATH)
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    metadata = {
        "threshold": threshold,
        "split": split,
        "node_features": list(NODE_FEATURES),
        "edge_features": list(EDGE_FEATURES),
        "git_sha": sha,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **extra,
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2))


def load_metadata(directory: Path) -> dict[str, Any]:
    """Bundle metadata; refuses a bundle built for a different feature layout."""
    path = directory / "metadata.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing; re-run `python -m botgraph_ml.gnn.train --eval-only` for this model"
        )
    metadata: dict[str, Any] = json.loads(path.read_text())
    if (
        tuple(metadata["node_features"]) != NODE_FEATURES
        or tuple(metadata["edge_features"]) != EDGE_FEATURES
    ):
        raise ValueError(f"{directory} was trained on a different feature layout; retrain it")
    return metadata
