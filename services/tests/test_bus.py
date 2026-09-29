from __future__ import annotations

import pytest

from botgraph_stream.bus import FLOWS, LocalBus, decode, encode


def test_local_bus_is_fifo_per_topic_and_respects_max() -> None:
    bus = LocalBus()
    for i in range(5):
        bus.publish(FLOWS, "s1", {"i": i})
    bus.publish("other", "s1", {"x": 1})

    first = bus.poll(FLOWS, max_messages=3)
    assert [m.value["i"] for m in first] == [0, 1, 2]
    assert [m.value["i"] for m in bus.poll(FLOWS)] == [3, 4]
    assert bus.poll(FLOWS) == []
    assert bus.pending("other") == 1


def test_local_bus_copies_values_like_a_real_broker() -> None:
    bus = LocalBus()
    value = {"ips": ["10.0.0.1"]}
    bus.publish(FLOWS, "s1", value)
    value["ips"].append("mutated after publish")
    assert bus.poll(FLOWS)[0].value == {"ips": ["10.0.0.1"]}


def test_codec_rejects_non_objects() -> None:
    assert decode(encode({"a": 1})) == {"a": 1}
    with pytest.raises(ValueError):
        decode(b"[1, 2]")


def test_kafka_security_comes_from_the_environment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from botgraph_stream.bus import security_config

    monkeypatch.setenv("BOTGRAPH_KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("BOTGRAPH_KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
    monkeypatch.setenv("BOTGRAPH_KAFKA_SASL_USERNAME", "detector")
    monkeypatch.setenv("BOTGRAPH_KAFKA_SASL_PASSWORD", "s3cret")
    monkeypatch.delenv("BOTGRAPH_KAFKA_SSL_CA_LOCATION", raising=False)
    assert security_config() == {
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "SCRAM-SHA-512",
        "sasl.username": "detector",
        "sasl.password": "s3cret",
    }
