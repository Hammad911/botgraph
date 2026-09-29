from __future__ import annotations

import pandas as pd

from botgraph_core import Label
from botgraph_ml.flow_labels import window_host_labels

NETS = ("192.168.0.0/16",)


def _flows(*rows: tuple[str, str, str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["src_ip", "dst_ip", "label"])


def test_originating_malicious_traffic_makes_a_bot() -> None:
    labels = window_host_labels(
        _flows(("192.168.1.10", "8.8.8.8", "benign"), ("192.168.1.10", "1.2.3.4", "botnet")), NETS
    )
    assert labels == {"192.168.1.10": Label.BOTNET}  # external hosts are never labelled


def test_all_benign_internal_host_is_benign() -> None:
    labels = window_host_labels(_flows(("192.168.1.20", "8.8.8.8", "benign")), NETS)
    assert labels == {"192.168.1.20": Label.BENIGN}


def test_being_attacked_is_not_being_infected() -> None:
    labels = window_host_labels(
        _flows(("5.6.7.8", "192.168.1.30", "botnet"), ("192.168.1.30", "8.8.8.8", "benign")), NETS
    )
    assert "192.168.1.30" not in labels  # received malicious traffic only: unknown


def test_unlabelled_flows_leave_host_unknown() -> None:
    labels = window_host_labels(
        _flows(("192.168.1.40", "8.8.8.8", "benign"), ("192.168.1.40", "8.8.4.4", "unknown")), NETS
    )
    assert labels == {}


def test_receive_only_hosts_are_unknown() -> None:
    # A bot scanning private addresses: the targets only receive (often no device exists).
    labels = window_host_labels(
        _flows(("192.168.1.50", "10.0.0.7", "benign"), ("192.168.1.50", "10.0.0.8", "benign")),
        ("192.168.0.0/16", "10.0.0.0/8"),
    )
    assert labels == {"192.168.1.50": Label.BENIGN}
