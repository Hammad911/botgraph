"""FastAPI application: REST endpoints for the console plus a WebSocket for live updates.

The API reads the same store the pipeline writes (SQLite in WAL mode or Postgres); it never
talks to the detector directly, so it can run, restart and scale independently.

No ``from __future__ import annotations`` here: FastAPI resolves dependency annotations at
runtime, and the role aliases (``Viewer`` etc.) are local to ``create_app``.
"""

import asyncio
import logging
import os
import time
from collections import Counter
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from botgraph_api.auth import Principal, RateLimiter, issue_token, production, read_token
from botgraph_api.observability import (
    LOGIN_LOCKOUTS,
    LOGINS,
    TRIAGE,
    WEBSOCKETS,
    ObservabilityMiddleware,
)
from botgraph_api.schemas import (
    AlertDetail,
    AlertOut,
    AlertUpdate,
    AuditOut,
    DriftOut,
    DriftPoint,
    GraphOut,
    HostOut,
    LoginRequest,
    Me,
    Overview,
    SensorOut,
    TimelinePoint,
    TokenResponse,
    TopHost,
    TrendPoint,
)
from botgraph_api.security import SecurityHeadersMiddleware
from botgraph_stream.store import Store

OPEN = ("open", "investigating")
HOUR = 3600.0
RECENT_S = 15 * 60.0
AUTH_TIMEOUT_S = 5.0
REVALIDATE_S = 30.0  # how often an open WebSocket re-checks its token against the store
# Path parameters end up in logs and queries: sensor ids and IPs have a known, small alphabet.
SensorId = Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
HostIp = Annotated[str, Path(pattern=r"^[0-9A-Fa-f.:]{2,64}$")]

