"""Unified flow schema shared by training, ingestion and inference.

Two representations exist on purpose:

* ``FlowRecord`` validates single records on the streaming path (ingest service).
* ``FLOW_COLUMNS`` defines the equivalent DataFrame layout used for batch work
  (dataset preparation, window graph construction). ``validate_frame`` enforces it.
"""

from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_address

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Proto(StrEnum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    OTHER = "other"


class Label(StrEnum):
    BOTNET = "botnet"
    BENIGN = "benign"
    UNKNOWN = "unknown"


class FlowRecord(BaseModel):
    """One bidirectional flow. ``src`` is the side that initiated the connection."""

    model_config = ConfigDict(frozen=True)

    ts: float = Field(description="Flow start, epoch seconds")
    duration: float = Field(ge=0)
    proto: Proto
    src_ip: str
    src_port: int | None = Field(default=None, ge=0, le=65535)
    dst_ip: str
    dst_port: int | None = Field(default=None, ge=0, le=65535)
    src_bytes: int = Field(ge=0)
    dst_bytes: int = Field(ge=0)
    pkts: int = Field(ge=0, description="Total packets in both directions")
    label: Label = Label.UNKNOWN
    sensor_id: str = "default"

    @field_validator("src_ip", "dst_ip")
    @classmethod
    def _normalize_ip(cls, value: str) -> str:
        return str(ip_address(value))

    @property
    def failed(self) -> bool:
        return is_failed(self.dst_bytes)


def is_failed(dst_bytes: int) -> bool:
    """A flow is 'failed' when the responder sent nothing back.

    Kept deliberately source-agnostic (CTU-13 Argus states and Zeek conn_state
    differ), so the same definition holds across every dataset and live traffic.
    """
    return dst_bytes == 0


FLOW_DTYPES: dict[str, str] = {
    "ts": "float64",
    "duration": "float64",
    "proto": "string",
    "src_ip": "string",
    "src_port": "Int32",
    "dst_ip": "string",
    "dst_port": "Int32",
    "src_bytes": "int64",
    "dst_bytes": "int64",
    "pkts": "int64",
    "label": "string",
    "sensor_id": "string",
}
FLOW_COLUMNS: list[str] = list(FLOW_DTYPES)


def validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` restricted to the schema columns with canonical dtypes.

    Raises ``ValueError`` on missing columns or invalid categorical values.
    """
    missing = set(FLOW_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"flow frame is missing columns: {sorted(missing)}")

    out = df[FLOW_COLUMNS].astype(FLOW_DTYPES)

    bad_proto = ~out["proto"].isin([p.value for p in Proto])
    if bad_proto.any():
        raise ValueError(f"invalid proto values: {out.loc[bad_proto, 'proto'].unique()[:5]}")
    bad_label = ~out["label"].isin([lbl.value for lbl in Label])
    if bad_label.any():
        raise ValueError(f"invalid label values: {out.loc[bad_label, 'label'].unique()[:5]}")
    if (out[["duration", "src_bytes", "dst_bytes", "pkts"]] < 0).any().any():
        raise ValueError("negative duration, byte or packet counts")
    bad_ip = ~(is_ip(out["src_ip"]) & is_ip(out["dst_ip"]))
    if bad_ip.any():
        sample = out.loc[bad_ip, ["src_ip", "dst_ip"]].head(3).to_dict("records")
        raise ValueError(f"non-IP addresses (e.g. ARP MACs) must be filtered first: {sample}")

    return out.reset_index(drop=True)


def _valid_ip(value: object) -> bool:
    try:
        ip_address(str(value))
    except ValueError:
        return False
    return True


def is_ip(addresses: pd.Series) -> pd.Series:
    """Boolean mask of valid IPv4/IPv6 addresses (each distinct value is parsed once)."""
    unique = pd.unique(addresses.to_numpy())
    valid = {v: _valid_ip(v) for v in unique}
    return addresses.map(valid).fillna(False).astype(bool)


def records_to_frame(records: list[FlowRecord]) -> pd.DataFrame:
    """Convert validated streaming records into the batch DataFrame layout."""
    rows = [r.model_dump(mode="json") for r in records]
    return validate_frame(pd.DataFrame(rows, columns=FLOW_COLUMNS))
