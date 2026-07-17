from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["ADMIN", "TRADER", "VIEWER"] = "TRADER"
    initial_balance: Decimal = Field(default=Decimal("1000000"), ge=0)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal["ACTIVE", "DISABLED"] | None = None
    role: Literal["ADMIN", "TRADER", "VIEWER"] | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class AccountAdjustmentRequest(BaseModel):
    amount: Decimal
    description: str = Field(min_length=1, max_length=255)


class ContractSpecRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(min_length=1, max_length=16)
    name: str = Field(default="", max_length=80)
    multiplier: Decimal = Field(default=Decimal("1"), gt=0)
    price_tick: Decimal = Field(gt=0)
    margin_rate: Decimal = Field(gt=0, le=1)
    fee_mode: Literal["TURNOVER_RATE", "PER_LOT"] = "TURNOVER_RATE"
    fee_value: Decimal = Field(default=Decimal("0"), ge=0)
    fee_close_today_mode: Literal["TURNOVER_RATE", "PER_LOT"] | None = None
    fee_close_today_value: Decimal | None = Field(default=None, ge=0)
    enabled: bool = True


class ManualOpenOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    position_side: Literal["LONG", "SHORT"]
    quantity: int = Field(ge=1, le=100000)
    stop_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=8, max_length=80)


class SignalOpenOrderRequest(BaseModel):
    quantity: int = Field(default=1, ge=1, le=100000)
    stop_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    disable_take_profit: bool = False
    idempotency_key: str = Field(min_length=8, max_length=80)


class ClosePositionRequest(BaseModel):
    quantity: int = Field(ge=1, le=100000)
    idempotency_key: str = Field(min_length=8, max_length=80)


class ExitRuleUpdateRequest(BaseModel):
    stop_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)

