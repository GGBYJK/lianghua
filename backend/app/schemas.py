from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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

