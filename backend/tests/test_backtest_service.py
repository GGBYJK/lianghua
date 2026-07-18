from __future__ import annotations

import pandas as pd
import pytest
from datetime import datetime

from app.backtest_schemas import BacktestCreateRequest, BacktestSymbolGroupCreateRequest
from app.backtest_service import _simulate_order, _summaries
from app.backtest_store import _run_dict, default_backtest_name
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
        "right_shoulder": {"time": "2026-07-17T09:00:00"},
        "head": {"time": "2026-07-17T08:50:00"},
        "pattern_metrics": {"trigger_price": 100, "stop": 95, "target": 112},
    }


def simulate(data: pd.DataFrame, rule: dict[str, object], max_holding_bars: int = 3) -> dict[str, object]:
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
    assert float(order["r_multiple"]) == -1


def test_qtr_rule_hits_take_profit() -> None:
    order = simulate(
        frame([(100, 101, 99, 100), (100, 104.2, 99, 104), (104, 105, 103, 104)]),
        {"key": "qtr-1", "label": "1 QTR", "type": "QTR", "multiplier": 1},
    )

    assert float(order["target_price"]) == 104
    assert order["exit_reason"] == "TAKE_PROFIT"
    assert float(order["r_multiple"]) == pytest.approx(0.8)


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
    assert float(order["r_multiple"]) == pytest.approx(0.4)


def test_summary_excludes_incomplete_from_win_rate() -> None:
    winner = simulate(
        frame([(100, 101, 99, 100), (100, 106, 99, 105), (105, 106, 104, 105)]),
        {"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1},
    )
    incomplete = simulate(
        frame([(100, 101, 99, 100), (100, 102, 99, 101)]),
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
            patterns=["head_shoulders_top"],
            alert_types=["right_shoulder_confirmed"],
            take_profit_rules=[{"key": "rr-1", "label": "1R", "type": "RR", "multiplier": 1}],
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
