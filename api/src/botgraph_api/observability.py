"""Request metrics, request ids and access logs for the API (a pure ASGI middleware).

Metrics are labelled by route *template* (``/api/alerts/{alert_id}``), never by the raw path,
so label cardinality stays bounded. They are served by ``botgraph-api serve --metrics-port``
on a separate port, not by the public app.
"""

import logging
import re
import time
import uuid
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUESTS = Counter("botgraph_api_requests_total", "API requests", ["method", "route", "status"])
REQUEST_LATENCY = Histogram(
    "botgraph_api_request_seconds",
    "API request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
LOGINS = Counter("botgraph_api_logins_total", "Login attempts", ["result"])
LOGIN_LOCKOUTS = Counter(
    "botgraph_api_login_lockouts_total", "Accounts locked after repeated wrong passwords"
)
WEBSOCKETS = Gauge("botgraph_api_websockets", "Open live-update WebSockets")
TRIAGE = Counter("botgraph_api_triage_total", "Alert triage updates", ["status"])

log = logging.getLogger("botgraph.api.access")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        # Accept a well-formed id from a proxy, so one request is traceable end to end.
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                message.setdefault("headers", [])
                message["headers"] = [
                    *message["headers"],
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            route: Any = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope["method"]
            REQUESTS.labels(method, template, str(status)).inc()
            REQUEST_LATENCY.labels(method, template).observe(elapsed)
            if template != "/api/health":  # probes every few seconds
                log.info(
                    "request",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "route": template,
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": round(1000 * elapsed, 1),
                        "client": (scope.get("client") or ("", 0))[0],
                    },
                )
