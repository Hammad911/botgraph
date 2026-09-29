"""FastAPI application: REST endpoints for the console plus a WebSocket for live updates.

The API reads the same store the pipeline writes (SQLite in WAL mode or Postgres); it never
talks to the detector directly, so it can run, restart and scale independently.

No ``from __future__ import annotations`` here: FastAPI resolves dependency annotations at
runtime, and the role aliases (``Viewer`` etc.) are local to ``create_app``.
"""

import asyncio
import os
from collections import Counter
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from botgraph_api.auth import Principal, issue_token, read_token
from botgraph_api.schemas import (
    AlertDetail,
    AlertOut,
    AlertUpdate,
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
from botgraph_stream.store import Store

OPEN = ("open", "investigating")
HOUR = 3600.0
RECENT_S = 15 * 60.0


def create_app(store: Store, secret: str, poll_interval_s: float = 2.0) -> FastAPI:
    app = FastAPI(title="BotGraph API", version="0.1.0")
    origins = os.environ.get("BOTGRAPH_CORS_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    bearer = HTTPBearer(auto_error=False)

    def principal(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        who = read_token(secret, creds.credentials) if creds else None
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
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(body: LoginRequest) -> TokenResponse:
        user = store.authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
        return TokenResponse(
            access_token=issue_token(secret, user.username, user.role),
            username=user.username,
            role=user.role,
        )

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
    def triage(alert_id: int, body: AlertUpdate, _: Analyst) -> AlertDetail:
        row = store.update_alert(alert_id, body.status, body.assignee, body.note)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
        return AlertDetail.model_validate(row)

    @app.get("/api/hosts/{sensor_id}/{ip}")
    def host(
        sensor_id: str, ip: str, _: Viewer, limit: Annotated[int, Query(ge=1, le=10_000)] = 1440
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
    def graph(sensor_id: str, _: Viewer) -> GraphOut:
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
    def recalibrate(sensor_id: str, _: Admin) -> dict[str, str]:
        if not store.recalibrate(sensor_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sensor not found")
        return {"status": "learning on next detector start"}

    # ------------------------------------------------------------------ live updates

    @app.websocket("/api/ws")
    async def live(ws: WebSocket, token: str = "") -> None:
        """Pushes {"type": "alert", "alert": ...} and {"type": "window", ...} as the store
        changes. Browsers cannot set headers on WebSockets, so the token is a query param."""
        if read_token(secret, token) is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        existing = await asyncio.to_thread(store.alerts, newest_first=True, limit=1)
        last_id = existing[0].id if existing else 0
        seen_windows: dict[str, float | None] = {
            s.id: s.last_window_start for s in await asyncio.to_thread(store.sensors)
        }
        try:
            while True:
                await asyncio.sleep(poll_interval_s)
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

    return app
