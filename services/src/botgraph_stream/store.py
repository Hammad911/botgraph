"""Persistence for the live pipeline and the dashboard (SQLAlchemy 2 + Alembic).

    BOTGRAPH_DB_URL=sqlite:///data/botgraph.db          (default, WAL mode)
    BOTGRAPH_DB_URL=postgresql+psycopg://user:pw@host/botgraph

Tables:
* sensors         learning state and calibrated threshold per monitored network
* alerts          warning/alert rows with explanations and triage (status, assignee, note)
* window_stats    one row per scored window
* host_scores     every internal host's score per window (host timelines; pruned by retention)
* graph_snapshots latest compact window graph per sensor (the live network map)
* users           dashboard accounts (admin / analyst / viewer)

The schema is managed by Alembic migrations (``botgraph_stream/migrations``); ``Store`` upgrades
the database on startup, including databases created before migrations existed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    create_engine,
    delete,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DEFAULT_URL = "sqlite:///botgraph.db"
ALERT_STATUSES = ("open", "investigating", "resolved", "false_positive")
ROLES = ("viewer", "analyst", "admin")  # ascending privileges
MIGRATIONS = Path(__file__).parent / "migrations"


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Sensor(Base):
    __tablename__ = "sensors"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="learning")  # learning | active
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_scores: Mapped[int] = mapped_column(Integer, default=0)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_window_start: Mapped[float | None] = mapped_column(Float, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(String(128), index=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16))  # warning | alert
    window_start: Mapped[float] = mapped_column(Float)  # event time that triggered it
    score: Mapped[float] = mapped_column(Float)
    hits: Mapped[int] = mapped_column(Integer)
    rule_k: Mapped[int] = mapped_column(Integer)
    rule_n: Mapped[int] = mapped_column(Integer)
    threshold: Mapped[float] = mapped_column(Float)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    cleared_window_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WindowStat(Base):
    __tablename__ = "window_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(String(128), index=True)
    window_id: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[float] = mapped_column(Float, index=True)
    n_flows: Mapped[int] = mapped_column(Integer)
    n_nodes: Mapped[int] = mapped_column(Integer)
    n_edges: Mapped[int] = mapped_column(Integer)
    n_hosts: Mapped[int] = mapped_column(Integer)
    max_score: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)


class HostScore(Base):
    __tablename__ = "host_scores"
    __table_args__ = (
        Index("ix_host_scores_host", "sensor_id", "ip", "window_start"),
        Index("ix_host_scores_window", "sensor_id", "window_start"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(String(128))
    ip: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)


class GraphSnapshot(Base):
    __tablename__ = "graph_snapshots"
    sensor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_id: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[float] = mapped_column(Float)
    graph: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))  # viewer | analyst | admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """scrypt (stdlib) with a random salt: ``scrypt$n$r$p$salt$hash``."""
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${n}${r}${p}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(
        password.encode(),
        salt=base64.b64decode(salt),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=32,
    )
    return hmac.compare_digest(candidate, base64.b64decode(digest))


# --------------------------------------------------------------------------- migrations


def migrate(engine: Engine) -> None:
    """Upgrade the schema to the latest Alembic revision.

    A database created before migrations existed (tables present, no alembic_version) is
    stamped at the initial revision first, so it upgrades in place instead of failing.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        tables = inspect(connection)
        if tables.has_table("sensors") and not tables.has_table("alembic_version"):
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")


def _sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")  # dashboard reads while the detector writes
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


# --------------------------------------------------------------------------- store