log = logging.getLogger("botgraph.api")


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(store: Store, secret: str, poll_interval_s: float = 2.0) -> FastAPI:
    prod = production()
    app = FastAPI(
        title="BotGraph API",
        version="0.1.0",
        # The schema and docs are for development; production exposes only the API itself.
        openapi_url=None if prod else "/openapi.json",
        docs_url=None if prod else "/docs",
        redoc_url=None,
    )
    origins = [
        o.strip()
        for o in os.environ.get(
            "BOTGRAPH_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if o.strip()
    ]
    if "*" in origins:
        raise ValueError("BOTGRAPH_CORS_ORIGINS must list origins explicitly, not '*'")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=prod)
    app.add_middleware(ObservabilityMiddleware)
    bearer = HTTPBearer(auto_error=False)
    login_limiter = RateLimiter(
        limit=int(os.environ.get("BOTGRAPH_LOGIN_RATE_LIMIT", "10")), window_s=60.0
    )

    def current(token: str) -> Principal | None:
        """The token's principal, checked against the user as stored now: revoked tokens
        (password changed, account disabled, signed out everywhere) and changed roles apply
        immediately, not when the token expires."""
        who = read_token(secret, token)
        if who is None:
            return None
        user = store.get_user(who.username)
        if user is None or user.disabled or user.token_version != who.version:
            return None
        return Principal(user.username, user.role, user.token_version, who.expires_at)

    def principal(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        who = current(creds.credentials) if creds else None
        if who is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return who

    def require(role: str) -> Callable[..., Principal]:
        def check(who: Annotated[Principal, Depends(principal)]) -> Principal:
            if not who.can(role):
                raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires the {role} role")
            return who

        return check

    Viewer = Annotated[Principal, Depends(require("viewer"))]  # noqa: N806 (type alias)
    Analyst = Annotated[Principal, Depends(require("analyst"))]  # noqa: N806 (type alias)
    Admin = Annotated[Principal, Depends(require("admin"))]  # noqa: N806 (type alias)

    # ------------------------------------------------------------------ auth

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness: the process serves requests."""
        return {"status": "ok"}

    @app.get("/api/ready")
    def ready() -> dict[str, str]:
        """Readiness: the store is reachable (a pod without its database gets no traffic)."""
        if not store.ping():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "store unreachable")
        return {"status": "ready"}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request) -> TokenResponse:
        client = _client(request)
        if not login_limiter.allow(client):
            LOGINS.labels("throttled").inc()
            log.warning("login throttled", extra={"client": client})
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many login attempts; try again later",
                headers={"Retry-After": str(login_limiter.retry_after(client))},
            )
        user, outcome = store.login(body.username, body.password)
        if user is None:
            LOGINS.labels("failure").inc()
            if outcome == "lockout":
                LOGIN_LOCKOUTS.inc()
            log.warning(
                "login failed",
                extra={"username": body.username, "outcome": outcome, "client": client},
            )
            store.audit(
                "login_failed", actor=body.username, client=client, detail={"outcome": outcome}
            )
            # One message for every failure: locked or disabled accounts are not revealed.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
        LOGINS.labels("success").inc()
        log.info("login", extra={"username": user.username, "role": user.role, "client": client})
        store.audit("login", actor=user.username, client=client)
        return TokenResponse(
            access_token=issue_token(secret, user.username, user.role, user.token_version),
            username=user.username,
            role=user.role,
        )

    @app.post("/api/auth/logout")
    def logout(who: Viewer, request: Request) -> dict[str, str]:
        """Revoke every token of this user (signs out all their sessions)."""
        store.revoke_tokens(who.username)
        store.audit("logout", actor=who.username, client=_client(request))
        return {"status": "signed out"}

    @app.get("/api/auth/me")
    def me(who: Viewer) -> Me:
        return Me(username=who.username, role=who.role)

    # ------------------------------------------------------------------ reads

    @app.get("/api/overview")
    def overview(_: Viewer) -> Overview:
        latest = store.latest_window_start()
        sensors = store.sensors()
        trend: list[TrendPoint] = []
        top: list[TopHost] = []
        if latest is not None:
            since = latest - 24 * HOUR
            buckets: Counter[tuple[float, str]] = Counter()
            for a in store.alerts():
                if a.window_start >= since:
                    buckets[(a.window_start // HOUR * HOUR, a.level)] += 1
            first = since // HOUR * HOUR + HOUR
            trend = [
                TrendPoint(
                    hour_start=h,
                    warnings=buckets[(h, "warning")],
                    alerts=buckets[(h, "alert")],
                )
                for h in (first + i * HOUR for i in range(24))
            ]
            open_hosts = {(a.sensor_id, a.ip) for a in store.alerts() if a.status in OPEN}
            top = [
                TopHost(sensor_id=s, ip=ip, peak_score=peak, open_alert=(s, ip) in open_hosts)
                for s, ip, peak in store.top_hosts(since=latest - RECENT_S, limit=10)
            ]
        return Overview(
            latest_window_start=latest,
            sensors=len(sensors),
            active_sensors=sum(s.state == "active" for s in sensors),
            windows_scored=int(store.summary()["windows"] or 0),
            hosts_monitored=store.hosts_monitored(latest - RECENT_S) if latest is not None else 0,
            open_alerts=store.count_alerts("alert", OPEN),
            open_warnings=store.count_alerts("warning", OPEN),
            trend=trend,
            top_hosts=top,
        )

    @app.get("/api/sensors")
    def sensors(_: Viewer) -> list[SensorOut]:
        return [SensorOut.model_validate(s) for s in store.sensors()]

    @app.get("/api/alerts")
    def alerts(
        _: Viewer,
        status_: Annotated[str | None, Query(alias="status")] = None,
        level: str | None = None,
        sensor: str | None = None,
        ip: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[AlertOut]:
        rows = store.alerts(
            status=status_, level=level, sensor_id=sensor, ip=ip, limit=limit, newest_first=True
        )
        return [AlertOut.model_validate(a) for a in rows]

    @app.get("/api/alerts/{alert_id}")
    def alert(alert_id: int, _: Viewer) -> AlertDetail:
        row = store.get_alert(alert_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
        return AlertDetail.model_validate(row)

    @app.patch("/api/alerts/{alert_id}")
    def triage(alert_id: int, body: AlertUpdate, who: Analyst, request: Request) -> AlertDetail:
        row = store.update_alert(alert_id, body.status, body.assignee, body.note)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
        TRIAGE.labels(row.status).inc()
        log.info(
            "alert triaged",
            extra={"alert_id": alert_id, "status": row.status, "by": who.username},
        )
        store.audit(
            "alert_triaged",
            actor=who.username,
            target=f"alert:{alert_id}",
            client=_client(request),
            detail=body.model_dump(exclude_none=True),
        )
        return AlertDetail.model_validate(row)

    @app.get("/api/hosts/{sensor_id}/{ip}")
    def host(
        sensor_id: SensorId,
        ip: HostIp,
        _: Viewer,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 1440,
    ) -> HostOut:
        timeline = store.host_timeline(sensor_id, ip, limit)
        rows = store.alerts(sensor_id=sensor_id, ip=ip, newest_first=True)
        if not timeline and not rows:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "host not found")
        sensor = next((s for s in store.sensors() if s.id == sensor_id), None)
        return HostOut(
            sensor_id=sensor_id,
            ip=ip,
            threshold=sensor.threshold if sensor else None,
            timeline=[TimelinePoint(window_start=w, score=sc) for w, sc in timeline],
            alerts=[AlertOut.model_validate(a) for a in rows],
        )

    @app.get("/api/graph/{sensor_id}")
    def graph(sensor_id: SensorId, _: Viewer) -> GraphOut:
        snap = store.latest_graph(sensor_id)
        if snap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no graph for this sensor yet")
        return GraphOut(
            sensor_id=sensor_id,
            window_id=snap.window_id,
            window_start=snap.window_start,
            graph=snap.graph,
        )

    @app.post("/api/sensors/{sensor_id}/recalibrate")
    def recalibrate(sensor_id: SensorId, who: Admin, request: Request) -> dict[str, str]:
        if not store.recalibrate(sensor_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sensor not found")
        log.warning(
            "sensor recalibration requested", extra={"sensor": sensor_id, "by": who.username}
        )
        store.audit(
            "sensor_recalibrated",
            actor=who.username,
            target=f"sensor:{sensor_id}",
            client=_client(request),
        )
        return {"status": "learning on next detector start"}

    # ------------------------------------------------------------------ drift and audit

    @app.get("/api/drift")
    def drift(_: Viewer) -> list[DriftOut]:
        """The latest drift report of every sensor."""
        return [DriftOut.from_report(r) for r in store.latest_drift()]

    @app.get("/api/drift/{sensor_id}")
    def drift_history(
        sensor_id: SensorId, _: Viewer, limit: Annotated[int, Query(ge=1, le=5000)] = 1000
    ) -> list[DriftPoint]:
        return [
            DriftPoint(
                window_start=r.window_start,
                baseline_psi=r.baseline_psi,
                training_psi=r.training_psi,
            )
            for r in store.drift_reports(sensor_id, limit)
        ]

    @app.get("/api/audit")
    def audit(
        _: Admin,
        action: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[AuditOut]:
        return [AuditOut.model_validate(e) for e in store.audit_events(limit, action)]

    # ------------------------------------------------------------------ live updates

    @app.websocket("/api/ws")
    async def live(ws: WebSocket) -> None:
        """Pushes {"type": "alert", "alert": ...} and {"type": "window", ...} as the store
        changes.

        Protocol: the client's first message must be {"type": "auth", "token": "<jwt>"} within
        5 s; the server answers {"type": "ready"} or closes with 4401. The token is never put in
        the URL, where it would end up in access logs, proxies and browser history.
        """
        origin = ws.headers.get("origin")
        if origin is not None and origin not in origins:
            # Browsers always send Origin: refuse pages from other sites (cross-site WebSocket
            # hijacking); non-browser clients send none and still need a valid token.
            await ws.close(code=4403)
            return
        # Take the cursors *before* accepting: anything recorded once the client sees the
        # connection open is then guaranteed to be newer and pushed (no race on connect).
        last_id = await asyncio.to_thread(store.max_alert_id)
        seen_windows: dict[str, float | None] = {
            s.id: s.last_window_start for s in await asyncio.to_thread(store.sensors)
        }
        await ws.accept()
        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT_S)
        except (TimeoutError, WebSocketDisconnect, ValueError):
            hello = None
        token = (
            hello.get("token") if isinstance(hello, dict) and hello.get("type") == "auth" else None
        )
        if not isinstance(token, str):
            await ws.close(code=4401)
            return
        who = await asyncio.to_thread(current, token)
        if who is None:
            await ws.close(code=4401)
            return
        await ws.send_json({"type": "ready"})
        WEBSOCKETS.inc()
        checked = time.monotonic()
        try:
            while True:
                await asyncio.sleep(poll_interval_s)
                # A long-lived socket must not outlive its token: expiry or revocation closes it.
                if time.monotonic() - checked >= REVALIDATE_S:
                    checked = time.monotonic()
                    if await asyncio.to_thread(current, token) is None:
                        await ws.close(code=4401)
                        return
                if time.time() >= who.expires_at:
                    await ws.close(code=4401)
                    return
                for a in await asyncio.to_thread(store.alerts, after_id=last_id):
                    last_id = max(last_id, a.id)
                    payload: dict[str, Any] = {
                        "type": "alert",
                        "alert": AlertOut.model_validate(a).model_dump(mode="json"),
                    }
                    await ws.send_json(payload)
                for s in await asyncio.to_thread(store.sensors):
                    if s.last_window_start != seen_windows.get(s.id):
                        seen_windows[s.id] = s.last_window_start
                        await ws.send_json(
                            {
                                "type": "window",
                                "sensor_id": s.id,
                                "window_start": s.last_window_start,
                            }
                        )
        except WebSocketDisconnect:
            return
        finally:
            WEBSOCKETS.dec()

    return app
