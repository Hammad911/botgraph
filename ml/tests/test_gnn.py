"""GNN tests on synthetic window graphs where the bot signal lives only on edges."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch
from torch_geometric.loader import DataLoader

from botgraph_core import EDGE_FEATURES, NODE_FEATURES, WindowGraph
from botgraph_ml.compare import render
from botgraph_ml.gnn.data import (
    EDGE_DIM,
    Scaler,
    labelled_rows,
    to_data,
    window_start_from_id,
)
from botgraph_ml.gnn.explain import explain_node
from botgraph_ml.gnn.models import (
    MODEL_KINDS,
    ModelConfig,
    NodeClassifier,
    load_bundle,
    save_bundle,
)
from botgraph_ml.gnn.train import fit, predict
from botgraph_ml.tracking import Tracker, _flatten

PERIODICITY = EDGE_FEATURES.index("periodicity")
CFG = {
    "hidden": 32,
    "layers": 2,
    "dropout": 0.0,
    "heads": 4,
    "lr": 0.01,
    "weight_decay": 0.0,
    "epochs": 40,
    "patience": 40,
    "batch_graphs": 4,
    "seed": 0,
}


def synthetic_graph(rng: np.random.Generator, start: int) -> WindowGraph:
    """3 bots + 6 benign internal hosts + 10 external hosts + 1 C2 server.

    Node features are pure noise. Every internal host talks to random external hosts with
    irregular timing; bots additionally beacon to the C2 with periodicity 1.0.
    """
    bots = [f"10.0.0.{i}" for i in range(3)]
    benign = [f"10.0.1.{i}" for i in range(6)]
    external = [f"198.51.100.{i}" for i in range(10)]
    nodes = [*bots, *benign, *external, "203.0.113.66"]
    c2 = len(nodes) - 1

    src, dst, attrs = [], [], []
    for h in range(len(bots) + len(benign)):
        for ext in rng.choice(len(external), size=3, replace=False):
            src.append(h)
            dst.append(len(bots) + len(benign) + int(ext))
            a = rng.normal(0, 1, len(EDGE_FEATURES))
            a[PERIODICITY] = rng.uniform(0.0, 0.3)
            attrs.append(a)
    for b in range(len(bots)):
        src.append(b)
        dst.append(c2)
        a = rng.normal(0, 1, len(EDGE_FEATURES))
        a[PERIODICITY] = 1.0
        attrs.append(a)

    y = np.full(len(nodes), -1, dtype=np.int8)
    y[: len(bots)] = 1
    y[len(bots) : len(bots) + len(benign)] = 0
    return WindowGraph(
        window_id=f"{start}-{start + 300}",
        nodes=nodes,
        x=rng.normal(0, 1, (len(nodes), len(NODE_FEATURES))).astype(np.float32),
        edge_index=np.array([src, dst], dtype=np.int64),
        edge_attr=np.array(attrs, dtype=np.float32),
        y=y,
    )


@pytest.fixture(scope="module")
def splits():  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(0)
    graphs = {
        part: [synthetic_graph(rng, 60 * i) for i in range(n)]
        for part, n in (("train", 24), ("val", 8), ("test", 8))
    }
    scaler = Scaler.fit(graphs["train"])
    data = {part: [to_data(g, scaler, scenario=1) for g in gs] for part, gs in graphs.items()}
    return graphs, scaler, data


def test_to_data_adds_reverse_edges_with_direction_flag(splits) -> None:  # type: ignore[no-untyped-def]
    graphs, scaler, _ = splits
    g = graphs["train"][0]
    d = to_data(g, scaler)

    assert d.num_nodes == g.num_nodes
    assert d.edge_index.shape == (2, 2 * g.num_edges)
    assert d.edge_attr.shape == (2 * g.num_edges, EDGE_DIM)
    assert torch.equal(d.edge_index[:, g.num_edges :], d.edge_index[[1, 0], : g.num_edges])
    assert d.edge_attr[: g.num_edges, -1].sum() == 0
    assert d.edge_attr[g.num_edges :, -1].eq(1).all()


def test_window_start_parsing() -> None:
    assert window_start_from_id("1312969500-1312969800") == 1312969500.0
    assert window_start_from_id("-240-60") == -240.0
    with pytest.raises(ValueError):
        window_start_from_id("garbage")


def test_scaler_round_trip_and_feature_guard(splits) -> None:  # type: ignore[no-untyped-def]
    _, scaler, _ = splits
    restored = Scaler.from_dict(scaler.to_dict())
    np.testing.assert_allclose(restored.x_mean, scaler.x_mean)
    bad = {**scaler.to_dict(), "node_features": ["something_else"]}
    with pytest.raises(ValueError, match="different feature set"):
        Scaler.from_dict(bad)


def test_labelled_rows_maps_nodes_back_to_ips(splits) -> None:  # type: ignore[no-untyped-def]
    graphs, _, data = splits
    batch = next(iter(DataLoader(data["train"][:2], batch_size=2)))
    rows = labelled_rows(batch, np.arange(batch.num_nodes, dtype=float))

    assert len(rows) == 2 * 9  # 3 bots + 6 benign per graph
    second = rows[rows["window_id"] == graphs["train"][1].window_id]
    assert second["ip"].iloc[0] == "10.0.0.0"
    assert second["score"].iloc[0] == graphs["train"][0].num_nodes  # offset by the first graph
    assert set(rows["y"]) == {0, 1}


@pytest.mark.parametrize("kind", MODEL_KINDS)
def test_forward_shapes(kind: str, splits) -> None:  # type: ignore[no-untyped-def]
    _, _, data = splits
    d = data["train"][0]
    model = NodeClassifier(ModelConfig(kind=kind, hidden=32))  # type: ignore[arg-type]
    assert model(d.x, d.edge_index, d.edge_attr).shape == (d.num_nodes,)


def test_e_graphsage_learns_edge_only_signal(splits) -> None:  # type: ignore[no-untyped-def]
    _, _, data = splits
    result = fit(
        data["train"], data["val"], {**CFG, "model": "e_graphsage"}, torch.device("cpu"), 2.0
    )
    assert result.best_val_ap > 0.95

    test = predict(result.model, DataLoader(data["test"], batch_size=4), torch.device("cpu"))
    bots, benign = test[test["y"] == 1]["score"], test[test["y"] == 0]["score"]
    assert bots.min() > benign.max()  # perfectly separated on unseen windows


def test_bundle_round_trip(tmp_path: Path, splits) -> None:  # type: ignore[no-untyped-def]
    _, scaler, data = splits
    model = NodeClassifier(ModelConfig(kind="e_graphsage", hidden=32)).eval()
    save_bundle(tmp_path / "bundle", model, scaler)
    loaded, loaded_scaler = load_bundle(tmp_path / "bundle")

    d = data["test"][0]
    with torch.no_grad():
        assert torch.allclose(
            model(d.x, d.edge_index, d.edge_attr), loaded(d.x, d.edge_index, d.edge_attr)
        )
    np.testing.assert_allclose(loaded_scaler.e_std, scaler.e_std)


def test_explain_node_reports_raw_values(splits) -> None:  # type: ignore[no-untyped-def]
    graphs, _, data = splits
    torch.manual_seed(0)
    model = NodeClassifier(ModelConfig(kind="e_graphsage", hidden=32)).eval()
    out = explain_node(model, graphs["test"][0], data["test"][0], node=0, steps=16, top_k=3)

    assert out["ip"] == "10.0.0.0"
    assert 0.0 <= out["score"] <= 1.0
    assert 1 <= len(out["top_features"]) <= 3
    assert all(f["importance"] > 0 for f in out["top_features"])  # only what pushed it up
    assert {f["feature"] for f in out["top_features"]} <= set(NODE_FEATURES)
    for flow in out["top_flows"]:
        assert set(flow["features"]) == set(EDGE_FEATURES)
        # raw (unscaled) periodicity is always in [0, 1] in the synthetic data
        assert 0.0 <= flow["features"]["periodicity"] <= 1.0


def test_explanations_survive_a_saturated_score(splits) -> None:  # type: ignore[no-untyped-def]
    """Regression test: real alerts score ~1.000 (logit ~47), where GNNExplainer returned
    all-zero masks. Integrated Gradients on the logit must still attribute, and satisfy
    completeness (attributions sum to logit - baseline logit)."""
    from botgraph_ml.gnn.explain import integrated_gradients

    graphs, _, data = splits
    torch.manual_seed(0)
    model = NodeClassifier(ModelConfig(kind="gatv2", hidden=32)).eval()
    d = data["test"][0]
    with torch.no_grad():  # push node 0's output deep into saturation, like the trained model
        model.head[-1].weight.mul_(200.0)
        if model(d.x, d.edge_index, d.edge_attr)[0] < 0:
            model.head[-1].weight.neg_()
            model.head[-1].bias.neg_()
        assert torch.sigmoid(model(d.x, d.edge_index, d.edge_attr)[0]).item() > 0.999999

    attr_x, attr_e, logit, base = integrated_gradients(model, d, node=0, steps=64)
    total = float(attr_x.sum() + attr_e.sum())
    assert total == pytest.approx(logit - base, rel=0.05, abs=0.5)
    assert float(attr_x[0].abs().sum()) > 0  # the host's own features get non-zero credit

    out = explain_node(model, graphs["test"][0], d, node=0, steps=32)
    assert out["score"] > 0.999 and out["top_features"]


def _report(model: str, family: str, pr_auc: float) -> dict:  # type: ignore[type-arg]
    window = {
        "precision": 0.9,
        "recall": 0.8,
        "f1": 0.85,
        "pr_auc": pr_auc,
        "fpr_at_95_recall": None,
    }
    alerts = {
        "bots_alerted": 1,
        "bots_total": 1,
        "benign_alerted": 0,
        "benign_total": 6,
        "median_time_to_alert_s": 120.0,
    }
    scenario = {"family": family, "window": window, "alerts": alerts}
    return {
        "model": model,
        "test": {"alert_rule": {"k": 3, "n": 5}, "window": window, "per_scenario": {"7": scenario}},
    }


def test_compare_renders_main_split_and_cv() -> None:
    text = render(
        {"e_graphsage": _report("e_graphsage", "Sogou", 0.9)},
        cv={
            "e_graphsage": {
                "Sogou": _report("e_graphsage", "Sogou", 0.9),
                "Menti": _report("e_graphsage", "Menti", 0.7),
            }
        },
    )
    assert "| E-GraphSAGE | 0.900 | 0.800 | 0.850 | 0.900 | n/a |" in text
    assert "flagged in 3 of the last 5 windows" in text
    assert "| E-GraphSAGE | Sogou | 1/1 | 0/6 | 2 min |" in text
    # two folds: mean/std of PR-AUC (0.9, 0.7) and summed alert counts
    assert "| E-GraphSAGE | Menti | 0.700 | n/a | 1/1 | 0/6 | 2 min |" in text
    assert "| E-GraphSAGE | 2 | 0.800 ± 0.141 | 2/2 | 0/12 |" in text


def test_tracker_is_noop_without_server() -> None:
    tracker = Tracker(active=False)
    tracker.log_params({"a": 1})
    tracker.log_metrics({"test": {"f1": 0.5}})
    assert _flatten({"test": {"f1": 0.5, "name": "x"}, "n": 3}) == {"test.f1": 0.5, "n": 3}
