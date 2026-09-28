"""Turn prepared flow tables into window graphs plus a labelled node table.

    python -m botgraph_ml.build_graphs ctu13

Outputs (per scenario):
    <processed>/ctu13/graphs/scenario=<N>/<window_id>.npz   full graphs, for the GNN
    <processed>/ctu13/nodes/scenario=<N>.parquet             labelled nodes only, for tabular models
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from botgraph_core import (
    NODE_FEATURES,
    GraphConfig,
    Label,
    WindowSpec,
    build_window_graph,
    sliding_windows,
)
from botgraph_ml.config import graph_config, load_labels, load_params, repo_path, window_spec
from botgraph_ml.graph_io import save_graph


def build_scenario(
    flows: pd.DataFrame,
    graphs_dir: Path,
    host_labels: Mapping[str, Label],
    spec: WindowSpec,
    config: GraphConfig,
    min_flows: int,
) -> pd.DataFrame:
    """Write one ``.npz`` per window and return the labelled node rows across all windows."""
    rows: list[pd.DataFrame] = []
    for window in sliding_windows(flows, spec, min_flows=min_flows):
        graph = build_window_graph(window.flows, window.window_id, config, host_labels)
        save_graph(graphs_dir / f"{window.window_id}.npz", graph)

        labelled = graph.y >= 0
        if not labelled.any():
            continue
        part = pd.DataFrame(graph.x[labelled], columns=list(NODE_FEATURES))
        part.insert(0, "y", graph.y[labelled].astype("int8"))
        part.insert(0, "ip", [ip for ip, keep in zip(graph.nodes, labelled, strict=True) if keep])
        part.insert(0, "window_start", window.start)
        part.insert(0, "window_id", window.window_id)
        rows.append(part)

    if not rows:
        return pd.DataFrame(columns=["window_id", "window_start", "ip", "y", *NODE_FEATURES])
    return pd.concat(rows, ignore_index=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("dataset", choices=["ctu13"])
    parser.add_argument("--scenarios", type=int, nargs="*", help="only these scenario ids")
    args = parser.parse_args(argv)

    params = load_params()
    processed = repo_path(params["data"]["processed_dir"]) / args.dataset
    labels = load_labels(repo_path(params["ctu13"]["labels"]))
    spec = window_spec(params)
    config = graph_config(params, args.dataset)
    min_flows = int(params["window"]["min_flows"])

    wanted = set(args.scenarios or labels.scenarios)
    for sid in tqdm(sorted(wanted), desc="scenarios"):
        flows_path = processed / "flows" / f"scenario={sid}.parquet"
        if not flows_path.exists():
            raise SystemExit(
                f"{flows_path} missing; run `python -m botgraph_ml.prepare ctu13` first"
            )
        nodes = build_scenario(
            pd.read_parquet(flows_path),
            processed / "graphs" / f"scenario={sid}",
            labels.scenarios[sid].host_labels(),
            spec,
            config,
            min_flows,
        )
        nodes_path = processed / "nodes" / f"scenario={sid}.parquet"
        nodes_path.parent.mkdir(parents=True, exist_ok=True)
        nodes.to_parquet(nodes_path, index=False)
        bots = int((nodes["y"] == 1).sum())
        tqdm.write(
            f"scenario {sid}: {nodes['window_id'].nunique()} windows, {bots} bot rows, "
            f"{len(nodes) - bots} benign rows"
        )


if __name__ == "__main__":
    main()
