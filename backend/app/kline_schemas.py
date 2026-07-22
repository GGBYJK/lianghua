from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


KlineTimeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]


class KlineDatasetCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframes: list[KlineTimeframe] = Field(default_factory=list)
    timeframe: KlineTimeframe | None = None
    target_count: int = Field(default=10000, ge=120, le=10000)
    auto_update: bool = True

    @model_validator(mode="after")
    def normalize_symbol(self) -> "KlineDatasetCreateRequest":
        self.symbol = self.symbol.strip()
        if not self.symbol:
            raise ValueError("品种不能为空")
        selected = list(dict.fromkeys([
            *self.timeframes,
            *([self.timeframe] if self.timeframe else []),
        ]))
        if not selected:
            raise ValueError("至少需要选择一个K线周期")
        self.timeframes = selected
        return self


class KlineDatasetUpdateRequest(BaseModel):
    target_count: int | None = Field(default=None, ge=120, le=10000)
    auto_update: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "KlineDatasetUpdateRequest":
        if self.target_count is None and self.auto_update is None:
            raise ValueError("至少需要修改一个字段")
        return self
