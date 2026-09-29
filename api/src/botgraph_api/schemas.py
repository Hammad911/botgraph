"""Response and request models (the API contract the web console relies on)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["open", "investigating", "resolved", "false_positive"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    username: str
    role: str


class Me(BaseModel):
    username: str
    role: str


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sensor_id: str
    ip: str
    level: str
    window_start: float
    score: float
    hits: int
    rule_k: int
    rule_n: int
    threshold: float
    raised_at: datetime
    cleared_window_start: float | None
    status: str
    assignee: str | None
    note: str | None
    updated_at: datetime | None


class AlertDetail(AlertOut):
    explanation: dict[str, Any] | None


class AlertUpdate(BaseModel):
    status: Status | None = None
    assignee: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


class SensorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: str
    threshold: float | None
    model_threshold: float | None
    baseline_scores: int
    calibrated_at: datetime | None
    last_window_start: float | None


class TopHost(BaseModel):
    sensor_id: str
    ip: str
    peak_score: float
    open_alert: bool


class TrendPoint(BaseModel):
    hour_start: float  # event time, epoch seconds
    warnings: int
    alerts: int


class Overview(BaseModel):
    latest_window_start: float | None
    sensors: int
    active_sensors: int
    windows_scored: int
    hosts_monitored: int  # distinct hosts scored in the last 15 minutes of event time
    open_alerts: int
    open_warnings: int
    trend: list[TrendPoint]
    top_hosts: list[TopHost]


class TimelinePoint(BaseModel):
    window_start: float
    score: float


class HostOut(BaseModel):
    sensor_id: str
    ip: str
    threshold: float | None
    timeline: list[TimelinePoint]
    alerts: list[AlertOut]


class GraphOut(BaseModel):
    sensor_id: str
    window_id: str
    window_start: float
    graph: dict[str, Any]
