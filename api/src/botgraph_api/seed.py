"""Synthetic demo data for UI development, screenshots and the end-to-end browser test.

One sensor, an hour of one-minute windows: eight normal hosts and one host (10.0.0.66) that
beacons to an IRC server, a warning and an alert with an explanation, and a map snapshot.
"""

from __future__ import annotations

import math

from botgraph_stream.store import Store

SENSOR = "demo-lab"
BOT = "10.0.0.66"
C2 = "203.0.113.9"
T0 = 1_700_000_000.0


def seed(store: Store, username: str, password: str, role: str = "admin") -> None:
    store.create_user(username, password, role)
    store.record_event(
        {
            "type": "calibrated",
            "sensor_id": SENSOR,
            "window_start": T0,
            "threshold": 0.96,
            "model_threshold": 0.96,
            "baseline_scores": 480,
        }
    )
    hosts = [f"10.0.0.{i}" for i in range(1, 9)]
    for w in range(60):
        start = T0 + 60 * w
        scores = {h: round(0.05 + 0.1 * abs(math.sin(w / 7 + i)), 4) for i, h in enumerate(hosts)}
        scores[BOT] = round(min(0.999, 0.6 + w / 40), 4)
        nodes = [
            {"id": ip, "score": sc, "internal": True, "flagged": sc >= 0.96, "degree": 3}
            for ip, sc in scores.items()
        ] + [{"id": C2, "score": 0.02, "internal": False, "flagged": False, "degree": 1}]
        edges = [{"source": BOT, "target": C2, "flows": 5, "bytes": 3000, "periodicity": 0.98}] + [
            {
                "source": h,
                "target": hosts[(i + 1) % len(hosts)],
                "flows": 2,
                "bytes": 800,
                "periodicity": 0.1,
            }
            for i, h in enumerate(hosts)
        ]
        store.record_window(
            {
                "sensor_id": SENSOR,
                "window_id": f"{int(start)}-{int(start) + 300}",
                "window_start": start,
                "window_end": start + 300,
                "n_flows": 120,
                "n_nodes": len(nodes),
                "n_edges": len(edges),
                "latency_ms": 12.0,
                "hosts": [{"ip": ip, "score": sc} for ip, sc in scores.items()],
                "graph": {
                    "nodes": nodes,
                    "edges": edges,
                    "total_nodes": len(nodes),
                    "total_edges": len(edges),
                },
            }
        )
    common = {"sensor_id": SENSOR, "ip": BOT, "threshold": 0.96, "first_seen": T0}
    store.record_event(
        {
            **common,
            "type": "warning",
            "window_start": T0 + 60 * 16,
            "score": 0.97,
            "hits": 3,
            "rule": {"k": 3, "n": 5},
        }
    )
    store.record_event(
        {
            **common,
            "type": "alert",
            "window_start": T0 + 60 * 25,
            "score": 0.999,
            "hits": 12,
            "rule": {"k": 12, "n": 15},
            "explanation": {
                "ip": BOT,
                "score": 0.999,
                "method": "integrated_gradients",
                "logit": 9.2,
                "baseline_logit": -2.0,
                "top_features": [
                    {"feature": "periodicity", "importance": 5.1, "value": 0.98},
                    {"feature": "frac_irc", "importance": 3.4, "value": 1.0},
                    {"feature": "log_out_degree", "importance": 0.9, "value": 0.693},
                ],
                "top_flows": [
                    {
                        "src": BOT,
                        "dst": C2,
                        "importance": 2.2,
                        "features": {
                            "log_flows": 1.792,
                            "periodicity": 0.98,
                            "log_iat_mean": 4.111,
                            "log_dst_ports": 0.693,
                            "failed_ratio": 0.0,
                        },
                    }
                ],
            },
        }
    )
