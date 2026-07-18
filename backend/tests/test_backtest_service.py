from __future__ import annotations

import pandas as pd
import pytest
from datetime import datetime

from app.backtest_schemas import BacktestCreateRequest, BacktestSymbolGroupCreateRequest
from app.backtest_service import _filter_backtest_signals, _position_key, _simulate_order, _simulate_symbol_orders, _stop_price, _summaries
from app.backtest_store import _run_dict, default_backtest_name
from app.strategy import HeadShoulderTopConfig, should_emit_pullback_alert
from app.trading_store import with_utc_timestamps


def frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame({
        "datetime": pd.date_range("2026-07-17 09:00:00", periods=len(rows), freq="5min"),
        "open": [item[0] for item in rows],
        "high": [item[1] for item in rows],
        "low": [item[2] for item in rows],
        "close": [item[3] for item in rows],
        "volume": [100] * len(rows),
    })


def long_signal() -> dict[str, object]:
    return {
        "symbol": "rb2610",
        "timeframe": "5m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "score": 82,
        "pattern_score": 76,
        "qtr": 4,
        "retest_time": "2026-07-17T09:00:00",
        "right_shoulder": {"time": "2026-07-17T09:00:00", "price": 100},
        "right_neck": {"time": "2026-07-17T08:55:00", "price": 105},
        "head": {"time": "2026-07-17T08:50:00"},
        "pattern_metrics": {"trigger_price": 100, "stop": 95, "target": 112},
    }


def simulate(data: pd.DataFrame, rule: dict[str, object], max_holding_bars: int | None = 3) -> dict[str, object]:
    return _simulate_order(
        run_id="run",
        series_id="series",
        frame=data,
        signal=long_signal(),
        rule=rule,
        max_holding_bars=max_holding_bars,
        contract=None,
    )


