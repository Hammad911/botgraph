"""Persistence for the live pipeline (SQLAlchemy 2; SQLite by default, Postgres in production).

    BOTGRAPH_DB_URL=sqlite:///botgraph.db              (default)
    BOTGRAPH_DB_URL=postgresql+psycopg://user:pw@host/botgraph

Tables: sensors (learning state and calibrated threshold), alerts (warning/alert rows, closed
when cleared, with explanations) and window_stats (one row per scored window).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Float, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DEFAULT_URL = "sqlite:///botgraph.db"


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
    calibrated_at: Mapped[datetime | None] = mapped_column(nullable=True)
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
    raised_at: Mapped[datetime] = mapped_column(default=_now)
    cleared_window_start: Mapped[float | None] = mapped_column(Float, nullable=True)


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


class Store:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("BOTGRAPH_DB_URL", DEFAULT_URL)
        self.engine = create_engine(self.url)
        Base.metadata.create_all(self.engine)

    def thresholds(self) -> dict[str, float]:
        """Calibrated thresholds of active sensors, so a restart skips learning."""
        with Session(self.engine) as s:
            rows = s.scalars(select(Sensor).where(Sensor.state == "active")).all()
            return {r.id: r.threshold for r in rows if r.threshold is not None}

    def record_window(self, detection: dict[str, Any]) -> None:
        hosts = detection["hosts"]
        with Session(self.engine) as s, s.begin():
            s.add(
                WindowStat(
                    sensor_id=detection["sensor_id"],
                    window_id=detection["window_id"],
                    window_start=detection["window_start"],
                    n_flows=detection["n_flows"],
                    n_nodes=detection["n_nodes"],
                    n_edges=detection["n_edges"],
                    n_hosts=len(hosts),
                    max_score=max((h["score"] for h in hosts), default=0.0),
                    latency_ms=detection["latency_ms"],
                )
            )
            sensor = s.get(Sensor, detection["sensor_id"]) or Sensor(id=detection["sensor_id"])
            sensor.last_window_start = detection["window_start"]
            s.add(sensor)

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

    def alerts(self, open_only: bool = False) -> list[Alert]:
        with Session(self.engine) as s:
            query = select(Alert).order_by(Alert.window_start)
            if open_only:
                query = query.where(Alert.cleared_window_start.is_(None))
            return list(s.scalars(query).all())

    def summary(self) -> dict[str, Any]:
        with Session(self.engine) as s:
            return {
                "sensors": s.scalar(select(func.count()).select_from(Sensor)),
                "windows": s.scalar(select(func.count()).select_from(WindowStat)),
                "warnings": s.scalar(select(func.count()).where(Alert.level == "warning")),
                "alerts": s.scalar(select(func.count()).where(Alert.level == "alert")),
            }
