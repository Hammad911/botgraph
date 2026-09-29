"""Derive per-window host labels from per-flow labels (IoT-23).

CTU-13 gives host-level ground truth per scenario. IoT-23 labels each flow instead, and an
infected device also produces benign traffic, so labels are assigned per window:

* ``botnet``  - an internal host that *originated* at least one malicious flow in the window
* ``benign``  - an internal host that originated at least one flow in the window, with every
                flow it took part in labelled benign
* ``unknown`` - everything else: external hosts, hosts that only *received* traffic (scan
                targets, often addresses with no device behind them; being attacked is not
                being infected), hosts with unlabelled flows
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network

import pandas as pd

from botgraph_core import Label


def window_host_labels(flows: pd.DataFrame, internal_nets: tuple[str, ...]) -> dict[str, Label]:
    nets = [ip_network(n) for n in internal_nets]

    def internal(ip: str) -> bool:
        addr = ip_address(ip)
        return any(addr in net for net in nets)

    # Every (host, flow label) pair a host takes part in, as originator or responder.
    roles = pd.concat(
        [
            pd.DataFrame({"ip": flows["src_ip"], "label": flows["label"], "origin": True}),
            pd.DataFrame({"ip": flows["dst_ip"], "label": flows["label"], "origin": False}),
        ],
        ignore_index=True,
    ).astype({"ip": str, "label": str})

    labels: dict[str, Label] = {}
    for ip, part in roles.groupby("ip", sort=False):
        if not internal(str(ip)):
            continue
        malicious = part["label"] == Label.BOTNET.value
        if (malicious & part["origin"]).any():
            labels[str(ip)] = Label.BOTNET
        elif part["origin"].any() and (part["label"] == Label.BENIGN.value).all():
            labels[str(ip)] = Label.BENIGN
    return labels
