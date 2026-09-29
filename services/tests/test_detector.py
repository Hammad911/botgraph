from __future__ import annotations

import json

import numpy as np
import pytest
import torch
from stream_fixtures import INTERNAL, synthetic_flows

from botgraph_core import GraphConfig, WindowSpec, build_window_graph, sliding_windows
from botgraph_ml.gnn.data import to_data
from botgraph_ml.gnn.models import load_bundle
from botgraph_stream.alerts import AlertEngine
from botgraph_stream.bus import DETECTIONS, DLQ, FLOWS, FLOWS_RAW, LocalBus
from botgraph_stream.detector import Detector, ego_graph
from botgraph_stream.ingest import IngestStats, ingest_step
from botgraph_stream.service import DetectorService

SPEC = WindowSpec(size_s=300, hop_s=60)
CONFIG = GraphConfig(internal_nets=INTERNAL)


def _records(frame):  # type: ignore[no-untyped-def]
    return json.loads(frame.to_json(orient="records"))


def test_stream_scores_equal_offline_scores(bundle_dir) -> None:  # type: ignore[no-untyped-def]
    flows = synthetic_flows(np.random.default_rng(1), minutes=20)
    det = Detector(bundle_dir, CONFIG, SPEC, min_flows=1, allowed_lateness_s=0.0)
    streamed = []
    for idx in np.array_split(np.arange(len(flows)), 17):
        streamed += det.process("lab", flows.iloc[idx].reset_index(drop=True))
    streamed += det.flush()

    model, scaler = load_bundle(bundle_dir)
    offline = []
    for w in sliding_windows(flows, SPEC, 1):
        g = build_window_graph(w.flows, w.window_id, CONFIG)
        d = to_data(g, scaler)
        with torch.no_grad():
            s = torch.sigmoid(model(d.x, d.edge_index, d.edge_attr)).numpy()
        offline.append(
            {ip: float(v) for ip, v in zip(g.nodes, s, strict=True) if ip.startswith("10.")}
        )

    assert [d["window_start"] for d in streamed] == [
        w.start for w in sliding_windows(flows, SPEC, 1)
    ]
    for det_msg, expected in zip(streamed, offline, strict=True):
        got = {h["ip"]: h["score"] for h in det_msg["hosts"]}
        assert got.keys() == expected.keys()  # internal hosts only
        assert all(got[ip] == pytest.approx(expected[ip], abs=1e-5) for ip in got)


def test_bundle_feature_layout_is_checked(bundle_dir, tmp_path) -> None:  # type: ignore[no-untyped-def]
    for name in ("model.pt", "config.json", "scaler.json"):
        (tmp_path / name).write_bytes((bundle_dir / name).read_bytes())
    meta = json.loads((bundle_dir / "metadata.json").read_text())
    (tmp_path / "metadata.json").write_text(json.dumps({**meta, "node_features": ["old"]}))
    with pytest.raises(ValueError, match="feature layout"):
        Detector(tmp_path, CONFIG, SPEC)


def test_pipeline_ingest_to_detections_with_dlq(bundle_dir) -> None:  # type: ignore[no-untyped-def]
    flows = synthetic_flows(np.random.default_rng(2), minutes=12)
    records = _records(flows)
    records.append({**records[0], "src_ip": "00:15:17:2c:e5:2d"})  # ARP-style junk
    records.append({**records[1], "src_bytes": -5})

    bus, stats = LocalBus(), IngestStats()
    for i in range(0, len(records), 50):
        bus.publish(FLOWS_RAW, "lab", {"sensor_id": "lab", "flows": records[i : i + 50]})
    det = Detector(bundle_dir, CONFIG, SPEC, min_flows=1, allowed_lateness_s=0.0)
    service = DetectorService(bus, det, AlertEngine(det.threshold), explain_epochs=0)
    while ingest_step(bus, stats) or service.step():
        pass
    service.flush()

    assert (stats.flows_ok, stats.flows_rejected) == (len(flows), 2)
    assert sum(len(m.value["errors"]) for m in bus.poll(DLQ)) == 2
    detections = bus.poll(DETECTIONS)
    assert len(detections) == len(list(sliding_windows(flows, SPEC, 1)))
    assert bus.pending(FLOWS) == 0
    beacon = [h for d in detections for h in d.value["hosts"] if h["ip"] == "10.0.0.66"]
    assert beacon and all(0.0 <= h["score"] <= 1.0 for h in beacon)


def test_ego_graph_keeps_the_exact_score(bundle_dir) -> None:  # type: ignore[no-untyped-def]
    flows = synthetic_flows(np.random.default_rng(5), minutes=6)
    graph = build_window_graph(flows, "0-300", CONFIG)
    model, scaler = load_bundle(bundle_dir)
    full = to_data(graph, scaler)
    for ip in ("10.0.0.66", "10.0.0.1"):
        ego, node = ego_graph(graph, ip, hops=model.cfg.layers)
        sub = to_data(ego, scaler)
        with torch.no_grad():
            s_full = model(full.x, full.edge_index, full.edge_attr)[graph.nodes.index(ip)]
            s_ego = model(sub.x, sub.edge_index, sub.edge_attr)[node]
        assert ego.nodes[node] == ip
        assert ego.num_nodes < graph.num_nodes
        assert float(s_ego) == pytest.approx(float(s_full), abs=1e-5)
