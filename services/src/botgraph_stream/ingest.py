"""Ingest: validate raw flow batches, pass clean flows on, quarantine the rest.

    flows.raw  {"sensor_id": str, "flows": [ {FlowRecord fields}, ... ]}
      -> flows.normalized  {"sensor_id": str, "flows": [ normalised FlowRecord dicts ]}
      -> flows.dlq         {"sensor_id": str, "errors": [ {"flow": ..., "error": ...} ]}

Validation is ``botgraph_core.FlowRecord``: the same schema as training data (IPs normalised,
ports and byte counts range-checked, non-IP rows such as ARP rejected).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from botgraph_core import FlowRecord
from botgraph_stream.bus import DLQ, FLOWS, FLOWS_RAW, Bus
from botgraph_stream.metrics import FLOWS_INGESTED

MAX_ERROR_CHARS = 300


@dataclass
class IngestStats:
    flows_in: int = 0
    flows_ok: int = 0
    flows_rejected: int = 0


def validate_batch(
    flows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    good, bad = [], []
    for raw in flows:
        try:
            good.append(FlowRecord.model_validate(raw).model_dump(mode="json"))
        except ValidationError as exc:
            bad.append({"flow": raw, "error": str(exc)[:MAX_ERROR_CHARS]})
    return good, bad


def ingest_step(bus: Bus, stats: IngestStats, max_messages: int = 100, timeout: float = 0.0) -> int:
    """Process pending raw batches once; returns the number of messages handled."""
    messages = bus.poll(FLOWS_RAW, max_messages, timeout)
    for msg in messages:
        sensor = str(msg.value.get("sensor_id") or msg.key)
        flows = msg.value.get("flows", [])
        good, bad = validate_batch(flows)
        stats.flows_in += len(flows)
        stats.flows_ok += len(good)
        stats.flows_rejected += len(bad)
        FLOWS_INGESTED.labels("ok").inc(len(good))
        FLOWS_INGESTED.labels("rejected").inc(len(bad))
        if good:
            bus.publish(FLOWS, sensor, {"sensor_id": sensor, "flows": good})
        if bad:
            bus.publish(DLQ, sensor, {"sensor_id": sensor, "errors": bad})
    return len(messages)
