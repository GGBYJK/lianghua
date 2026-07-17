from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TakeProfitRuleRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    type: Literal["PATTERN_TARGET", "RR", "QTR"]
    multiplier: float | None = Field(default=None, gt=0, le=20)

    @model_validator(mode="after")
    def validate_multiplier(self) -> "TakeProfitRuleRequest":
        if self.type in {"RR", "QTR"} and self.multiplier is None:
            raise ValueError("RR 和 QTR 止盈条件必须填写倍数")
        return self


class BacktestCreateRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    symbols: list[str] = Field(min_length=1, max_length=20)
    timeframes: list[Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]] = Field(min_length=1, max_length=7)
    kline_count: int = Field(default=240, ge=120, le=8000)
    max_holding_bars: int = Field(default=60, ge=1, le=500)
    patterns: list[Literal["head_shoulders_top", "inverse_head_shoulders"]] = Field(min_length=1, max_length=2)
    alert_types: list[
        Literal["right_shoulder_confirmed", "head_shoulders_top_pullback", "inverse_head_shoulders_pullback"]
    ] = Field(min_length=1, max_length=3)
    take_profit_rules: list[TakeProfitRuleRequest] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "BacktestCreateRequest":
        self.symbols = list(dict.fromkeys(item.strip() for item in self.symbols if item.strip()))
        self.timeframes = list(dict.fromkeys(self.timeframes))
        self.patterns = list(dict.fromkeys(self.patterns))
        self.alert_types = list(dict.fromkeys(self.alert_types))
        keys = [rule.key for rule in self.take_profit_rules]
        if len(keys) != len(set(keys)):
            raise ValueError("止盈条件 key 不能重复")
        if not self.symbols:
            raise ValueError("至少选择一个回测品种")
        if len(self.symbols) * len(self.timeframes) > 50:
            raise ValueError("单次回测最多包含 50 个品种周期组合")
        return self
