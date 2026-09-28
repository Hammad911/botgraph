"""Sliding time windows over a flow frame."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class WindowSpec:
    size_s: float = 300.0  # 5-minute windows
    hop_s: float = 60.0  # advance every minute

    def __post_init__(self) -> None:
        if self.size_s <= 0 or self.hop_s <= 0:
            raise ValueError("window size and hop must be positive")
        if self.hop_s > self.size_s:
            raise ValueError("hop larger than window size would silently drop flows")


@dataclass(frozen=True, slots=True)
class Window:
    start: float
    end: float  # exclusive
    flows: pd.DataFrame

    @property
    def window_id(self) -> str:
        return f"{int(self.start)}-{int(self.end)}"


def sliding_windows(flows: pd.DataFrame, spec: WindowSpec, min_flows: int = 1) -> Iterator[Window]:
    """Yield windows ``[start, start + size)`` with starts aligned to multiples of ``hop``.

    A flow belongs to every window containing its start timestamp. Windows with
    fewer than ``min_flows`` flows are skipped.
    """
    if flows.empty:
        return
    ordered = flows.sort_values("ts", kind="stable").reset_index(drop=True)
    ts = ordered["ts"].to_numpy()

    # Earliest hop-aligned start whose window still contains the first flow.
    start = (math.floor((ts[0] - spec.size_s) / spec.hop_s) + 1) * spec.hop_s
    last_ts = ts[-1]

    while start <= last_ts:
        end = start + spec.size_s
        lo, hi = np.searchsorted(ts, [start, end], side="left")
        if hi - lo >= min_flows:
            yield Window(start=start, end=end, flows=ordered.iloc[lo:hi].reset_index(drop=True))
        start += spec.hop_s
