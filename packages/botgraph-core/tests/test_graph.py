from __future__ import annotations

import math

import numpy as np
import pytest

from botgraph_core.graph import EDGE_FEATURES, NODE_FEATURES, build_window_graph

N = {name: i for i, name in enumerate(NODE_FEATURES)}
E = {name: i for i, name in enumerate(EDGE_FEATURES)}


def node_row(graph, ip):  # type: ignore[no-untyped-def]
    return graph.x[graph.nodes.index(ip)]


def test_shapes_and_edge_aggregation(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        {"ts": 0.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"},
        {"ts": 5.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"},
        {"ts": 9.0, "src_ip": "10.0.0.2", "dst_ip": "10.0.0.3"},
    )
    g = build_window_graph(flows, "w0")

    assert g.num_nodes == 3
    assert g.num_edges == 2  # the two 10.0.0.1 -> 10.0.0.2 flows collapse into one edge
    assert g.x.shape == (3, len(NODE_FEATURES)) and g.x.dtype == np.float32
    assert g.edge_attr.shape == (2, len(EDGE_FEATURES))
    assert g.edge_index.dtype == np.int64

    a, b = g.nodes.index("10.0.0.1"), g.nodes.index("10.0.0.2")
    edge = [i for i in range(g.num_edges) if tuple(g.edge_index[:, i]) == (a, b)]
    assert len(edge) == 1
    assert g.edge_attr[edge[0], E["log_flows"]] == pytest.approx(math.log1p(2))


def test_beaconing_host_has_high_periodicity(make_flows):  # type: ignore[no-untyped-def]
    beacon = [
        {"ts": 60.0 * i, "src_ip": "10.0.0.5", "dst_ip": "203.0.113.7", "dst_port": 6667}
        for i in range(5)
    ]
    jitter = [
        {"ts": t, "src_ip": "10.0.0.6", "dst_ip": "198.51.100.1"}
        for t in (0.0, 3.0, 90.0, 95.0, 280.0)
    ]
    g = build_window_graph(make_flows(*beacon, *jitter), "w0")

    bot, human = node_row(g, "10.0.0.5"), node_row(g, "10.0.0.6")
    assert bot[N["periodicity"]] == pytest.approx(1.0)
    assert human[N["periodicity"]] < 0.7
    assert bot[N["frac_irc"]] == pytest.approx(1.0)
    assert bot[N["is_internal"]] == 1.0
    assert node_row(g, "203.0.113.7")[N["is_internal"]] == 0.0


def test_periodicity_requires_minimum_flows(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        {"ts": 0.0, "src_ip": "10.0.0.5", "dst_ip": "203.0.113.7"},
        {"ts": 60.0, "src_ip": "10.0.0.5", "dst_ip": "203.0.113.7"},
    )
    g = build_window_graph(flows, "w0")
    assert node_row(g, "10.0.0.5")[N["periodicity"]] == 0.0


def test_scanner_features(make_flows):  # type: ignore[no-untyped-def]
    scan = [
        {
            "ts": float(i),
            "src_ip": "10.0.0.9",
            "dst_ip": f"10.0.1.{i}",
            "dst_port": 22 + i,
            "dst_bytes": 0,
        }
        for i in range(20)
    ]
    g = build_window_graph(make_flows(*scan), "w0")
    row = node_row(g, "10.0.0.9")

    assert row[N["log_out_degree"]] == pytest.approx(math.log1p(20))
    assert row[N["log_unique_dst_ports"]] == pytest.approx(math.log1p(20))
    assert row[N["failed_ratio"]] == pytest.approx(1.0)
    assert row[N["dst_entropy"]] == pytest.approx(1.0)  # perfectly uniform fan-out


def test_bytes_sent_and_received_by_role(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        {"ts": 0.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2", "src_bytes": 100, "dst_bytes": 900}
    )
    g = build_window_graph(flows, "w0")
    client, server = node_row(g, "10.0.0.1"), node_row(g, "10.0.0.2")

    assert client[N["log_bytes_sent"]] == pytest.approx(math.log1p(100))
    assert client[N["log_bytes_recv"]] == pytest.approx(math.log1p(900))
    assert server[N["log_bytes_sent"]] == pytest.approx(math.log1p(900))
    assert client[N["sent_ratio"]] == pytest.approx(0.1)


def test_host_labels(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        {"ts": 0.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"},
        {"ts": 1.0, "src_ip": "10.0.0.3", "dst_ip": "10.0.0.2"},
    )
    g = build_window_graph(flows, "w0", host_labels={"10.0.0.1": "botnet", "10.0.0.2": "benign"})
    labels = dict(zip(g.nodes, g.y.tolist(), strict=True))
    assert labels == {"10.0.0.1": 1, "10.0.0.2": 0, "10.0.0.3": -1}


def test_features_are_finite(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        {
            "ts": 0.0,
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "dst_port": None,
            "proto": "icmp",
            "duration": 0.0,
        },
        {
            "ts": 0.0,
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "src_bytes": 0,
            "dst_bytes": 0,
            "pkts": 0,
        },
    )
    g = build_window_graph(flows, "w0")
    assert np.isfinite(g.x).all() and np.isfinite(g.edge_attr).all()


def test_empty_window_rejected(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows({"ts": 0.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"}).iloc[0:0]
    with pytest.raises(ValueError):
        build_window_graph(flows, "w0")
