"""Explain why a host was flagged: its most influential flows (edges) and features.

    python -m botgraph_ml.gnn.explain --model e_graphsage \
        --window ml/data/processed/ctu13/graphs/scenario=10/<window_id>.npz --top 3

Uses Integrated Gradients on the host's raw logit: how much each of its own features and
each flow moved the score away from an "average host" baseline. The output is what the
analyst console shows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch_geometric.data import Data

from botgraph_core import EDGE_FEATURES, NODE_FEATURES, WindowGraph
from botgraph_ml.config import load_params, repo_path
from botgraph_ml.gnn.data import to_data
from botgraph_ml.gnn.models import MODEL_KINDS, NodeClassifier, load_bundle
from botgraph_ml.graph_io import load_graph


def integrated_gradients(
    model: NodeClassifier, data: Data, node: int, steps: int = 32
) -> tuple[torch.Tensor, torch.Tensor, float, float]:
    """Integrated Gradients of ``node``'s raw logit w.r.t. node and edge features.

    Baseline: an average host and flow (zero in the scaled feature space) on the same
    graph, with each edge's direction flag kept (structure, not a measurement). Returns
    (node attributions, edge attributions, logit, baseline logit); by the completeness
    property the attributions sum to ``logit - baseline logit``.

    The logit is used, not the probability: flagged hosts typically score ~1.000, where
    the sigmoid is flat and probability-based methods (GNNExplainer) get no gradient.
    """
    model.eval()
    x, e = data.x, data.edge_attr
    x0 = torch.zeros_like(x)
    e0 = torch.zeros_like(e)
    e0[:, -1] = e[:, -1]  # direction flag is structure: keep it in the baseline
    grad_x = torch.zeros_like(x)
    grad_e = torch.zeros_like(e)
    for alpha in (torch.arange(steps, dtype=x.dtype) + 0.5) / steps:  # midpoint rule
        xi = (x0 + alpha * (x - x0)).requires_grad_(True)
        ei = (e0 + alpha * (e - e0)).requires_grad_(True)
        out = model(xi, data.edge_index, ei)[node]
        gx, ge = torch.autograd.grad(out, (xi, ei))
        grad_x += gx
        grad_e += ge
    with torch.no_grad():
        logit = float(model(x, data.edge_index, e)[node])
        base = float(model(x0, data.edge_index, e0)[node])
    return (x - x0) * grad_x / steps, (e - e0) * grad_e / steps, logit, base


def explain_node(
    model: NodeClassifier,
    graph: WindowGraph,
    data: Data,
    node: int,
    steps: int = 32,
    top_k: int = 5,
) -> dict[str, Any]:
    """Explain one host. ``data`` must be ``to_data(graph, scaler)``; ``graph`` supplies the
    unscaled feature values shown to analysts.

    ``importance`` is the contribution to the host's logit (positive = pushed it towards
    "bot"); only positive contributions are listed, since the question is why it was flagged.
    """
    attr_x, attr_e, logit, base = integrated_gradients(model, data, node, steps)
    own = attr_x[node].detach().numpy()

    # to_data stores each flow twice (forward half, then reversed half): add both halves'
    # contributions back onto the original flow edge.
    per_edge = attr_e[:, :-1].sum(dim=1).detach().numpy()
    n_flows = graph.num_edges
    per_flow = per_edge[:n_flows] + per_edge[n_flows:]
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
    top_features = [
        {
            "feature": NODE_FEATURES[i],
            "importance": round(float(own[i]), 4),
            "value": round(float(graph.x[node, i]), 3),
        }
        for i in np.argsort(-own)[:top_k]
        if own[i] > 0
    ]
    return {
        "ip": graph.nodes[node],
        "score": round(float(torch.sigmoid(torch.tensor(logit))), 4),
        "method": "integrated_gradients",
        "logit": round(logit, 3),
        "baseline_logit": round(base, 3),
        "top_flows": top_edges,
        "top_features": top_features,
    }


def explain_top_hosts(
    model: NodeClassifier, graph: WindowGraph, data: Data, top: int, steps: int
) -> list[dict[str, Any]]:
    with torch.no_grad():
        scores = torch.sigmoid(model(data.x, data.edge_index, data.edge_attr)).numpy()
    return [explain_node(model, graph, data, int(i), steps) for i in np.argsort(-scores)[:top]]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", choices=MODEL_KINDS, default="e_graphsage")
    parser.add_argument("--window", type=Path, required=True, help="window graph .npz")
    parser.add_argument("--top", type=int, default=3, help="explain the N highest-scoring hosts")
    parser.add_argument("--steps", type=int, default=32, help="Integrated Gradients steps")
    args = parser.parse_args(argv)

    params = load_params()
    model, scaler = load_bundle(repo_path(params["data"]["models_dir"]) / args.model)
    graph = load_graph(args.window)
    explanations = explain_top_hosts(model, graph, to_data(graph, scaler), args.top, args.steps)
    print(json.dumps({"window_id": graph.window_id, "hosts": explanations}, indent=2))


if __name__ == "__main__":
    main()
