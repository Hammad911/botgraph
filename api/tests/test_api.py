from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from botgraph_api.app import create_app
from botgraph_api.auth import issue_token
from botgraph_stream.store import Store

SECRET = "test-secret"
T0 = 1_700_000_000.0


def _alert(ip: str, start: float, level: str = "alert") -> dict:  # type: ignore[type-arg]
    return {
        "type": level,
        "sensor_id": "lab",
        "ip": ip,
        "window_start": start,
        "score": 0.99,
        "hits": 12,
        "rule": {"k": 12, "n": 15},
        "threshold": 0.9,
        "explanation": {
            "ip": ip,
            "top_features": [{"feature": "periodicity", "importance": 0.9, "value": 1.0}],
        },
    }


def _window(i: int, hosts: dict[str, float]) -> dict:  # type: ignore[type-arg]
    start = T0 + 60 * i
    return {
        "sensor_id": "lab",
        "window_id": f"{int(start)}-{int(start) + 300}",
        "window_start": start,
        "window_end": start + 300,
        "n_flows": 50,
        "n_nodes": 10,
        "n_edges": 12,
        "latency_ms": 20.0,
        "hosts": [{"ip": ip, "score": sc} for ip, sc in hosts.items()],
        "graph": {"nodes": [{"id": ip, "score": sc} for ip, sc in hosts.items()], "edges": []},
    }


@pytest.fixture
def store(tmp_path) -> Store:  # type: ignore[no-untyped-def]
    s = Store(f"sqlite:///{tmp_path / 'api.db'}")
    for name, role in (("viv", "viewer"), ("ana", "analyst"), ("adm", "admin")):
        s.create_user(name, f"{name}-password", role)
    s.record_event(
        {
            "type": "calibrated",
            "sensor_id": "lab",
            "window_start": T0,
            "threshold": 0.9,
            "model_threshold": 0.9,
            "baseline_scores": 0,
        }
    )
    for i in range(30):
        s.record_window(_window(i, {"10.0.0.66": 0.5 + i / 60, "10.0.0.2": 0.1}))
    s.record_event(_alert("10.0.0.66", T0 + 60 * 3, "warning"))
    s.record_event(_alert("10.0.0.66", T0 + 60 * 12))
    return s


@pytest.fixture
def client(store: Store) -> TestClient:
    return TestClient(create_app(store, SECRET, poll_interval_s=0.05))


def _auth(client: TestClient, user: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": user, "password": f"{user}-password"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_auth_required_and_login(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert (
        client.post("/api/auth/login", json={"username": "ana", "password": "wrong"}).status_code
        == 401
    )
    me = client.get("/api/auth/me", headers=_auth(client, "ana")).json()
    assert me == {"username": "ana", "role": "analyst"}


def test_expired_token_is_rejected(client: TestClient) -> None:
    old = issue_token(SECRET, "ana", "analyst", now=datetime.now(UTC) - timedelta(days=2))
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_roles(client: TestClient, store: Store) -> None:
    alert_id = store.alerts(level="alert")[0].id
    viewer, analyst, admin = (_auth(client, u) for u in ("viv", "ana", "adm"))
    assert (
        client.patch(
            f"/api/alerts/{alert_id}", json={"status": "resolved"}, headers=viewer
        ).status_code
        == 403
    )
    assert client.post("/api/sensors/lab/recalibrate", headers=analyst).status_code == 403
    assert client.post("/api/sensors/lab/recalibrate", headers=admin).status_code == 200
    assert client.post("/api/sensors/nope/recalibrate", headers=admin).status_code == 404


def test_triage(client: TestClient, store: Store) -> None:
    alert_id = store.alerts(level="alert")[0].id
    h = _auth(client, "ana")
    r = client.patch(
        f"/api/alerts/{alert_id}",
        json={"status": "investigating", "assignee": "ana", "note": "C2 on 6667"},
        headers=h,
    )
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["assignee"], r.json()["note"]) == (
        "investigating",
        "ana",
        "C2 on 6667",
    )
    assert (
        client.patch(f"/api/alerts/{alert_id}", json={"status": "deleted"}, headers=h).status_code
        == 422
    )
    assert (
        client.patch("/api/alerts/999", json={"status": "resolved"}, headers=h).status_code == 404
    )

    detail = client.get(f"/api/alerts/{alert_id}", headers=h).json()
    assert detail["explanation"]["top_features"][0]["feature"] == "periodicity"
    listed = client.get("/api/alerts", params={"status": "investigating"}, headers=h).json()
    assert [a["id"] for a in listed] == [alert_id]


def test_overview_host_and_graph(client: TestClient) -> None:
    h = _auth(client, "viv")
    ov = client.get("/api/overview", headers=h).json()
    assert ov["latest_window_start"] == T0 + 60 * 29
    assert (ov["open_alerts"], ov["open_warnings"], ov["sensors"], ov["active_sensors"]) == (
        1,
        1,
        1,
        1,
    )
    assert ov["hosts_monitored"] == 2
    assert len(ov["trend"]) == 24 and sum(p["alerts"] for p in ov["trend"]) == 1
    assert ov["top_hosts"][0] == {
        "sensor_id": "lab",
        "ip": "10.0.0.66",
        "peak_score": pytest.approx(0.5 + 29 / 60),
        "open_alert": True,
    }

    host = client.get("/api/hosts/lab/10.0.0.66", headers=h).json()
    assert len(host["timeline"]) == 30 and host["threshold"] == 0.9
    assert [a["level"] for a in host["alerts"]] == ["alert", "warning"]
    assert client.get("/api/hosts/lab/10.9.9.9", headers=h).status_code == 404

    graph = client.get("/api/graph/lab", headers=h).json()
    assert graph["window_start"] == T0 + 60 * 29 and len(graph["graph"]["nodes"]) == 2
    assert client.get("/api/graph/nope", headers=h).status_code == 404


def test_websocket_pushes_new_alerts_and_windows(client: TestClient, store: Store) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/ws?token=bad") as ws:
        ws.receive_json()

    token = client.post(
        "/api/auth/login", json={"username": "viv", "password": "viv-password"}
    ).json()["access_token"]
    with client.websocket_connect(f"/api/ws?token={token}") as ws:
        store.record_event(_alert("10.0.0.99", T0 + 60 * 30))
        store.record_window(_window(30, {"10.0.0.99": 0.97}))
        messages = [ws.receive_json(), ws.receive_json()]
    kinds = {m["type"]: m for m in messages}
    assert kinds["alert"]["alert"]["ip"] == "10.0.0.99"  # only new alerts, not history
    assert kinds["window"] == {"type": "window", "sensor_id": "lab", "window_start": T0 + 60 * 30}
