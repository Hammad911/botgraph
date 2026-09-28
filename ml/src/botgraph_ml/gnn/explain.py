"""Explain why a host was flagged: its most influential flows (edges) and features.

    python -m botgraph_ml.gnn.explain --model e_graphsage \
        --window ml/data/processed/ctu13/graphs/scenario=10/<window_id>.npz --top 3

Uses GNNExplainer, which learns soft masks over edges and node features that preserve
the model's prediction for the target host. The output is what the analyst console shows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch_geometric.data import Data
from torch_geometric.explain import Explainer, GNNExplainer

from botgraph_core import EDGE_FEATURES, NODE_FEATURES, WindowGraph
from botgraph_ml.config import load_params, repo_path
from botgraph_ml.gnn.data import to_data
from botgraph_ml.gnn.models import MODEL_KINDS, NodeClassifier, load_bundle
from botgraph_ml.graph_io import load_graph


def explain_node(
    model: NodeClassifier,
    graph: WindowGraph,
    data: Data,
    node: int,
    epochs: int = 100,
    top_k: int = 5,
) -> dict[str, Any]:
    """Explain one host. ``data`` must be ``to_data(graph, scaler)``; ``graph`` supplies the
    unscaled feature values shown to analysts."""
    explainer = Explainer(
        model=model,
        algorithm=GNNExplainer(epochs=epochs),
        explanation_type="model",
        node_mask_type="attributes",
        edge_mask_type="object",
        model_config={"mode": "binary_classification", "task_level": "node", "return_type": "raw"},
    )
    explanation = explainer(data.x, data.edge_index, index=node, edge_attr=data.edge_attr)

    with torch.no_grad():
        score = float(torch.sigmoid(model(data.x, data.edge_index, data.edge_attr)[node]))

    # to_data stores each flow twice (forward half, then reversed half): fold both halves
    # back onto the original flow edge so importance is reported per real flow.
    edge_mask = explanation.edge_mask.detach().cpu().numpy()
    n_flows = graph.num_edges
    per_flow = np.maximum(edge_mask[:n_flows], edge_mask[n_flows:])
    top_edges = [
        {
            "src": graph.nodes[int(graph.edge_index[0, i])],
            "dst": graph.nodes[int(graph.edge_index[1, i])],
            "importance": round(float(per_flow[i]), 4),
            "features": {
                name: round(float(v), 3)
                for name, v in zip(EDGE_FEATURES, graph.edge_attr[i], strict=True)
            },
        }
        for i in np.argsort(-per_flow)[:top_k]
        if per_flow[i] > 0
    ]

    feature_mask = explanation.node_mask[node].detach().cpu().numpy()
    top_features = sorted(
        (
            {"feature": name, "importance": round(float(m), 4), "value": round(float(v), 3)}
            for name, m, v in zip(NODE_FEATURES, feature_mask, graph.x[node], strict=True)
        ),
        key=lambda item: -float(item["importance"]),
    )[:top_k]

    return {
        "ip": graph.nodes[node],
        "score": round(score, 4),
        "top_flows": top_edges,
        "top_features": top_features,
    }


def explain_top_hosts(
    model: NodeClassifier, graph: WindowGraph, data: Data, top: int, epochs: int
) -> list[dict[str, Any]]:
    with torch.no_grad():
        scores = torch.sigmoid(model(data.x, data.edge_index, data.edge_attr)).numpy()
    return [explain_node(model, graph, data, int(i), epochs) for i in np.argsort(-scores)[:top]]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", choices=MODEL_KINDS, default="e_graphsage")
    parser.add_argument("--window", type=Path, required=True, help="window graph .npz")
    parser.add_argument("--top", type=int, default=3, help="explain the N highest-scoring hosts")
    parser.add_argument("--epochs", type=int, default=100)
    args = parser.parse_args(argv)

    params = load_params()
    model, scaler = load_bundle(repo_path(params["data"]["models_dir"]) / args.model)
    graph = load_graph(args.window)
    explanations = explain_top_hosts(model, graph, to_data(graph, scaler), args.top, args.epochs)
    print(json.dumps({"window_id": graph.window_id, "hosts": explanations}, indent=2))


if __name__ == "__main__":
    main()
