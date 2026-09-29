"""JWT authentication and role-based access (viewer < analyst < admin)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

from botgraph_stream.store import ROLES

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)


def load_secret(data_dir: Path) -> str:
    """``BOTGRAPH_JWT_SECRET`` if set (production); otherwise a random secret generated once and
    kept in ``data/jwt_secret`` (gitignored) so dev logins survive restarts."""
    secret = os.environ.get("BOTGRAPH_JWT_SECRET")
    if secret:
        return secret
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

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def issue_token(secret: str, username: str, role: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    claims = {"sub": username, "role": role, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_token(secret: str, token: str) -> Principal | None:
    """The token's principal, or None if it is invalid, expired or has an unknown role."""
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    role = claims.get("role")
    if role not in ROLES or not claims.get("sub"):
        return None
    return Principal(username=str(claims["sub"]), role=str(role))
