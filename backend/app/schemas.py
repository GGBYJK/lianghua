from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str


class DefaultConfigResponse(BaseModel):
    symbol: str
    timeframe: str
    config: dict[str, Any]


class ScanResponse(BaseModel):
    symbol: str
    timeframe: str
    rows: int
    start_time: str | None
    end_time: str | None
    config: dict[str, Any]
    signals: list[dict[str, Any]]
    chart: dict[str, Any]


class WatchPoolItemBase(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(min_length=1, max_length=16)
    enabled: bool = True
    monitor_minutes: int = Field(default=30, ge=1, le=1440)
    trading_sessions: str = Field(default="day,night", max_length=40)
    min_head_to_neck_height: float = Field(default=0.0, ge=0.0)


class WatchPoolItemCreate(WatchPoolItemBase):
    pass


class WatchPoolItemUpdate(WatchPoolItemBase):
    pass


class WatchPoolItemResponse(WatchPoolItemBase):
    id: str
    monitor_started_at: str | None = None
    created_at: str | None
    updated_at: str | None


class HeadShouldersAlertResponse(BaseModel):
    id: str
    watch_pool_id: str
    symbol: str
    timeframe: str
    pattern: str
    alert_type: str
    score: int
    message: str
    unique_key: str
    signal_payload: dict[str, Any]
    chart_payload: dict[str, Any]
    created_at: str | None


class HeadShouldersAlertSummaryResponse(BaseModel):
    id: str
    watch_pool_id: str
    symbol: str
    timeframe: str
    pattern: str
    alert_type: str
    score: int
    message: str
    unique_key: str
    signal_payload: dict[str, Any]
    created_at: str | None


class AlertFeedbackCreate(BaseModel):
    alert_id: str
    note: str = Field(default="", max_length=2000)


class AlertFeedbackResponse(BaseModel):
    id: str
    alert_id: str
    symbol: str
    timeframe: str
    pattern: str
    alert_type: str
    score: int
    message: str
    unique_key: str
    signal_payload: dict[str, Any]
    chart_payload: dict[str, Any]
    feedback_note: str
    alert_created_at: str | None
    created_at: str | None
    updated_at: str | None
