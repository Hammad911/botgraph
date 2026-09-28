from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest

from botgraph_core.schema import validate_frame

FlowFactory = Callable[..., pd.DataFrame]


@pytest.fixture
def make_flows() -> FlowFactory:
    """Build a validated flow frame from partial rows; unspecified fields get defaults."""

    def factory(*rows: dict[str, Any]) -> pd.DataFrame:
        defaults: dict[str, Any] = {
            "duration": 1.0,
            "proto": "tcp",
            "src_port": 40000,
            "dst_port": 80,
            "src_bytes": 100,
            "dst_bytes": 500,
            "pkts": 4,
            "label": "unknown",
            "sensor_id": "test",
        }
        return validate_frame(pd.DataFrame([{**defaults, **row} for row in rows]))

    return factory
