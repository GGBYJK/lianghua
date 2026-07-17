from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import app.trading_service as trading_service
from app.security import hash_password, verify_password
from app.trading_service import (
    QUOTE_STALE_SECONDS,
    _fee,
    _fill_price,
    _rule_triggered,
    _validate_exit_prices,
    build_trade_signal,
)
from app.trading_store import TradingStoreError, provider_symbol


def fresh_snapshot() -> dict[str, object]:
    return {"last_price": Decimal("3501"), "updated_at": datetime.now(timezone.utc)}


def test_password_hash_is_not_plaintext_and_verifies() -> None:
    password_hash = hash_password("strong-password")
    assert password_hash != "strong-password"
    assert verify_password(password_hash, "strong-password") is True
    assert verify_password(password_hash, "wrong-password") is False


def test_signal_direction_and_default_take_profit() -> None:
    alert = {
        "id": "12",
        "symbol": "rb2610",
        "pattern": "head_shoulders_top",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signal_payload": {
            "pattern_metrics": {
                "trigger_price": 3500,
                "stop": 3530,
                "target": 3420,
                "rr": 2.2,
            },
        },
    }
    result = build_trade_signal(alert, fresh_snapshot())
    assert result["direction"] == "SHORT"
    assert result["suggested_take_profit_price"] == 3420
    assert result["tradeable"] is True


def test_low_rr_signal_keeps_target_as_reference_but_disables_default() -> None:
    alert = {
        "id": "13",
        "symbol": "m2609",
        "pattern": "inverse_head_shoulders",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signal_payload": {
            "pattern_metrics": {
                "trigger_price": 3100,
                "stop": 3070,
                "target": 3130,
                "rr": 1.0,
            },
        },
    }
    result = build_trade_signal(alert, fresh_snapshot())
    assert result["direction"] == "LONG"
    assert result["suggested_target_price"] == 3130
    assert result["suggested_take_profit_price"] is None


def test_signal_with_stale_quote_cannot_trade() -> None:
    alert = {
        "id": "14",
        "symbol": "rb2610",
        "pattern": "head_shoulders_top",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signal_payload": {"pattern_metrics": {"trigger_price": 3500, "stop": 3530}},
    }
    snapshot = {
        "last_price": Decimal("3501"),
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=QUOTE_STALE_SECONDS + 1),
    }

    result = build_trade_signal(alert, snapshot)

    assert result["tradeable"] is False
    assert result["quote_fresh"] is False
    assert result["tradeable_reason"] == "行情已过期，暂时禁止成交"


def test_trade_signals_are_sorted_by_detection_time_descending(monkeypatch: pytest.MonkeyPatch) -> None:
    newer = {
        "id": "21",
        "symbol": "rb2610",
        "pattern": "head_shoulders_top",
        "created_at": "2026-07-16T10:05:00+00:00",
        "signal_payload": {"pattern_metrics": {"trigger_price": 3500, "stop": 3530}},
    }
    older = {**newer, "id": "20", "created_at": "2026-07-16T10:00:00+00:00"}
    monkeypatch.setattr(trading_service, "list_head_shoulders_alerts", lambda **_: [older, newer])
    monkeypatch.setattr(
        trading_service,
        "list_market_snapshots",
        lambda _: [{"symbol": "rb2610", **fresh_snapshot()}],
    )

    result = trading_service.list_trade_signals()

    assert [signal["id"] for signal in result] == ["21", "20"]


def test_market_fill_applies_adverse_slippage() -> None:
    assert _fill_price(Decimal("100"), "BUY", Decimal("0.5"), Decimal("1"))[0] == Decimal("100.5")
    assert _fill_price(Decimal("100"), "SELL", Decimal("0.5"), Decimal("1"))[0] == Decimal("99.5")


def test_turnover_rate_fee_uses_trade_value() -> None:
    spec = {"multiplier": Decimal("10"), "fee_mode": "TURNOVER_RATE", "fee_value": Decimal("0.0005")}

    assert _fee(Decimal("100"), 2, spec, "OPEN") == Decimal("1")


def test_per_lot_fee_uses_quantity_only() -> None:
    spec = {"multiplier": Decimal("10"), "fee_mode": "PER_LOT", "fee_value": Decimal("3.2")}

    assert _fee(Decimal("100"), 2, spec, "CLOSE") == Decimal("6.4")


def test_close_today_fee_uses_the_special_fee_when_configured() -> None:
    spec = {
        "multiplier": Decimal("10"),
        "fee_mode": "TURNOVER_RATE",
        "fee_value": Decimal("0.000075"),
        "fee_close_today_mode": "TURNOVER_RATE",
        "fee_close_today_value": Decimal("0.00015"),
    }

    assert _fee(Decimal("100"), 2, spec, "CLOSE") == Decimal("0.15")
    assert _fee(Decimal("100"), 2, spec, "CLOSE_TODAY") == Decimal("0.3")


def test_exit_prices_must_be_on_correct_side() -> None:
    _validate_exit_prices("LONG", Decimal("100"), Decimal("95"), Decimal("110"))
    _validate_exit_prices("SHORT", Decimal("100"), Decimal("105"), Decimal("90"))
    with pytest.raises(TradingStoreError):
        _validate_exit_prices("LONG", Decimal("100"), Decimal("101"), None)
    with pytest.raises(TradingStoreError):
        _validate_exit_prices("SHORT", Decimal("100"), None, Decimal("101"))


def test_exit_rule_trigger_direction() -> None:
    assert _rule_triggered("STOP_LOSS", "LONG", Decimal("94"), Decimal("95")) is True
    assert _rule_triggered("TAKE_PROFIT", "LONG", Decimal("110"), Decimal("110")) is True
    assert _rule_triggered("STOP_LOSS", "SHORT", Decimal("105"), Decimal("105")) is True
    assert _rule_triggered("TAKE_PROFIT", "SHORT", Decimal("89"), Decimal("90")) is True


def test_provider_symbol_restores_exchange_specific_case() -> None:
    assert provider_symbol("dce.v2609") == "DCE.v2609"
    assert provider_symbol("shfe.rb2610") == "SHFE.rb2610"
    assert provider_symbol("czce.ur609") == "CZCE.UR609"
