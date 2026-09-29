from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from botgraph_api.app import create_app
from botgraph_api.auth import RateLimiter, issue_token, load_secret, read_token
from botgraph_stream.store import MAX_FAILED_LOGINS, Store

SECRET = "test-secret-that-is-long-enough-for-hs256!"


@pytest.fixture
def store(tmp_path) -> Store:  # type: ignore[no-untyped-def]
    s = Store(f"sqlite:///{tmp_path / 'sec.db'}")
    s.create_user("ana", "ana-password", "analyst")
    s.create_user("adm", "adm-password", "admin")
    return s


@pytest.fixture
def client(store: Store) -> TestClient:
    return TestClient(create_app(store, SECRET, poll_interval_s=0.02))


def _login(client: TestClient, user: str, password: str | None = None) -> dict[str, str]:
    r = client.post(
        "/api/auth/login", json={"username": user, "password": password or f"{user}-password"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_account_locks_after_repeated_failures(client: TestClient, store: Store) -> None:
    for _ in range(MAX_FAILED_LOGINS):
        r = client.post("/api/auth/login", json={"username": "ana", "password": "wrong-one"})
        assert r.status_code == 401
    # Locked: even the right password fails, with the same message as a wrong one.
    r = client.post("/api/auth/login", json={"username": "ana", "password": "ana-password"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid username or password"
    assert store.login("ana", "ana-password") == (None, "locked")

    store.set_password("ana", "a-brand-new-password")  # an admin reset unlocks the account
    _login(client, "ana", "a-brand-new-password")
    outcomes = [e.detail["outcome"] for e in store.audit_events(action="login_failed")]
    assert outcomes.count("locked") >= 2 and "invalid" in outcomes


def test_unknown_users_and_disabled_accounts_look_like_wrong_passwords(
    client: TestClient, store: Store
) -> None:
    assert store.login("ghost", "whatever-password") == (None, "invalid")
    store.set_disabled("ana", True)
    r = client.post("/api/auth/login", json={"username": "ana", "password": "ana-password"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid username or password"


def test_logins_are_rate_limited_per_client(store: Store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BOTGRAPH_LOGIN_RATE_LIMIT", "3")
    client = TestClient(create_app(store, SECRET))
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "x", "password": "y"})
    r = client.post("/api/auth/login", json={"username": "ana", "password": "ana-password"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_rate_limiter_window_and_eviction() -> None:
    limiter = RateLimiter(limit=2, window_s=10, max_keys=4)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=1)
    assert not limiter.allow("a", now=2)
    assert limiter.allow("a", now=10.5)  # the first hit left the window
    for i in range(10):
        limiter.allow(f"k{i}", now=100 + i)
    assert len(limiter._hits) <= 4


def test_tokens_are_revoked_by_logout_password_change_and_disable(
    client: TestClient, store: Store
) -> None:
    for revoke in (
        lambda h: client.post("/api/auth/logout", headers=h),
        lambda h: store.set_password("ana", "ana-password"),
        lambda h: store.set_disabled("ana", True),
    ):
        store.set_disabled("ana", False)
        headers = _login(client, "ana")
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        revoke(headers)
        assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_role_changes_apply_to_existing_tokens(client: TestClient, store: Store) -> None:
    headers = _login(client, "adm")
    with store.engine.begin() as c:
        c.exec_driver_sql("UPDATE users SET role = 'viewer' WHERE username = 'adm'")
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "viewer"
    assert client.get("/api/audit", headers=headers).status_code == 403


def test_tokens_need_issuer_and_expiry() -> None:
    import jwt

    assert read_token(SECRET, issue_token(SECRET, "ana", "analyst")) is not None
    no_iss = jwt.encode({"sub": "ana", "role": "admin", "exp": 9e9, "iat": 0}, SECRET, "HS256")
    no_exp = jwt.encode({"sub": "ana", "role": "admin", "iss": "botgraph", "iat": 0}, SECRET)
    unsigned = jwt.encode({"sub": "ana", "role": "admin", "iss": "botgraph"}, None, "none")
    assert read_token(SECRET, no_iss) is None
    assert read_token(SECRET, no_exp) is None
    assert read_token(SECRET, unsigned) is None


def test_production_requires_a_strong_secret(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BOTGRAPH_ENV", "production")
    monkeypatch.delenv("BOTGRAPH_JWT_SECRET", raising=False)
    with pytest.raises(SystemExit, match="requires BOTGRAPH_JWT_SECRET"):
        load_secret(tmp_path)
    monkeypatch.setenv("BOTGRAPH_JWT_SECRET", "short")
    with pytest.raises(SystemExit, match="at least 32 bytes"):
        load_secret(tmp_path)
    monkeypatch.setenv("BOTGRAPH_JWT_SECRET", SECRET)
    assert load_secret(tmp_path) == SECRET
    assert not (tmp_path / "jwt_secret").exists()  # never falls back to a local file


def test_production_hides_docs_and_sends_hsts(store: Store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dev = TestClient(create_app(store, SECRET))
    assert dev.get("/openapi.json").status_code == 200
    assert "strict-transport-security" not in dev.get("/api/health").headers
    monkeypatch.setenv("BOTGRAPH_ENV", "production")
    prod = TestClient(create_app(store, SECRET))
    assert prod.get("/openapi.json").status_code == 404
    assert prod.get("/docs").status_code == 404
    assert "max-age" in prod.get("/api/health").headers["strict-transport-security"]


def test_security_headers_and_body_limit(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    big = client.post(
        "/api/auth/login",
        content=b"{" + b" " * 70_000 + b"}",
        headers={"content-type": "application/json"},
    )
    assert big.status_code == 413


def test_cors_wildcard_is_refused(store: Store, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BOTGRAPH_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="explicitly"):
        create_app(store, SECRET)


def test_path_parameters_are_validated(client: TestClient) -> None:
    headers = _login(client, "ana")
    assert client.get("/api/hosts/lab/10.0.0.1", headers=headers).status_code == 404
    assert client.get("/api/hosts/lab/not an ip", headers=headers).status_code == 422
    assert client.get("/api/graph/bad sensor!", headers=headers).status_code == 422


def test_websocket_rejects_foreign_origins_and_revoked_tokens(
    client: TestClient, store: Store, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    token = _login(client, "ana")["Authorization"].removeprefix("Bearer ")
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/ws", headers={"origin": "https://evil.example"}) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 4403

    monkeypatch.setattr("botgraph_api.app.REVALIDATE_S", 0.0)
    with client.websocket_connect("/api/ws", headers={"origin": "http://localhost:3000"}) as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json() == {"type": "ready"}
        store.revoke_tokens("ana")
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_json()
    assert closed.value.code == 4401


def test_triage_and_recalibration_are_audited(client: TestClient, store: Store) -> None:
    store.record_event(
        {
            "type": "alert",
            "sensor_id": "lab",
            "ip": "10.0.0.66",
            "window_start": 0.0,
            "score": 0.99,
            "hits": 12,
            "rule": {"k": 12, "n": 15},
            "threshold": 0.9,
        }
    )
    store.record_event(
        {
            "type": "calibrated",
            "sensor_id": "lab",
            "window_start": 0.0,
            "threshold": 0.9,
            "model_threshold": 0.9,
            "baseline_scores": 1,
        }
    )
    ana, adm = _login(client, "ana"), _login(client, "adm")
    alert_id = store.alerts()[0].id
    client.patch(f"/api/alerts/{alert_id}", json={"status": "resolved", "note": "fp"}, headers=ana)
    client.post("/api/sensors/lab/recalibrate", headers=adm)

    assert client.get("/api/audit", headers=ana).status_code == 403
    events = client.get("/api/audit", headers=adm).json()
    triaged = next(e for e in events if e["action"] == "alert_triaged")
    assert triaged["actor"] == "ana" and triaged["target"] == f"alert:{alert_id}"
    assert triaged["detail"] == {"status": "resolved", "note": "fp"}
    assert any(e["action"] == "sensor_recalibrated" and e["actor"] == "adm" for e in events)
    assert {e["action"] for e in events} >= {"login"}
