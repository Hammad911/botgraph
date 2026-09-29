"""JWT authentication and role-based access (viewer < analyst < admin).

Tokens carry the user's ``token_version``; the API checks it (and the user's current role and
disabled flag) against the store on every request, so a password change, a disabled account or
"sign out everywhere" revokes every token already issued.
"""

from __future__ import annotations

import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

from botgraph_stream.store import ROLES

ALGORITHM = "HS256"
ISSUER = "botgraph"
MIN_SECRET_BYTES = 32


def token_ttl() -> timedelta:
    return timedelta(minutes=float(os.environ.get("BOTGRAPH_TOKEN_TTL_MINUTES", "480")))


def production() -> bool:
    return os.environ.get("BOTGRAPH_ENV", "development").lower() == "production"


def load_secret(data_dir: Path) -> str:
    """``BOTGRAPH_JWT_SECRET`` if set; otherwise (development only) a random secret generated
    once and kept in ``data/jwt_secret`` (gitignored) so dev logins survive restarts.

    In production (``BOTGRAPH_ENV=production``) the secret is required and must be at least
    32 bytes: every replica has to share it, and a short HMAC key can be brute-forced offline
    from any captured token.
    """
    secret = os.environ.get("BOTGRAPH_JWT_SECRET")
    if secret:
        if production() and len(secret.encode()) < MIN_SECRET_BYTES:
            raise SystemExit(f"BOTGRAPH_JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        return secret
    if production():
        raise SystemExit(
            "BOTGRAPH_ENV=production requires BOTGRAPH_JWT_SECRET (openssl rand -base64 48)"
        )
    path = data_dir / "jwt_secret"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    version: int = 0
    expires_at: float = float("inf")

    def can(self, role: str) -> bool:
        return bool(ROLES.index(self.role) >= ROLES.index(role))


def issue_token(
    secret: str, username: str, role: str, version: int = 0, now: datetime | None = None
) -> str:
    now = now or datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "sub": username,
        "role": role,
        "ver": version,
        "iat": now,
        "exp": now + token_ttl(),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_token(secret: str, token: str) -> Principal | None:
    """The token's principal, or None if it is invalid, expired or has an unknown role."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],  # never trust the token's own alg header
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        return None
    role = claims.get("role")
    if role not in ROLES or not claims.get("sub"):
        return None
    return Principal(
        username=str(claims["sub"]),
        role=str(role),
        version=int(claims.get("ver", 0)),
        expires_at=float(claims["exp"]),
    )


class RateLimiter:
    """Sliding-window limit per key (client address): at most ``limit`` hits per ``window_s``.

    In memory, so per API replica; the per-account lockout in the store is the global limit.
    """

    def __init__(self, limit: int, window_s: float, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if len(self._hits) >= self.max_keys and key not in self._hits:
            self._evict(now)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        return max(1, int(hits[0] + self.window_s - now) + 1) if hits else 1

    def _evict(self, now: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window_s]
        for key in stale:
            del self._hits[key]
        if len(self._hits) >= self.max_keys:  # still full of active keys: drop the oldest half
            for key in list(self._hits)[: self.max_keys // 2]:
                del self._hits[key]