class Store:
    def __init__(self, url: str | None = None, host_score_retention_s: float = 7 * 86400) -> None:
        self.url = url or os.environ.get("BOTGRAPH_DB_URL", DEFAULT_URL)
        self.engine = create_engine(self.url)
        if self.engine.dialect.name == "sqlite":
            _sqlite_pragmas(self.engine)
        migrate(self.engine)
        self.host_score_retention_s = host_score_retention_s
        self._windows_since_prune = 0

    # ---------------------------------------------------------------- pipeline writes

    def thresholds(self) -> dict[str, float]:
        """Calibrated thresholds of active sensors, so a restart skips learning."""
        with Session(self.engine) as s:
            rows = s.scalars(select(Sensor).where(Sensor.state == "active")).all()
            return {r.id: r.threshold for r in rows if r.threshold is not None}

    def record_window(self, detection: dict[str, Any]) -> None:
        sensor_id, start = detection["sensor_id"], float(detection["window_start"])
        hosts = detection["hosts"]
        with Session(self.engine) as s, s.begin():
            s.add(
                WindowStat(
                    sensor_id=sensor_id,
                    window_id=detection["window_id"],
                    window_start=start,
                    n_flows=detection["n_flows"],
                    n_nodes=detection["n_nodes"],
                    n_edges=detection["n_edges"],
                    n_hosts=len(hosts),
                    max_score=max((h["score"] for h in hosts), default=0.0),
                    latency_ms=detection["latency_ms"],
                )
            )
            if hosts:
                s.execute(
                    HostScore.__table__.insert(),
                    [
                        {
                            "sensor_id": sensor_id,
                            "ip": h["ip"],
                            "window_start": start,
                            "score": h["score"],
                        }
                        for h in hosts
                    ],
                )
            if detection.get("graph") is not None:
                snapshot = s.get(GraphSnapshot, sensor_id) or GraphSnapshot(sensor_id=sensor_id)
                snapshot.window_id = detection["window_id"]
                snapshot.window_start = start
                snapshot.graph = detection["graph"]
                snapshot.updated_at = _now()
                s.add(snapshot)
            sensor = s.get(Sensor, sensor_id) or Sensor(id=sensor_id)
            sensor.last_window_start = start
            s.add(sensor)
        self._windows_since_prune += 1
        if self._windows_since_prune >= 60:
            self._prune(sensor_id, start)

    def _prune(self, sensor_id: str, latest: float) -> None:
        self._windows_since_prune = 0
        with Session(self.engine) as s, s.begin():
            s.execute(
                delete(HostScore).where(
                    HostScore.sensor_id == sensor_id,
                    HostScore.window_start < latest - self.host_score_retention_s,
                )
            )

    def record_event(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        with Session(self.engine) as s, s.begin():
            if kind == "calibrated":
                sensor = s.get(Sensor, event["sensor_id"]) or Sensor(id=event["sensor_id"])
                sensor.state = "active"
                sensor.threshold = event["threshold"]
                sensor.model_threshold = event["model_threshold"]
                sensor.baseline_scores = event["baseline_scores"]
                sensor.calibrated_at = _now()
                s.add(sensor)
            elif kind in ("warning", "alert"):
                s.add(
                    Alert(
                        sensor_id=event["sensor_id"],
                        ip=event["ip"],
                        level=kind,
                        window_start=event["window_start"],
                        score=event["score"],
                        hits=event["hits"],
                        rule_k=event["rule"]["k"],
                        rule_n=event["rule"]["n"],
                        threshold=event["threshold"],
                        explanation=event.get("explanation"),
                    )
                )
            elif kind == "cleared":
                open_alerts = s.scalars(
                    select(Alert).where(
                        Alert.sensor_id == event["sensor_id"],
                        Alert.ip == event["ip"],
                        Alert.cleared_window_start.is_(None),
                    )
                ).all()
                for alert in open_alerts:
                    alert.cleared_window_start = event["window_start"]

    def recalibrate(self, sensor_id: str) -> bool:
        """Put a sensor back into learning mode (takes effect on the next start)."""
        with Session(self.engine) as s, s.begin():
            sensor = s.get(Sensor, sensor_id)
            if sensor is None:
                return False
            sensor.state, sensor.threshold, sensor.calibrated_at = "learning", None, None
            return True

    # ---------------------------------------------------------------- reads

    def alerts(
        self,
        open_only: bool = False,
        status: str | None = None,
        level: str | None = None,
        sensor_id: str | None = None,
        ip: str | None = None,
        after_id: int | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[Alert]:
        query = select(Alert)
        if open_only:
            query = query.where(Alert.cleared_window_start.is_(None))
        if status:
            query = query.where(Alert.status == status)
        if level:
            query = query.where(Alert.level == level)
        if sensor_id:
            query = query.where(Alert.sensor_id == sensor_id)
        if ip:
            query = query.where(Alert.ip == ip)
        if after_id is not None:
            query = query.where(Alert.id > after_id)
        order = (
            (Alert.window_start.desc(), Alert.id.desc())
            if newest_first
            else (Alert.window_start, Alert.id)
        )
        query = query.order_by(*order)
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine) as s:
            return list(s.scalars(query).all())

    def get_alert(self, alert_id: int) -> Alert | None:
        with Session(self.engine) as s:
            return s.get(Alert, alert_id)

    def update_alert(
        self,
        alert_id: int,
        status: str | None = None,
        assignee: str | None = None,
        note: str | None = None,
    ) -> Alert | None:
        if status is not None and status not in ALERT_STATUSES:
            raise ValueError(f"status must be one of {ALERT_STATUSES}")
        with Session(self.engine, expire_on_commit=False) as s, s.begin():
            alert = s.get(Alert, alert_id)
            if alert is None:
                return None
            if status is not None:
                alert.status = status
            if assignee is not None:
                alert.assignee = assignee or None
            if note is not None:
                alert.note = note or None
            alert.updated_at = _now()
            return alert

    def sensors(self) -> list[Sensor]:
        with Session(self.engine) as s:
            return list(s.scalars(select(Sensor).order_by(Sensor.id)).all())

    def host_timeline(
        self, sensor_id: str, ip: str, limit: int = 1440
    ) -> list[tuple[float, float]]:
        """(window_start, score) for one host, oldest first, at most ``limit`` latest points."""
        with Session(self.engine) as s:
            rows = s.execute(
                select(HostScore.window_start, HostScore.score)
                .where(HostScore.sensor_id == sensor_id, HostScore.ip == ip)
                .order_by(HostScore.window_start.desc())
                .limit(limit)
            ).all()
        return [(float(w), float(sc)) for w, sc in reversed(rows)]

    def latest_graph(self, sensor_id: str) -> GraphSnapshot | None:
        with Session(self.engine) as s:
            return s.get(GraphSnapshot, sensor_id)

    def latest_window_start(self) -> float | None:
        with Session(self.engine) as s:
            return s.scalar(select(func.max(WindowStat.window_start)))

    def top_hosts(self, since: float, limit: int = 10) -> list[tuple[str, str, float]]:
        """(sensor, ip, peak score) of the riskiest hosts in windows since ``since``."""
        with Session(self.engine) as s:
            rows = s.execute(
                select(HostScore.sensor_id, HostScore.ip, func.max(HostScore.score).label("peak"))
                .where(HostScore.window_start >= since)
                .group_by(HostScore.sensor_id, HostScore.ip)
                .order_by(func.max(HostScore.score).desc())
                .limit(limit)
            ).all()
        return [(str(a), str(b), float(c)) for a, b, c in rows]

    def count_alerts(self, level: str | None = None, statuses: tuple[str, ...] = ()) -> int:
        query = select(func.count()).select_from(Alert)
        if level:
            query = query.where(Alert.level == level)
        if statuses:
            query = query.where(Alert.status.in_(statuses))
        with Session(self.engine) as s:
            return int(s.scalar(query) or 0)

    def hosts_monitored(self, since: float) -> int:
        """Distinct internal hosts scored in windows starting at or after ``since``."""
        with Session(self.engine) as s:
            query = select(func.count(func.distinct(HostScore.sensor_id + "|" + HostScore.ip)))
            return int(s.scalar(query.where(HostScore.window_start >= since)) or 0)

    def summary(self) -> dict[str, Any]:
        with Session(self.engine) as s:
            return {
                "sensors": s.scalar(select(func.count()).select_from(Sensor)),
                "windows": s.scalar(select(func.count()).select_from(WindowStat)),
                "warnings": s.scalar(select(func.count()).where(Alert.level == "warning")),
                "alerts": s.scalar(select(func.count()).where(Alert.level == "alert")),
            }

    # ---------------------------------------------------------------- users

    def create_user(self, username: str, password: str, role: str) -> User:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        with Session(self.engine, expire_on_commit=False) as s, s.begin():
            user = User(username=username, password_hash=hash_password(password), role=role)
            s.add(user)
            return user

    def authenticate(self, username: str, password: str) -> User | None:
        with Session(self.engine) as s:
            user = s.scalar(select(User).where(User.username == username))
        if user is None or not verify_password(password, user.password_hash):
            return None
        return user

    def get_user(self, username: str) -> User | None:
        with Session(self.engine) as s:
            return s.scalar(select(User).where(User.username == username))
