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
    timeframes: list[Literal["1m", "3m", "5m", "15m", "30m", "1h", "1d"]] = Field(default_factory=lambda: ["3m", "5m"], min_length=1, max_length=7)
    kline_count: int = Field(default=1000, ge=120, le=8000)
    max_holding_bars: int | None = Field(default=None, ge=1, le=500)
    entry_conditions: list[Literal[
        "head_shoulders_top:right_shoulder_confirmed",
        "inverse_head_shoulders:right_shoulder_confirmed",
    ]] = Field(default_factory=list, max_length=2)
    other_entry_conditions: list[Literal[
        "head_shoulders_top:head_shoulders_top_pullback",
        "inverse_head_shoulders:inverse_head_shoulders_pullback",
    ]] = Field(default_factory=list, max_length=2)
    min_pattern_score: int = Field(default=75, ge=0, le=100)
    min_trend_score: int = Field(default=65, ge=0, le=100)
    other_min_pattern_score: int = Field(default=80, ge=0, le=100)
    other_max_trend_score: int = Field(default=35, ge=0, le=100)
    stop_loss_qtr_multiplier: float = Field(default=0.5, gt=0, le=20)
    take_profit_rules: list[TakeProfitRuleRequest] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "BacktestCreateRequest":
        self.symbols = list(dict.fromkeys(item.strip() for item in self.symbols if item.strip()))
        self.timeframes = list(dict.fromkeys(self.timeframes))
        self.entry_conditions = list(dict.fromkeys(self.entry_conditions))
        self.other_entry_conditions = list(dict.fromkeys(self.other_entry_conditions))
        keys = [rule.key for rule in self.take_profit_rules]
        if len(keys) != len(set(keys)):
            raise ValueError("止盈条件 key 不能重复")
        if not self.symbols:
            raise ValueError("至少选择一个回测品种")
        if not self.entry_conditions and not self.other_entry_conditions:
            raise ValueError("至少选择一个进场形态")
        if len(self.symbols) * len(self.timeframes) > 50:
            raise ValueError("单次回测最多包含 50 个品种周期组合")
        return self


class BacktestSymbolGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    symbols: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def normalize(self) -> "BacktestSymbolGroupCreateRequest":
        self.name = self.name.strip()
        self.symbols = list(dict.fromkeys(item.strip() for item in self.symbols if item.strip()))
        if not self.name:
            raise ValueError("分组名称不能为空")
        if not self.symbols:
            raise ValueError("分组至少需要包含一个品种")
        return self


class BacktestSymbolGroupUpdateRequest(BacktestSymbolGroupCreateRequest):
    pass