def test_same_bar_stop_and_target_uses_stop_first() -> None:
    order = simulate(
        frame([(100, 101, 99, 100), (100, 106, 94, 101), (101, 102, 99, 100)]),
        {"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1},
    )

    assert order["status"] == "CLOSED"
    assert order["exit_reason"] == "STOP_LOSS"
    assert float(order["exit_price"]) == 101
    assert float(order["r_multiple"]) == pytest.approx(0.5)


def test_position_key_groups_contract_months_by_product() -> None:
    assert _position_key("DCE.a2609") == "dce.a"
    assert _position_key("SHFE.au2608") == "shfe.au"


def test_entry_and_exit_use_the_trigger_candle_close() -> None:
    order = simulate(
        frame([(100, 103, 99, 102), (102, 110, 100, 108), (108, 109, 107, 108)]),
        {"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1},
    )

    assert float(order["entry_price"]) == 102
    assert float(order["target_price"]) == 106
    assert order["exit_reason"] == "TAKE_PROFIT"
    assert float(order["exit_price"]) == 108


def test_qtr_rule_hits_take_profit() -> None:
    order = simulate(
        frame([(100, 101, 99, 100), (100, 104.2, 99, 104), (104, 105, 103, 104)]),
        {"key": "qtr-1", "label": "1 QTR", "type": "QTR", "multiplier": 1},
    )

    assert float(order["target_price"]) == 104
    assert order["exit_reason"] == "TAKE_PROFIT"
    assert float(order["r_multiple"]) == pytest.approx(2)


def test_head_shoulders_pullback_uses_long_direction_for_fake_breakout() -> None:
    signal = long_signal()
    signal["pattern"] = "head_shoulders_top"
    signal["alert_type"] = "head_shoulders_top_pullback"
    signal["right_neck"] = {"time": "2026-07-17T08:55:00", "price": 99}
    order = _simulate_order(
        run_id="run",
        series_id="series",
        frame=frame([(100, 101, 99, 100), (100, 106, 99, 105), (105, 106, 104, 105)]),
        signal=signal,
        rule={"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1},
        max_holding_bars=3,
        contract=None,
    )

    assert order["direction"] == "LONG"
    assert order["status"] == "CLOSED"


def signal_for_bar(index: int, head_time: str, pattern_score: int = 76, trend_score: int = 82) -> dict[str, object]:
    signal = long_signal()
    time = (pd.Timestamp("2026-07-17 09:00:00") + pd.Timedelta(index * 5, unit="min")).isoformat()
    signal["retest_time"] = time
    signal["right_shoulder"] = {"time": time, "price": 100}
    signal["head"] = {"time": head_time}
    signal["pattern_score"] = pattern_score
    signal["score"] = trend_score
    return signal


def symbol_orders(
    data: pd.DataFrame,
    signals: list[dict[str, object]],
    max_holding_bars: int | None = 3,
) -> list[dict[str, object]]:
    return _simulate_symbol_orders(
        run_id="run",
        events=[{"series_id": "series", "frame": data, "signal": signal, "contract": None} for signal in signals],
        rules=[{"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1}],
        max_holding_bars=max_holding_bars,
        stop_loss_qtr_multiplier=0.5,
    )


def test_stop_qtr_multiplier_uses_the_correct_structure_anchor() -> None:
    normal_top = {**long_signal(), "pattern": "head_shoulders_top", "alert_type": "right_shoulder_confirmed"}
    top_pullback = {**long_signal(), "pattern": "head_shoulders_top", "alert_type": "head_shoulders_top_pullback"}
    inverse_pullback = {**long_signal(), "pattern": "inverse_head_shoulders", "alert_type": "inverse_head_shoulders_pullback"}

    assert _stop_price(normal_top, "SHORT", 0.5) == 102
    assert _stop_price(long_signal(), "LONG", 0.5) == 98
    assert _stop_price(top_pullback, "LONG", 0.5) == 103
    assert _stop_price(inverse_pullback, "SHORT", 0.5) == 107


def test_symbol_sequence_blocks_other_signals_while_a_position_is_open() -> None:
    data = frame([
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 103, 99, 102),
        (100, 101, 99, 100),
        (100, 103, 99, 102),
    ])
    first = signal_for_bar(0, "2026-07-17T08:30:00")
    blocked = signal_for_bar(1, "2026-07-17T08:35:00", pattern_score=95)
    next_head = signal_for_bar(3, "2026-07-17T08:40:00")

    orders = symbol_orders(data, [first, blocked, next_head])

    assert len(orders) == 2
    assert [order["signal_key"] for order in orders] == [
        "rb2610|5m|inverse_head_shoulders|right_shoulder_confirmed|2026-07-17T08:30:00|2026-07-17T09:00:00",
        "rb2610|5m|inverse_head_shoulders|right_shoulder_confirmed|2026-07-17T08:40:00|2026-07-17T09:15:00",
    ]


def test_same_head_requires_a_higher_score_after_a_stop() -> None:
    data = frame([
        (100, 101, 99, 100),
        (100, 101, 97, 99),
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 103, 99, 102),
        (100, 101, 99, 100),
        (100, 103, 99, 102),
    ])
    head_time = "2026-07-17T08:30:00"
    stopped = signal_for_bar(0, head_time, pattern_score=70, trend_score=70)
    unchanged = signal_for_bar(2, head_time, pattern_score=70, trend_score=70)
    improved = signal_for_bar(3, head_time, pattern_score=71, trend_score=70)
    after_take_profit = signal_for_bar(5, head_time, pattern_score=90, trend_score=90)

    orders = symbol_orders(data, [stopped, unchanged, improved, after_take_profit])

    assert len(orders) == 3
    assert [order["exit_reason"] for order in orders] == ["STOP_LOSS", "TAKE_PROFIT", "TAKE_PROFIT"]
    assert orders[1]["signal_key"].endswith("|2026-07-17T09:15:00")


def test_time_exit_releases_the_symbol_for_a_new_head() -> None:
    data = frame([
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 101, 99, 100),
    ])
    first = signal_for_bar(0, "2026-07-17T08:30:00")
    next_head = signal_for_bar(2, "2026-07-17T08:40:00")

    orders = symbol_orders(data, [first, next_head], max_holding_bars=1)

    assert len(orders) == 2
    assert [order["exit_reason"] for order in orders] == ["TIME_EXIT", "TIME_EXIT"]


def test_short_history_without_exit_is_incomplete() -> None:
    order = simulate(
        frame([(100, 101, 99, 100), (100, 102, 99, 101), (101, 102, 100, 101)]),
        {"key": "rr-2", "label": "2R", "type": "RR", "multiplier": 2},
        max_holding_bars=5,
    )

    assert order["status"] == "INCOMPLETE"
    assert order["exit_reason"] is None


def test_max_holding_closes_at_period_end() -> None:
    order = simulate(
        frame([(100, 101, 99, 100), (100, 102, 99, 101), (101, 103, 100, 102), (102, 103, 101, 102)]),
        {"key": "rr-3", "label": "3R", "type": "RR", "multiplier": 3},
        max_holding_bars=2,
    )

    assert order["exit_reason"] == "TIME_EXIT"
    assert order["holding_bars"] == 2
    assert float(order["r_multiple"]) == pytest.approx(1)


def test_without_max_holding_does_not_force_a_time_exit() -> None:
    order = simulate(
        frame([(100, 101, 99, 100), (100, 102, 99, 101), (101, 102, 100, 102), (102, 103, 101, 102)]),
        {"key": "rr-3", "label": "3R", "type": "RR", "multiplier": 3},
        max_holding_bars=None,
    )

    assert order["status"] == "INCOMPLETE"
    assert order["exit_reason"] is None
    assert order["holding_bars"] == 3


def test_summary_excludes_incomplete_from_win_rate() -> None:
    winner = simulate(
        frame([(100, 101, 99, 100), (100, 106, 99, 105), (105, 106, 104, 105)]),
        {"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1},
    )
    incomplete = simulate(
        frame([(100, 101, 99, 100), (100, 101.9, 99, 101)]),
        {"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1},
        max_holding_bars=5,
    )

    result = _summaries("run", [{"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1}], [winner, incomplete])[0]

    assert result["wins"] == 1
    assert result["losses"] == 0
    assert result["incomplete"] == 1
    assert float(result["win_rate"]) == 1


def test_request_rejects_more_than_fifty_market_combinations() -> None:
    with pytest.raises(ValueError, match="50"):
        BacktestCreateRequest(
            symbols=[f"S{index}" for index in range(8)],
            timeframes=["1m", "3m", "5m", "15m", "30m", "1h", "1d"],
            entry_conditions=["head_shoulders_top:right_shoulder_confirmed"],
            take_profit_rules=[{"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1}],
        )


def test_backtest_request_uses_the_default_entry_score_thresholds() -> None:
    request = BacktestCreateRequest(
        symbols=["DCE.a2609"],
        entry_conditions=["head_shoulders_top:right_shoulder_confirmed"],
        take_profit_rules=[{"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1}],
    )

    assert request.min_pattern_score == 75
    assert request.min_trend_score == 65
    assert request.timeframes == ["3m", "5m"]
    assert request.kline_count == 1000


def test_entry_condition_and_score_filters_select_only_eligible_signals() -> None:
    signals = [
        {"pattern": "head_shoulders_top", "alert_type": "right_shoulder_confirmed", "pattern_score": 78, "score": 71},
        {"pattern": "head_shoulders_top", "alert_type": "head_shoulders_top_pullback", "pattern_score": 80, "score": 20},
        {"pattern": "head_shoulders_top", "alert_type": "right_shoulder_confirmed", "pattern_score": 60, "score": 90},
        {"pattern": "head_shoulders_top", "alert_type": "head_shoulders_top_pullback", "pattern_score": 60, "score": 20},
        {"pattern": "head_shoulders_top", "alert_type": "head_shoulders_top_pullback", "pattern_score": 80, "score": 40},
    ]

    result = _filter_backtest_signals(
        signals,
        ["head_shoulders_top:right_shoulder_confirmed"],
        ["head_shoulders_top:head_shoulders_top_pullback"],
        min_pattern_score=70,
        min_trend_score=70,
        other_min_pattern_score=70,
        other_max_trend_score=30,
    )

    assert result == [signals[0], signals[1]]


def test_pullback_signal_thresholds_can_be_overridden_for_backtests() -> None:
    result = {"final_score": 70}

    assert not should_emit_pullback_alert(40, result, HeadShoulderTopConfig())
    assert should_emit_pullback_alert(
        40,
        result,
        HeadShoulderTopConfig(pullback_min_pattern_score=70, pullback_max_trend_score=40),
    )


def test_symbol_group_normalizes_name_and_duplicate_symbols() -> None:
    group = BacktestSymbolGroupCreateRequest(
        name="  黑色系  ",
        symbols=[" SHFE.rb2610 ", "DCE.i2609", "SHFE.rb2610", ""],
    )

    assert group.name == "黑色系"
    assert group.symbols == ["SHFE.rb2610", "DCE.i2609"]


def test_database_timestamps_are_serialized_as_utc() -> None:
    result = with_utc_timestamps({"created_at": datetime(2026, 7, 17, 2, 31, 1)}, "created_at")
    assert result["created_at"] == "2026-07-17T02:31:01+00:00"


def test_backtest_run_timestamps_include_utc_offset() -> None:
    result = _run_dict({
        "id": "run",
        "request_json": "{}",
        "created_at": datetime(2026, 7, 17, 2, 31, 1),
        "started_at": None,
        "completed_at": None,
        "updated_at": datetime(2026, 7, 17, 2, 32, 1),
    })
    assert result["created_at"] == "2026-07-17T02:31:01+00:00"
    assert result["updated_at"] == "2026-07-17T02:32:01+00:00"


def test_default_backtest_name_uses_shanghai_time() -> None:
    assert default_backtest_name(datetime(2026, 7, 17, 2, 30)) == "策略回测 2026-07-17 10:30"
