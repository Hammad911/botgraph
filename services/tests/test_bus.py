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
