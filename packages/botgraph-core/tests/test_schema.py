from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from botgraph_core.schema import FlowRecord, Label, records_to_frame, validate_frame


def _record(**overrides: object) -> FlowRecord:
    base: dict[str, object] = {
        "ts": 1.0,
        "duration": 0.5,
        "proto": "tcp",
        "src_ip": "10.0.0.1",
        "src_port": 1234,
        "dst_ip": "10.0.0.2",
        "dst_port": 80,
        "src_bytes": 10,
        "dst_bytes": 0,
        "pkts": 2,
    }
    return FlowRecord.model_validate({**base, **overrides})


def test_record_normalizes_ip_and_defaults() -> None:
    rec = _record(src_ip="2001:0db8:0000:0000:0000:0000:0000:0001")
    assert rec.src_ip == "2001:db8::1"
    assert rec.label is Label.UNKNOWN
    assert rec.failed  # no response bytes


@pytest.mark.parametrize(
    "override", [{"src_ip": "not-an-ip"}, {"dst_port": 70000}, {"src_bytes": -1}]
)
def test_record_rejects_invalid(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _record(**override)


def test_records_to_frame_round_trip() -> None:
    df = records_to_frame([_record(), _record(ts=2.0, label="botnet")])
    assert len(df) == 2
    assert df["label"].tolist() == ["unknown", "botnet"]


def test_validate_frame_rejects_bad_values() -> None:
    df = records_to_frame([_record()])
    with pytest.raises(ValueError, match="proto"):
        validate_frame(df.assign(proto="sctp"))
    with pytest.raises(ValueError, match="missing"):
        validate_frame(pd.DataFrame({"ts": [1.0]}))


def test_validate_frame_rejects_non_ip_addresses() -> None:
    df = records_to_frame([_record()])
    with pytest.raises(ValueError, match="non-IP"):
        validate_frame(df.assign(src_ip="00:15:17:2c:e5:2d"))
