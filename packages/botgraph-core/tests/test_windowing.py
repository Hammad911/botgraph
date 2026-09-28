from __future__ import annotations

from collections import Counter

import pytest

from botgraph_core.windowing import WindowSpec, sliding_windows


def test_window_contents_and_boundaries(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        *(
            {"ts": t, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"}
            for t in (0.0, 59.0, 60.0, 299.0, 300.0)
        )
    )
    windows = {w.start: w for w in sliding_windows(flows, WindowSpec(size_s=300, hop_s=60))}

    assert min(windows) == -240.0  # earliest hop-aligned window that still contains ts=0
    assert windows[0.0].flows["ts"].tolist() == [0.0, 59.0, 60.0, 299.0]  # end is exclusive
    assert windows[0.0].window_id == "0-300"


def test_every_flow_lands_in_size_over_hop_windows(make_flows):  # type: ignore[no-untyped-def]
    ts = [0.0, 13.0, 61.5, 200.0, 420.0, 421.0]
    flows = make_flows(*({"ts": t, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"} for t in ts))
    counts: Counter[float] = Counter()
    for w in sliding_windows(flows, WindowSpec(size_s=300, hop_s=60)):
        counts.update(w.flows["ts"].tolist())
    assert all(counts[t] == 5 for t in ts)


def test_min_flows_skips_sparse_windows(make_flows):  # type: ignore[no-untyped-def]
    flows = make_flows(
        {"ts": 0.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"},
        {"ts": 1000.0, "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2"},
    )
    windows = list(sliding_windows(flows, WindowSpec(size_s=60, hop_s=60), min_flows=1))
    assert [w.start for w in windows] == [0.0, 960.0]


def test_invalid_spec() -> None:
    with pytest.raises(ValueError):
        WindowSpec(size_s=60, hop_s=120)
    with pytest.raises(ValueError):
        WindowSpec(size_s=0, hop_s=0)
