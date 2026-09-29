"""Event-time sliding windows over a live flow stream.

Same windows as the batch ``botgraph_core.sliding_windows`` (``[start, start + size)``, starts
aligned to multiples of ``hop``), but computed incrementally: flows arrive in batches, possibly
out of order, and a window is emitted once the watermark passes its end.

    watermark = (largest flow timestamp seen) - allowed_lateness

A flow that arrives after every window containing it has been emitted is *late*: it is counted
and dropped. On in-order input the emitted windows are identical to the batch function's.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np
import pandas as pd

from botgraph_core import Window, WindowSpec


class StreamingWindower:
    def __init__(
        self, spec: WindowSpec, allowed_lateness_s: float = 30.0, min_flows: int = 1
    ) -> None:
        if allowed_lateness_s < 0:
            raise ValueError("allowed lateness must be >= 0")
        self.spec = spec
        self.allowed_lateness_s = allowed_lateness_s
        self.min_flows = min_flows
        self._buffer: pd.DataFrame | None = None
        self._next_start: float | None = None  # earliest window not yet emitted
        self._closed_any = False
        self._max_ts = -math.inf
        self.late_flows = 0
        self.windows_emitted = 0

    @property
    def watermark(self) -> float:
        return self._max_ts - self.allowed_lateness_s

    def _first_start(self, ts: float) -> float:
        """Earliest hop-aligned start whose window still contains ``ts``."""
        return (math.floor((ts - self.spec.size_s) / self.spec.hop_s) + 1) * self.spec.hop_s

    def add(self, flows: pd.DataFrame) -> Iterator[Window]:
        """Buffer a batch of flows and yield every window the watermark has now closed."""
        if flows.empty:
            return
        ts = flows["ts"].to_numpy()
        if self._next_start is not None and self._closed_any:
            late = ts < self._next_start
            if late.any():
                self.late_flows += int(late.sum())
                flows = flows.loc[~late]
                ts = ts[~late]
                if flows.empty:
                    return
        start = self._first_start(float(ts.min()))
        if self._next_start is None or (not self._closed_any and start < self._next_start):
            self._next_start = start  # nothing emitted yet: an earlier flow moves the start back
        self._max_ts = max(self._max_ts, float(ts.max()))
        self._buffer = flows if self._buffer is None else pd.concat([self._buffer, flows])
        yield from self._emit(until=self.watermark)

    def flush(self) -> Iterator[Window]:
        """End of stream: emit every remaining window that contains a flow."""
        yield from self._emit(until=math.inf)

    def _emit(self, until: float) -> Iterator[Window]:
        """Emit windows that are closed at ``until`` (a window closes once its end <= until).

        Progress is recorded before each window is yielded, so a consumer that stops early
        never sees a window twice.
        """
        if self._buffer is None or self._next_start is None or self._buffer.empty:
            return
        size, hop = self.spec.size_s, self.spec.hop_s
        ordered = self._buffer.sort_values("ts", kind="stable").reset_index(drop=True)
        ts = ordered["ts"].to_numpy()
        # Drop flows that no open window can use (left over from an earlier, abandoned pass).
        first = int(np.searchsorted(ts, self._next_start, side="left"))
        ordered, ts = ordered.iloc[first:].reset_index(drop=True), ts[first:]
        self._buffer = ordered
        if len(ts) == 0:
            return
        last_closed_start = until - size  # windows starting at or before this are closed
        start = self._next_start
        while start <= last_closed_start and start <= ts[-1]:
            lo, hi = np.searchsorted(ts, [start, start + size], side="left")
            window = None
            if hi - lo >= self.min_flows:
                window = Window(
                    start=start, end=start + size, flows=ordered.iloc[lo:hi].reset_index(drop=True)
                )
            if hi > lo:
                start += hop
            else:
                # Empty window: jump to the first window holding the next flow, but never past
                # a window that is still open (a flow within the lateness could still land).
                target = self._first_start(float(ts[lo]))
                if math.isfinite(last_closed_start):
                    target = min(target, (math.floor(last_closed_start / hop) + 1) * hop)
                start = max(start + hop, target)
            self._next_start = start
            self._closed_any = True
            if window is not None:
                self.windows_emitted += 1
                yield window
        # Flows before the next window's start can never be used again.
        self._buffer = ordered.iloc[int(np.searchsorted(ts, start, side="left")) :]
