"""Drift references and the population stability index (PSI).

A reference summarises the distribution of active internal hosts' node features and model
scores (hosts that sent at least one flow in the window, see ``active``):
per-feature decile bins (edges from the reference data itself) and the share of hosts in each
bin. The live detector compares recent windows against two references:

* ``training``  the model's training windows (``drift_reference.json`` in the model bundle):
                "does this network look like what the model learned on?" (IoT-23 did not)
* ``baseline``  the sensor's own first hour, the traffic its threshold was calibrated on:
                "has this network changed since calibration?"

    python -m botgraph_ml.drift --model gatv2      # write the bundle's training reference

PSI < 0.1 is usually read as stable, 0.1-0.25 as a moderate shift and > 0.25 as significant.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from botgraph_core import NODE_FEATURES

REFERENCE_FILE = "drift_reference.json"
# is_internal is constant (1) for the hosts compared; everything else is monitored.
DRIFT_FEATURES: tuple[str, ...] = tuple(f for f in NODE_FEATURES if f != "is_internal")
FEATURE_INDEX = np.array([NODE_FEATURES.index(f) for f in DRIFT_FEATURES])
# Scores use fixed bins: they are probabilities, and the high end is where alerts live.
SCORE_EDGES: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)
EPSILON = 1e-4  # smooths empty bins so PSI stays finite
PSI_MODERATE, PSI_SIGNIFICANT = 0.1, 0.25


def _shares(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    counts = np.bincount(np.searchsorted(edges, values, side="right"), minlength=len(edges) + 1)
    return counts / max(counts.sum(), 1)


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(expected, EPSILON, None)
    a = np.clip(actual, EPSILON, None)
    return float(np.sum((a - e) * np.log(a / e)))


@dataclass
class Reference:
    """Binned distribution of internal-host features (``DRIFT_FEATURES`` order) and scores."""

    edges: list[np.ndarray]
    shares: list[np.ndarray]
    score_shares: np.ndarray
    n_hosts: int

    @classmethod
    def build(cls, features: np.ndarray, scores: np.ndarray, bins: int = 10) -> Reference:
        """``features``: (hosts, len(DRIFT_FEATURES)) raw feature values; ``scores``: (hosts,)."""
        if len(features) == 0:
            raise ValueError("a drift reference needs at least one host")
        quantiles = np.linspace(0, 1, bins + 1)[1:-1]
        edges, shares = [], []
        for col in features.T:
            # Many features are mostly zero: duplicate quantiles collapse into one bin.
            e = np.unique(np.quantile(col, quantiles))
            edges.append(e)
            shares.append(_shares(col, e))
        return cls(edges, shares, _shares(scores, np.array(SCORE_EDGES)), len(features))

    def compare(self, features: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
        """PSI of each feature and of the score distribution against this reference."""
        per_feature = {
            name: round(psi(ref, _shares(col, e)), 4)
            for name, col, e, ref in zip(
                DRIFT_FEATURES, features.T, self.edges, self.shares, strict=True
            )
        }
        top = max(per_feature, key=lambda k: per_feature[k])
        return {
            "features": per_feature,
            "max_feature": top,
            "max_psi": per_feature[top],
            "score_psi": round(psi(self.score_shares, _shares(scores, np.array(SCORE_EDGES))), 4),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": list(DRIFT_FEATURES),
            "edges": [e.tolist() for e in self.edges],
            "shares": [s.tolist() for s in self.shares],
            "score_edges": list(SCORE_EDGES),
            "score_shares": self.score_shares.tolist(),
            "n_hosts": self.n_hosts,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Reference:
        if tuple(raw["features"]) != DRIFT_FEATURES or tuple(raw["score_edges"]) != SCORE_EDGES:
            raise ValueError("drift reference was built for a different feature layout")
        return cls(
            [np.asarray(e, dtype=float) for e in raw["edges"]],
            [np.asarray(s, dtype=float) for s in raw["shares"]],
            np.asarray(raw["score_shares"], dtype=float),
            int(raw["n_hosts"]),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: Path) -> Reference:
        return cls.from_dict(json.loads(path.read_text()))


def internal_rows(x: np.ndarray) -> np.ndarray:
    """Monitored feature columns of the internal hosts in a window's raw node features."""
    internal = x[:, NODE_FEATURES.index("is_internal")] == 1.0
    return np.asarray(x[internal][:, FEATURE_INDEX], dtype=float)


_OUT_FLOWS = DRIFT_FEATURES.index("log_out_flows")


def active(rows: np.ndarray) -> np.ndarray:
    """Mask of hosts that sent at least one flow.

    Only these are compared. An internal address that only *receives* traffic is usually an
    unused address hit by a scan: on CTU-13 scenario 6 they are 35-80% of internal "hosts" per
    window, and a scan burst inside the baseline made PSI jump to 0.42 once the burst left the
    recent windows (0.02 when only active hosts are compared).
    """
    return np.asarray(rows[:, _OUT_FLOWS] > 0)


def build_training_reference(model_name: str, max_windows: int = 3000) -> Path:
    """Score the training windows of a bundle's split and write its drift reference."""
    import torch  # heavy; only this entry point needs the model

    from botgraph_ml.config import load_labels, load_params, repo_path
    from botgraph_ml.gnn.data import list_windows, to_data
    from botgraph_ml.gnn.models import load_bundle, load_metadata
    from botgraph_ml.graph_io import load_graph

    params = load_params()
    bundle = repo_path(params["data"]["models_dir"]) / model_name
    metadata = load_metadata(bundle)
    model, scaler = load_bundle(bundle)
    labels = load_labels(repo_path(params["ctu13"]["labels"]))
    graphs_dir = repo_path(params["data"]["processed_dir"]) / "ctu13" / "graphs"
    windows = list_windows(graphs_dir, labels.split(metadata["split"])["train"])
    step = max(1, len(windows) // max_windows)  # evenly spaced, deterministic sample
    feats, scores = [], []
    for _, path in windows[::step]:
        graph = load_graph(path)
        data = to_data(graph, scaler)
        with torch.no_grad():
            s = torch.sigmoid(model(data.x, data.edge_index, data.edge_attr)).numpy()
        internal = graph.x[:, NODE_FEATURES.index("is_internal")] == 1.0
        rows = internal_rows(graph.x)
        keep = active(rows)
        feats.append(rows[keep])
        scores.append(s[internal][keep])
    reference = Reference.build(np.concatenate(feats), np.concatenate(scores))
    reference.save(bundle / REFERENCE_FILE)
    return bundle / REFERENCE_FILE


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", default="gatv2", help="bundle name under ml/models")
    parser.add_argument("--max-windows", type=int, default=3000)
    args = parser.parse_args(argv)
    path = build_training_reference(args.model, args.max_windows)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
