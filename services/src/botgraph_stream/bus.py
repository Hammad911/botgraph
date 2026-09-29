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


class KafkaBus:
    """Kafka/Redpanda transport.

    * One consumer per polled topic, in consumer group ``<group_id>.<topic>``, so each service
      (ingest, detector) tracks its own offsets and several instances share partitions.
    * Keys are sensor ids, so one sensor's flows land on one partition and stay in order.
    * ``prefix`` namespaces topic names (tests, multiple deployments on one cluster).
    """

    def __init__(
        self,
        bootstrap: str,
        group_id: str,
        prefix: str = "",
        auto_offset_reset: str = "earliest",
    ) -> None:
        from confluent_kafka import Producer  # optional at import time for LocalBus users

        self.bootstrap = bootstrap
        self.group_id = group_id
        self.prefix = prefix
        self.auto_offset_reset = auto_offset_reset
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "client.id": f"botgraph-{group_id}",
                "enable.idempotence": True,
                "linger.ms": 20,
                "compression.type": "lz4",
            }
        )
        self._consumers: dict[str, Any] = {}

    def _name(self, topic: str) -> str:
        return f"{self.prefix}{topic}"

    def ensure_topics(self, partitions: int = 3, topics: tuple[str, ...] = TOPICS) -> None:
        """Create the pipeline topics if missing (idempotent)."""
        from confluent_kafka import KafkaException
        from confluent_kafka.admin import AdminClient, NewTopic

        admin = AdminClient({"bootstrap.servers": self.bootstrap})
        futures = admin.create_topics(
            [
                NewTopic(self._name(t), num_partitions=partitions, replication_factor=1)
                for t in topics
            ]
        )
        for future in futures.values():
            try:
                future.result(timeout=30)
            except KafkaException as exc:
                if "TOPIC_ALREADY_EXISTS" not in str(exc):
                    raise

    def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        payload = encode(value)
        while True:
            try:
                self._producer.produce(self._name(topic), key=key.encode(), value=payload)
                break
            except BufferError:  # local queue full: let delivery catch up
                self._producer.poll(0.5)
        self._producer.poll(0)

    def _consumer(self, topic: str) -> Any:
        if topic not in self._consumers:
            from confluent_kafka import Consumer

            consumer = Consumer(
                {
                    "bootstrap.servers": self.bootstrap,
                    "group.id": f"{self.group_id}.{topic}",
                    "auto.offset.reset": self.auto_offset_reset,
                    "enable.auto.commit": True,
                }
            )
            consumer.subscribe([self._name(topic)])
            self._consumers[topic] = consumer
        return self._consumers[topic]

    def poll(self, topic: str, max_messages: int = 1000, timeout: float = 0.0) -> list[Message]:
        from confluent_kafka import KafkaError

        out = []
        for msg in self._consumer(topic).consume(num_messages=max_messages, timeout=timeout):
            error = msg.error()
            if error is not None:
                if error.code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError(f"kafka consume error on {topic}: {error}")
            key = msg.key()
            out.append(Message(topic, key.decode() if key else "", decode(msg.value())))
        return out

    def flush(self) -> None:
        self._producer.flush(30)

    def close(self) -> None:
        self.flush()
        for consumer in self._consumers.values():
            consumer.close()
        self._consumers.clear()
