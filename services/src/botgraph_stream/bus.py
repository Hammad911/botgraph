"""Message bus abstraction: one poll-based interface, two transports.

* ``LocalBus``  in-process queues; used by ``botgraph run``, tests and the laptop demo.
* ``KafkaBus``  Kafka/Redpanda via confluent-kafka; messages are keyed by ``sensor_id`` so a
                network segment's flows stay on one partition, in order.

Messages are JSON objects. Topics carry the pipeline stages:

    flows.raw -> [ingest] -> flows.normalized -> [detector] -> detections -> [alerts] -> alerts
                    |
                    +-> flows.dlq (invalid rows)
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Protocol

FLOWS_RAW = "flows.raw"
FLOWS = "flows.normalized"
DLQ = "flows.dlq"
DETECTIONS = "detections"
ALERTS = "alerts"
TOPICS = (FLOWS_RAW, FLOWS, DLQ, DETECTIONS, ALERTS)


@dataclass(frozen=True, slots=True)
class Message:
    topic: str
    key: str
    value: dict[str, Any]


class Bus(Protocol):
    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None: ...

    def poll(self, topic: str, max_messages: int = 1000, timeout: float = 0.0) -> list[Message]:
        """Up to ``max_messages`` pending messages from ``topic`` (may be empty)."""
        ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


def encode(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def decode(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("bus messages must be JSON objects")
    return value


class LocalBus:
    """In-process FIFO queues, one per topic. Values are JSON round-tripped on publish so the
    local pipeline sees exactly what it would receive over Kafka (no shared mutable objects)."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[Message]] = defaultdict(deque)

    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        self._queues[topic].append(Message(topic, key, decode(encode(value))))

    def poll(self, topic: str, max_messages: int = 1000, timeout: float = 0.0) -> list[Message]:
        queue = self._queues[topic]
        return [queue.popleft() for _ in range(min(max_messages, len(queue)))]

    def pending(self, topic: str) -> int:
        return len(self._queues[topic])

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._queues.clear()
