"""HTTP hardening for the API: security headers and a request body size limit."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The API serves JSON only, so the strictest policy applies: nothing may be loaded, framed or
# sniffed, and responses (alerts, explanations, host timelines) are never cached.
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cache-control", b"no-store"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts: bool = False, max_body_bytes: int = 64 * 1024) -> None:
        self.app = app
        self.headers = SECURITY_HEADERS + ([HSTS] if hsts else [])
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_body_bytes):
            await self._reject(send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:  # chunked bodies have no content-length
                    raise _BodyTooLargeError
            return message

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [
                    *message.get("headers", []),
                    *((k, v) for k, v in self.headers if k not in existing),
                ]
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except _BodyTooLargeError:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = b'{"detail":"request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    *self.headers,
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLargeError(Exception):
    pass
