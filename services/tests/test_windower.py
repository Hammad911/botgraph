from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from botgraph_core import WindowSpec, sliding_windows
from botgraph_stream.windower import StreamingWindower

SPEC = WindowSpec(size_s=300, hop_s=60)


def _flows(ts: np.ndarray) -> pd.DataFrame:
    # Only the columns the windower touches; `idx` identifies each flow.
    return pd.DataFrame({"ts": ts, "idx": np.arange(len(ts))})


def _stream(
    flows: pd.DataFrame, batch_sizes: list[int], **kw: float
) -> list[tuple[float, list[int]]]:
    w = StreamingWindower(SPEC, **kw)  # type: ignore[arg-type]
    out, i = [], 0
    for size in batch_sizes:
        out += [(win.start, sorted(win.flows["idx"])) for win in w.add(flows.iloc[i : i + size])]
        i += size
    out += [(win.start, sorted(win.flows["idx"])) for win in w.flush()]
    return out


def _batch(flows: pd.DataFrame, min_flows: int) -> list[tuple[float, list[int]]]:
    return [(w.start, sorted(w.flows["idx"])) for w in sliding_windows(flows, SPEC, min_flows)]


def _random_ts(rng: np.random.Generator, n: int) -> np.ndarray:
    # Bursty traffic with long silent gaps, like real captures.
    gaps = rng.choice([0.5, 3.0, 40.0, 900.0, 7200.0], size=n, p=[0.6, 0.25, 0.1, 0.04, 0.01])
    return np.cumsum(gaps * rng.random(n)) + 1_300_000_000.0


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("min_flows", [1, 5])
def test_in_order_stream_matches_batch_windows(seed: int, min_flows: int) -> None:
    rng = np.random.default_rng(seed)
    flows = _flows(_random_ts(rng, 3000))
    batches = list(rng.integers(1, 400, size=100))
    got = _stream(flows, batches, allowed_lateness_s=0.0, min_flows=min_flows)
    assert got == _batch(flows, min_flows)


def test_out_of_order_within_lateness_matches_batch() -> None:
    rng = np.random.default_rng(42)
    ts = _random_ts(rng, 2000)
    flows = _flows(ts)
    # Arrival order: each flow delayed by up to 25 s; lateness 30 s absorbs all of it.
    arrival = flows.iloc[np.argsort(ts + rng.uniform(0, 25, size=len(ts)))]
    got = _stream(arrival, [50] * 40, allowed_lateness_s=30.0)
    assert got == _batch(flows, 1)


def test_late_flows_are_counted_and_dropped() -> None:
    w = StreamingWindower(SPEC, allowed_lateness_s=0.0)
    list(w.add(_flows(np.array([0.0, 100.0, 1000.0]))))  # closes windows before 1000 - 300
    late = list(w.add(_flows(np.array([50.0]))))
    assert late == [] and w.late_flows == 1


def test_early_flow_before_any_emission_moves_start_back() -> None:
    w = StreamingWindower(SPEC, allowed_lateness_s=10_000.0)
    assert list(w.add(_flows(np.array([600.0])))) == []
    assert list(w.add(_flows(np.array([0.0])))) == []  # arrives out of order, nothing closed yet
    starts = [win.start for win in w.flush()]
    assert starts[0] == -240.0  # first window containing ts=0, as in the batch function
    assert w.late_flows == 0


def test_abandoned_iteration_never_repeats_windows() -> None:
    flows = _flows(np.arange(0.0, 1200.0, 10.0))
    w = StreamingWindower(SPEC, allowed_lateness_s=0.0)
    first = next(iter(w.add(flows)))
    rest = list(w.flush())
    assert first.start not in [r.start for r in rest]
    assert [first.start, *[r.start for r in rest]] == [
        x.start for x in sliding_windows(flows, SPEC, 1)
    ]


def test_long_gap_is_skipped_quickly() -> None:
    flows = _flows(np.array([0.0, 30 * 24 * 3600.0]))  # a month of silence
    w = StreamingWindower(SPEC, allowed_lateness_s=0.0)
    got = list(w.add(flows)) + list(w.flush())
    assert len(got) == 10  # 5 windows around each flow, none in between
