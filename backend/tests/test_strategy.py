from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import get_symbol_prefix, load_head_shoulder_config
from app.csv_loader import normalize_kline_dataframe
from app.main import app
from app import strategy as strategy_module
from app.market_client import (
    extract_rows,
    normalize_period,
    normalize_aliyun_period,
    is_listed_futures_contract,
    MarketApiError,
    normalize_symbol_row,
    normalize_tqsdk_period,
    normalize_tqsdk_symbol,
    listed_contracts_to_varieties,
    query_main_and_sub_contracts_from_api,
    query_tqsdk_contracts_from_api,
    normalize_tushare_symbol,
    normalize_tushare_symbol_row,
    _fetch_kline_from_aliyun_market_sync,
    tqsdk_dataframe_to_kline,
    aggregate_exchange_hourly_bars,
    aggregate_exchange_daily_bars,
    tqsdk_symbol_hints,
    TqSdkMarketService,
    tushare_dataframe_to_kline,
    rows_to_dataframe,
    aliyun_symbol_hints,
)
from app.watch_pool_store import _contract_name_from_symbol
from app.strategy import (
    HeadShoulderTopConfig,
    HeadShoulderTopSignal,
    PivotPoint,
    calculate_neckline_price,
    calculate_qtr,
    calculate_pattern_score,
    calculate_true_range,
    check_neckline_break_then_pullback,
    check_right_shoulder_midpoint_trigger,
    deduplicate_overlapping_signals,
    find_pivots,
    iter_pattern_candidates,
    validate_candle_close_constraints,
    validate_head_shoulders_structure,
    passes_head_neck_bar_limit,
    passes_one_minute_head_neck_bar_limit,
    scan_head_shoulders,
    scan_head_shoulders_top,
    scan_inverse_head_shoulders,
    validate_inverse_head_shoulders_structure,
    calculate_ma_trend_score,
    calculate_combined_trend_score,
    trend_label_from_score,
    candle_display_time,
)
from app.monitor import build_signal_unique_key
from app.monitor import build_wechat_workbot_content


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample_data" / "head_shoulders_sample.csv"


def _pattern_test_df(
    close: list[float],
    *,
    volume: list[float] | None = None,
    macd_hist: list[float] | None = None,
) -> pd.DataFrame:
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-06-14 09:00:00", periods=len(close), freq="min"),
        "open": close,
        "high": [price + 0.5 for price in close],
        "low": [price - 0.5 for price in close],
        "close": close,
        "volume": volume if volume is not None else [100.0] * len(close),
    })
    if macd_hist is not None:
        df["macd_hist"] = macd_hist
        df["macd_dif"] = macd_hist
    return df


def test_symbol_prefix() -> None:
    assert get_symbol_prefix("rb2405") == "rb"
    assert get_symbol_prefix("SA405") == "sa"


def test_true_range_uses_previous_close() -> None:
    df = pd.DataFrame({
        "high": [100.0, 108.0, 104.0],
        "low": [95.0, 102.0, 96.0],
        "close": [97.0, 103.0, 100.0],
    })

    assert calculate_true_range(df, 0) == 5.0
    assert calculate_true_range(df, 1) == 11.0
    assert calculate_true_range(df, 2) == 8.0


def test_qtr_averages_true_range_including_both_necks() -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=5, freq="min")
    df = pd.DataFrame({
        "high": [100.0, 108.0, 104.0, 110.0, 106.0],
        "low": [95.0, 102.0, 96.0, 101.0, 100.0],
        "close": [97.0, 103.0, 100.0, 105.0, 102.0],
    })

    qtr = calculate_qtr(
        df,
        PivotPoint(1, times[1], 102.0, "low"),
        PivotPoint(3, times[3], 101.0, "low"),
    )

    assert qtr == (11.0 + 8.0 + 10.0) / 3


def test_three_minute_config_accepts_wide_right_neck_top_setup() -> None:
    config = load_head_shoulder_config("SA609", "3m")
    times = pd.to_datetime([
        "2026-06-03 21:06:00",
        "2026-06-03 21:15:00",
        "2026-06-03 21:39:00",
        "2026-06-04 09:18:00",
        "2026-06-04 09:27:00",
    ])
    points = [
        PivotPoint(0, times[0], 1206, "high"),
        PivotPoint(3, times[1], 1197, "low"),
        PivotPoint(11, times[2], 1210, "high"),
        PivotPoint(44, times[3], 1196, "low"),
        PivotPoint(47, times[4], 1201, "high"),
    ]

    ok, _, _ = validate_head_shoulders_structure(points, config)

    assert ok


def test_csv_aliases() -> None:
    raw = pd.DataFrame({
        "trade_time": ["2026-04-24 09:00:00"],
        "o": [10],
        "h": [11],
        "l": [9],
        "c": [10.5],
        "vol": [100],
    })
    df = normalize_kline_dataframe(raw)
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]


def test_config_merge_override() -> None:
    config = load_head_shoulder_config("rb2405", "5m", {"min_score_to_alert": 65})
    assert config.min_shoulder_to_head_height_ratio == 0.3
    assert config.min_score_to_alert == 65
    assert config.min_pattern_score_to_alert == 60


def test_five_minute_config_also_enables_strict_shape_rules() -> None:
    config_1m = load_head_shoulder_config("rb2405", "1m")
    config_3m = load_head_shoulder_config("rb2405", "3m")
    config_5m = load_head_shoulder_config("rb2405", "5m")
    config_15m = load_head_shoulder_config("rb2405", "15m")

    assert config_1m.require_head_beyond_shoulders_and_necks is True
    assert config_1m.require_shoulders_between_opposite_neck_and_head is True
    assert config_1m.min_shoulder_to_neck_height == 4
    assert config_3m.require_head_beyond_shoulders_and_necks is True
    assert config_3m.require_shoulders_between_opposite_neck_and_head is True
    assert config_3m.min_shoulder_to_neck_height == 0
    assert config_3m.max_shoulder_diff_pct == 0.005
    assert config_5m.require_head_beyond_shoulders_and_necks is True
    assert config_5m.require_shoulders_between_opposite_neck_and_head is True
    assert config_5m.min_shoulder_to_neck_height == 0
    assert config_5m.max_shoulder_diff_pct == 0.005
    assert config_15m.require_head_beyond_shoulders_and_necks is False
    assert config_15m.require_shoulders_between_opposite_neck_and_head is False


def test_infoway_kline_payload_normalizes_to_dataframe() -> None:
    payload = [
        {
            "s": "XAGUSD",
            "respList": [
                {"t": "1751270340", "o": "18.01", "h": "18.20", "l": "17.95", "c": "18.10", "v": "18000"},
                {"t": "1751270280", "o": "18.00", "h": "18.05", "l": "17.90", "c": "18.01", "v": "17000"},
            ],
        }
    ]
    rows = extract_rows(payload, symbol="XAGUSD")
    df = rows_to_dataframe(rows)
    assert len(df) == 2
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 18.10


def test_infoway_period_mapping() -> None:
    assert normalize_period("1m") == 1
    assert normalize_period("3m") == "3m"
    assert normalize_period("5m") == 2
    assert normalize_period("1h") == 5
    assert normalize_period("1d") == 8


def test_aliyun_period_mapping_and_symbol_hints() -> None:
    assert normalize_aliyun_period("3m") == "3"
    assert normalize_aliyun_period("1h") == "60"
    assert normalize_aliyun_period("1d") == "day"
    hints = aliyun_symbol_hints("c0")
    assert hints == [{"symbol": "c0", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn continuous"}]


def test_tqsdk_period_mapping_symbol_hints_and_dataframe() -> None:
    assert normalize_tqsdk_period("3m") == 180
    assert normalize_tqsdk_period("1h") == 3600
    assert normalize_tqsdk_period("1d") == 86400
    assert normalize_tqsdk_symbol("c0") == "KQ.m@DCE.c"
    assert normalize_tqsdk_symbol("KQ.m@DCE.c") == "KQ.m@DCE.c"
    assert tqsdk_symbol_hints("c0") == [
        {"symbol": "c0", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn continuous"}
    ]
    df = pd.DataFrame({
        "datetime": [1777347600000000000, 1777347660000000000],
        "open": [2000, 2001],
        "high": [2002, 2003],
        "low": [1999, 2000],
        "close": [2001, 2002],
        "volume": [100, 120],
    })
    normalized = tqsdk_dataframe_to_kline(df)
    assert list(normalized.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert normalized["close"].iloc[-1] == 2002
    assert normalized["datetime"].iloc[0].isoformat() == "2026-04-28T11:40:00"


def test_exchange_hourly_and_daily_display_time_uses_session_close() -> None:
    assert candle_display_time(pd.Timestamp("2026-06-26 09:00"), "1h").isoformat() == "2026-06-26T10:00:00"
    assert candle_display_time(pd.Timestamp("2026-06-26 10:00"), "1h").isoformat() == "2026-06-26T11:15:00"
    assert candle_display_time(pd.Timestamp("2026-06-26 11:15"), "1h").isoformat() == "2026-06-26T14:15:00"
    assert candle_display_time(pd.Timestamp("2026-06-26 14:15"), "1h").isoformat() == "2026-06-26T15:00:00"
    assert candle_display_time(pd.Timestamp("2026-06-25 21:00"), "1h").isoformat() == "2026-06-25T22:00:00"
    assert candle_display_time(pd.Timestamp("2026-06-25 22:00"), "1h").isoformat() == "2026-06-25T23:00:00"
    assert candle_display_time(pd.Timestamp("2026-06-26"), "1d").isoformat() == "2026-06-26T00:00:00"


def test_tqsdk_utc_timestamp_displays_as_beijing_time_in_wechat_message() -> None:
    df = pd.DataFrame({
        "datetime": [1777347600000000000],
        "open": [2000],
        "high": [2002],
        "low": [1999],
        "close": [2001],
        "volume": [100],
    })
    normalized = tqsdk_dataframe_to_kline(df)
    signal = {
        "symbol": "c0",
        "timeframe": "1m",
        "pattern": "head_shoulders_top",
        "alert_type": "right_shoulder_confirmed",
        "score": 80,
        "pattern_score": 77,
        "pattern_metrics": {"stop": 2010.25, "target": 1988.75},
        "right_shoulder": {"time": normalized["datetime"].iloc[0].isoformat(), "price": 3329},
    }

    content = build_wechat_workbot_content(signal, {"name": "c0"})

    assert content == (
        "头肩顶：c0  1m\n"
        "时间：20260428   11:40\n"
        "评分：77+80   强空头趋势\n"
        "止损价：2010.25\n"
        "目标价：1988.75"
    )


def test_tqsdk_contract_query_filters_target_exchanges() -> None:
    class FakeApi:
        def query_quotes(self, expired: bool | None = None) -> list[str]:
            assert expired is False
            return [
                "SHFE.rb2610",
                "DCE.c2607",
                "DCE.12702-P-800",
                "DCE.$c2607",
                "CZCE.IPS SF609&SM609",
                "CZCE.RM409-C-3000",
                "SHFE.Cu2401C50000",
                "CZCE.SR609",
                "CFFEX.IF2606",
                "KQ.m@DCE.c",
            ]

    assert query_tqsdk_contracts_from_api(FakeApi(), ["SHFE", "DCE", "CZCE"]) == [
        "CZCE.SR609",
        "DCE.c2607",
        "SHFE.rb2610",
    ]
    assert _contract_name_from_symbol("DCE.c2607") == "c2607"


def test_exchange_hourly_bars_use_futures_session_boundaries() -> None:
    times = [
        "2026-06-02 21:00",
        "2026-06-02 21:55",
        "2026-06-02 22:00",
        "2026-06-02 22:55",
        "2026-06-02 23:05",
        "2026-06-03 09:00",
        "2026-06-03 09:55",
        "2026-06-03 10:00",
        "2026-06-03 10:20",
        "2026-06-03 10:55",
        "2026-06-03 11:10",
        "2026-06-03 11:15",
        "2026-06-03 11:45",
        "2026-06-03 13:55",
        "2026-06-03 14:10",
        "2026-06-03 14:15",
        "2026-06-03 14:55",
    ]
    close = list(range(101, 101 + len(times)))
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(times),
            "open": close,
            "high": [value + 10 for value in close],
            "low": [value - 10 for value in close],
            "close": close,
            "volume": [1] * len(times),
        }
    )

    hourly = aggregate_exchange_hourly_bars(df)

    assert [item.isoformat() for item in hourly["datetime"]] == [
        "2026-06-02T21:00:00",
        "2026-06-02T22:00:00",
        "2026-06-03T09:00:00",
        "2026-06-03T10:00:00",
        "2026-06-03T11:15:00",
        "2026-06-03T14:15:00",
    ]
    assert list(hourly["open"]) == [101, 103, 106, 108, 112, 116]
    assert list(hourly["close"]) == [102, 104, 107, 111, 115, 117]
    assert list(hourly["volume"]) == [2, 2, 2, 3, 3, 2]


def test_exchange_daily_bars_treat_night_session_as_next_trading_day() -> None:
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2026-06-02 21:00",
                    "2026-06-02 22:55",
                    "2026-06-02 23:05",
                    "2026-06-03 09:00",
                    "2026-06-03 10:20",
                    "2026-06-03 11:45",
                    "2026-06-03 14:55",
                ]
            ),
            "open": [10, 11, 99, 12, 98, 97, 13],
            "high": [12, 13, 199, 14, 198, 197, 15],
            "low": [9, 10, 0, 11, 1, 2, 12],
            "close": [11, 12, 99, 13, 98, 97, 14],
            "volume": [1, 2, 100, 3, 100, 100, 4],
        }
    )

    daily = aggregate_exchange_daily_bars(df)

    assert len(daily) == 1
    assert daily["datetime"].iloc[0].isoformat() == "2026-06-03T00:00:00"
    assert daily["open"].iloc[0] == 10
    assert daily["close"].iloc[0] == 14
    assert daily["volume"].iloc[0] == 10


def test_exchange_daily_bars_use_previous_night_for_trade_date() -> None:
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2026-06-25 20:55",
                    "2026-06-25 21:00",
                    "2026-06-25 22:55",
                    "2026-06-25 23:05",
                    "2026-06-26 09:00",
                    "2026-06-26 10:20",
                    "2026-06-26 10:30",
                    "2026-06-26 11:25",
                    "2026-06-26 11:45",
                    "2026-06-26 13:30",
                    "2026-06-26 14:55",
                    "2026-06-26 15:00",
                ]
            ),
            "open": [99, 10, 11, 99, 12, 99, 13, 14, 99, 15, 16, 99],
            "high": [199, 12, 13, 199, 14, 199, 15, 16, 199, 17, 18, 199],
            "low": [0, 9, 10, 0, 11, 0, 12, 13, 0, 14, 15, 0],
            "close": [99, 11, 12, 99, 13, 99, 14, 15, 99, 16, 17, 99],
            "volume": [100, 1, 2, 100, 3, 100, 4, 5, 100, 6, 7, 100],
        }
    )

    daily = aggregate_exchange_daily_bars(df)

    assert len(daily) == 1
    assert daily["datetime"].iloc[0].isoformat() == "2026-06-26T00:00:00"
    assert daily["open"].iloc[0] == 10
    assert daily["high"].iloc[0] == 18
    assert daily["low"].iloc[0] == 9
    assert daily["close"].iloc[0] == 17
    assert daily["volume"].iloc[0] == 28


def test_aliyun_one_hour_uses_exchange_session_aggregation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_fetch(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
        calls.append((symbol, period, limit))
        return pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2026-06-25 21:00",
                        "2026-06-25 21:55",
                        "2026-06-25 22:00",
                        "2026-06-25 22:55",
                        "2026-06-26 09:00",
                        "2026-06-26 09:55",
                    ]
                ),
                "open": [10, 11, 12, 13, 14, 15],
                "high": [11, 12, 13, 14, 15, 16],
                "low": [9, 10, 11, 12, 13, 14],
                "close": [11, 12, 13, 14, 15, 16],
                "volume": [1, 2, 3, 4, 5, 6],
            }
        )

    monkeypatch.setattr("app.market_client._fetch_kline_from_aliyun_market_sync", fake_fetch)

    hourly = _fetch_kline_from_aliyun_market_sync("DCE.c2609", "1h", 3)

    assert calls == [("DCE.c2609", "5m", 420)]
    assert [item.isoformat() for item in hourly["datetime"]] == [
        "2026-06-25T21:00:00",
        "2026-06-25T22:00:00",
        "2026-06-26T09:00:00",
    ]
    assert list(hourly["volume"]) == [3, 7, 11]


def test_aliyun_daily_uses_exchange_trading_day_aggregation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_fetch(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
        calls.append((symbol, period, limit))
        return pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2026-06-25 21:00",
                        "2026-06-25 22:55",
                        "2026-06-25 23:05",
                        "2026-06-26 09:00",
                        "2026-06-26 14:55",
                    ]
                ),
                "open": [10, 11, 99, 12, 13],
                "high": [12, 13, 199, 14, 15],
                "low": [9, 10, 0, 11, 12],
                "close": [11, 12, 99, 13, 14],
                "volume": [1, 2, 100, 3, 4],
            }
        )

    monkeypatch.setattr("app.market_client._fetch_kline_from_aliyun_market_sync", fake_fetch)

    daily = _fetch_kline_from_aliyun_market_sync("DCE.c2609", "1d", 1)

    assert calls == [("DCE.c2609", "5m", 9000)]
    assert len(daily) == 1
    assert daily["datetime"].iloc[0].isoformat() == "2026-06-26T00:00:00"
    assert daily["open"].iloc[0] == 10
    assert daily["high"].iloc[0] == 15
    assert daily["low"].iloc[0] == 9
    assert daily["close"].iloc[0] == 14
    assert daily["volume"].iloc[0] == 10


def test_exchange_daily_bars_roll_friday_night_session_to_monday() -> None:
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2026-05-29 21:00",
                    "2026-05-29 22:55",
                    "2026-06-01 09:00",
                    "2026-06-01 14:55",
                ]
            ),
            "open": [10, 11, 12, 13],
            "high": [12, 13, 14, 15],
            "low": [9, 10, 11, 12],
            "close": [11, 12, 13, 14],
            "volume": [1, 2, 3, 4],
        }
    )

    daily = aggregate_exchange_daily_bars(df)

    assert len(daily) == 1
    assert daily["datetime"].iloc[0].isoformat() == "2026-06-01T00:00:00"
    assert daily["open"].iloc[0] == 10
    assert daily["close"].iloc[0] == 14


def test_tqsdk_main_and_sub_contract_query_uses_open_interest() -> None:
    class FakeQuote:
        def __init__(self, open_interest: float) -> None:
            self.open_interest = open_interest

    class FakeApi:
        def query_cont_quotes(self, exchange_id: str) -> list[str]:
            mapping = {
                "SHFE": ["SHFE.sp2609", "SHFE.Cu2401C50000"],
                "DCE": ["DCE.m2609", "DCE.12702-P-800"],
                "CZCE": ["CZCE.SR609", "CZCE.RM409-C-3000"],
            }
            return mapping[exchange_id]

        def query_quotes(self, ins_class: str, exchange_id: str, product_id: str, expired: bool) -> list[str]:
            assert ins_class == "FUTURE"
            assert expired is False
            mapping = {
                ("SHFE", "sp"): ["SHFE.sp2609", "SHFE.sp2607", "SHFE.sp2611"],
                ("DCE", "m"): ["DCE.m2609", "DCE.m2607"],
                ("CZCE", "SR"): ["CZCE.SR609", "CZCE.SR701"],
            }
            return mapping.get((exchange_id, product_id), [])

        def get_quote_list(self, contracts: list[str]) -> list[FakeQuote]:
            open_interest = {
                "SHFE.sp2609": 300,
                "SHFE.sp2607": 200,
                "SHFE.sp2611": 100,
                "DCE.m2609": 500,
                "DCE.m2607": 350,
                "CZCE.SR609": 700,
                "CZCE.SR701": 450,
            }
            return [FakeQuote(open_interest[contract]) for contract in contracts]

        def wait_update(self, deadline: float | None = None) -> bool:
            return True

    assert listed_contracts_to_varieties(["SHFE.au2606", "DCE.m2607", "CZCE.SR609"], ["SHFE", "DCE"]) == [
        "DCE.m",
        "SHFE.au",
    ]
    assert query_main_and_sub_contracts_from_api(FakeApi(), ["SHFE", "DCE", "CZCE"]) == [
        "CZCE.SR609",
        "CZCE.SR701",
        "DCE.m2607",
        "DCE.m2609",
        "SHFE.sp2607",
        "SHFE.sp2609",
    ]


def test_listed_futures_contract_filter_excludes_options() -> None:
    assert is_listed_futures_contract("DCE.c2607") is True
    assert is_listed_futures_contract("DCE.12702-P-800") is False
    assert is_listed_futures_contract("DCE.$c2607") is False
    assert is_listed_futures_contract("CZCE.IPS SF609&SM609") is False
    assert is_listed_futures_contract("CZCE.RM409-C-3000") is False
    assert is_listed_futures_contract("SHFE.Cu2401C50000") is False


def test_tqsdk_subscription_rejects_unlisted_contract() -> None:
    class FakeApi:
        def query_quotes(self, expired: bool | None = None) -> list[str]:
            assert expired is False
            return ["CZCE.CY609"]

    service = TqSdkMarketService()
    try:
        service._ensure_contract_listed(FakeApi(), "CZCE.CY608")
    except MarketApiError as exc:
        assert "未找到上市合约" in str(exc)
    else:
        raise AssertionError("expected unlisted contract to be rejected")


def test_infoway_symbol_row_normalizes() -> None:
    row = {
        "symbol": "XAGUSD",
        "name_cn": "白银/美元",
        "name_hk": "白銀/美元",
        "name_en": "Silver Spot",
    }
    assert normalize_symbol_row(row) == {
        "symbol": "XAGUSD",
        "name_cn": "白银/美元",
        "name_hk": "白銀/美元",
        "name_en": "Silver Spot",
    }


def test_tushare_symbol_helpers() -> None:
    assert normalize_tushare_symbol("C") == "C.DCE"
    assert normalize_tushare_symbol("C.DCE") == "C.DCE"
    assert normalize_tushare_symbol_row({"ts_code": "C2505.DCE", "symbol": "C2505", "name": "玉米2505"}) == {
        "symbol": "C2505.DCE",
        "name_cn": "玉米2505",
        "name_hk": None,
        "name_en": "C2505",
    }


def test_tushare_dataframe_to_kline() -> None:
    df = pd.DataFrame({
        "trade_date": ["20260428", "20260429"],
        "open": [2000, 2010],
        "high": [2020, 2030],
        "low": [1990, 2005],
        "close": [2015, 2025],
        "vol": [10000, 12000],
    })
    normalized = tushare_dataframe_to_kline(df)
    assert list(normalized.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert normalized["close"].iloc[-1] == 2025


def test_sample_scan_finds_confirmed_signal() -> None:
    df = pd.read_csv(SAMPLE)
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders_top(df, "rb2405", "5m", config)
    signal = next(signal for signal in signals if signal.alert_type == "right_shoulder_confirmed")
    midpoint_price = (signal.right_neck.price + signal.right_shoulder.price) / 2
    assert signal.qtr is not None and signal.qtr > 0
    assert signal.retest_time is not None
    assert signal.retest_time > signal.right_shoulder.time
    assert signal.retest_price is not None and signal.retest_price <= midpoint_price


def test_monitor_unique_key_uses_stable_pattern_structure() -> None:
    signal = {
        "symbol": "c0",
        "timeframe": "1m",
        "pattern": "head_shoulders_top",
        "alert_type": "neckline_break",
        "left_shoulder": {"time": "2026-05-12T09:00:00"},
        "left_neck": {"time": "2026-05-12T09:05:00"},
        "head": {"time": "2026-05-12T09:10:00"},
        "right_neck": {"time": "2026-05-12T09:15:00"},
        "right_shoulder": {"time": "2026-05-12T09:20:00"},
        "break_time": "2026-05-12T09:25:00",
        "retest_time": None,
        "score": 82,
        "trend_label": "空头趋势",
    }
    assert build_signal_unique_key(signal) == (
        "c0|1m|head_shoulders_top|neckline_break|2026-05-12T09:10:00"
    )


def test_monitor_unique_key_uses_head_position_for_repeat_alerts() -> None:
    signal = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "left_shoulder": {"time": "2026-06-02T21:15:00"},
        "left_neck": {"time": "2026-06-02T21:24:00"},
        "head": {"time": "2026-06-02T21:33:00"},
        "right_neck": {"time": "2026-06-02T21:45:00"},
        "right_shoulder": {"time": "2026-06-02T21:52:35"},
        "break_time": None,
        "retest_time": None,
        "score": 78,
        "trend_label": "多头趋势",
    }
    repeated = {
        **signal,
        "left_shoulder": {"time": "2026-06-02T21:18:00"},
        "left_neck": {"time": "2026-06-02T21:27:00"},
        "right_neck": {"time": "2026-06-02T21:48:00"},
        "right_shoulder": {"time": "2026-06-02T22:04:53"},
    }
    changed_score = {**repeated, "score": 79}

    assert build_signal_unique_key(signal) == build_signal_unique_key(repeated)
    assert build_signal_unique_key(signal) == build_signal_unique_key(changed_score)


def test_top_scan_emits_right_shoulder_alert_type_only() -> None:
    df = pd.read_csv(SAMPLE)
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders_top(df, "rb2405", "5m", config)
    alert_types = {signal.alert_type for signal in signals}
    assert "right_shoulder_confirmed" in alert_types
    assert "neckline_break" not in alert_types


def test_top_midpoint_trigger_waits_for_price_to_reach_halfway() -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=4, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [101.0, 95.01, 95.0, 94.5],
        "high": [102.0] * len(times),
        "close": [101.0, 95.01, 95.0, 94.5],
    })
    right_neck = PivotPoint(0, times[0], 90.0, "low")
    right_shoulder = PivotPoint(1, times[1], 100.0, "high")
    config = HeadShoulderTopConfig()

    before = check_right_shoulder_midpoint_trigger(
        df.iloc[:2].reset_index(drop=True),
        right_neck,
        right_shoulder,
        config,
        inverse=False,
    )
    triggered = check_right_shoulder_midpoint_trigger(
        df,
        right_neck,
        right_shoulder,
        config,
        inverse=False,
    )

    assert not before[0]
    assert triggered == (True, 2, times[2], 95.0, 95.0)


def test_inverse_midpoint_trigger_waits_for_price_to_reach_halfway() -> None:
    times = pd.date_range("2026-06-14 10:00:00", periods=4, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [98.0] * len(times),
        "high": [99.0, 104.99, 105.0, 105.5],
        "close": [99.0, 104.99, 105.0, 105.5],
    })
    right_neck = PivotPoint(0, times[0], 110.0, "high")
    right_shoulder = PivotPoint(1, times[1], 100.0, "low")
    config = HeadShoulderTopConfig()

    before = check_right_shoulder_midpoint_trigger(
        df.iloc[:2].reset_index(drop=True),
        right_neck,
        right_shoulder,
        config,
        inverse=True,
    )
    triggered = check_right_shoulder_midpoint_trigger(
        df,
        right_neck,
        right_shoulder,
        config,
        inverse=True,
    )

    assert not before[0]
    assert triggered == (True, 2, times[2], 105.0, 105.0)


def test_inverse_pullback_requires_break_then_fall_to_quarter_level() -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=6, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [100.0, 100.0, 100.0, 100.0, 107.0, 102.5],
        "high": [101.0, 101.0, 101.0, 101.0, 111.0, 106.0],
        "close": [100.0, 100.0, 100.0, 100.0, 110.0, 104.5],
    })
    left_neck = PivotPoint(0, times[0], 110.0, "high")
    right_neck = PivotPoint(2, times[2], 110.0, "high")
    right_shoulder = PivotPoint(3, times[3], 100.0, "low")

    result = check_neckline_break_then_pullback(
        df,
        left_neck,
        right_neck,
        right_shoulder,
        inverse=True,
    )

    assert result[0]
    assert result[1] == 4
    assert result[4] == 5
    assert result[7] == 102.5


def test_top_pullback_requires_break_then_rise_to_quarter_level() -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=6, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [100.0, 100.0, 100.0, 100.0, 89.0, 94.0],
        "high": [101.0, 101.0, 101.0, 101.0, 92.0, 97.5],
        "close": [100.0, 100.0, 100.0, 100.0, 90.0, 95.0],
    })
    left_neck = PivotPoint(0, times[0], 90.0, "low")
    right_neck = PivotPoint(2, times[2], 90.0, "low")
    right_shoulder = PivotPoint(3, times[3], 100.0, "high")

    result = check_neckline_break_then_pullback(
        df,
        left_neck,
        right_neck,
        right_shoulder,
        inverse=False,
    )

    assert result[0]
    assert result[1] == 4
    assert result[4] == 5
    assert result[7] == 97.5


def test_inverse_pullback_requires_break_above_both_necks() -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=6, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [100.0, 100.0, 100.0, 100.0, 108.0, 102.5],
        "high": [101.0, 101.0, 101.0, 101.0, 112.0, 106.0],
        "close": [100.0, 100.0, 100.0, 100.0, 111.0, 104.5],
    })
    left_neck = PivotPoint(0, times[0], 110.0, "high")
    right_neck = PivotPoint(2, times[2], 115.0, "high")
    right_shoulder = PivotPoint(3, times[3], 100.0, "low")

    result = check_neckline_break_then_pullback(
        df,
        left_neck,
        right_neck,
        right_shoulder,
        inverse=True,
    )

    assert not result[0]


def test_top_pullback_requires_break_below_both_necks() -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=6, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [100.0, 100.0, 100.0, 100.0, 88.0, 94.0],
        "high": [101.0, 101.0, 101.0, 101.0, 92.0, 97.5],
        "close": [100.0, 100.0, 100.0, 100.0, 90.0, 95.0],
    })
    left_neck = PivotPoint(0, times[0], 90.0, "low")
    right_neck = PivotPoint(2, times[2], 85.0, "low")
    right_shoulder = PivotPoint(3, times[3], 100.0, "high")

    result = check_neckline_break_then_pullback(
        df,
        left_neck,
        right_neck,
        right_shoulder,
        inverse=False,
    )

    assert not result[0]


def test_pattern_score_scores_top_structure_thresholds_and_grade() -> None:
    close = [90, 92, 94, 96, 100, 98, 95, 102, 111, 104, 96, 99, 104, 100, 96]
    df = _pattern_test_df(
        close,
        volume=[120, 120, 120, 120, 120, 110, 100, 90, 80, 70, 65, 60, 55, 50, 45],
        macd_hist=[1.0, 1.1, 1.2, 1.3, 2.0, 1.7, 1.5, 1.2, 1.0, 0.6, 0.3, 0.2, 0.1, -0.1, -0.2],
    )
    times = pd.to_datetime(df["datetime"])
    result = calculate_pattern_score(
        df,
        left_shoulder=PivotPoint(4, times[4], 100.0, "high"),
        left_neck=PivotPoint(6, times[6], 95.0, "low"),
        head=PivotPoint(8, times[8], 111.0, "high"),
        right_neck=PivotPoint(10, times[10], 96.0, "low"),
        right_shoulder=PivotPoint(12, times[12], 104.0, "high"),
        inverse=False,
        qtr=4.0,
        trigger_index=14,
        trigger_price=96.0,
        midpoint=100.0,
    )

    structure = next(section for section in result["sections"] if section["key"] == "structure")
    neckline = next(section for section in result["sections"] if section["key"] == "neckline")
    time_section = next(section for section in result["sections"] if section["key"] == "time")
    trade = next(section for section in result["sections"] if section["key"] == "trade_value")
    structure_scores = {item["label"]: item["score"] for item in structure["items"]}
    neckline_scores = {item["label"]: item["score"] for item in neckline["items"]}
    assert sum(section["max"] for section in result["sections"]) == 100
    assert structure["max"] == 32
    assert neckline["max"] == 16
    assert time_section["max"] == 14
    assert trade["max"] == 14
    rr_detail = trade["items"][0]["detail"]
    assert "触发价=" in rr_detail
    assert "止损价=" in rr_detail
    assert "目标价=" in rr_detail
    assert structure_scores["头部突出度"] == 4
    assert structure_scores["左右肩高度接近"] == 8
    assert structure_scores["左肩有效高度"] == 2
    assert structure_scores["右肩有效高度"] == 4
    assert structure_scores["肩颈价差对称"] == 1
    assert structure_scores["头颈价差对称"] == 4
    assert neckline_scores["左右颈价格接近"] == 10
    labels = {item["label"] for section in result["sections"] for item in section["items"]}
    assert "五点顺序清晰" not in labels
    assert "右肩未破坏头部" not in labels
    assert "颈线斜率合理" not in labels
    assert "右肩已被确认" not in labels
    assert "触发前未失效" not in labels
    assert "收盘价触及半程" not in labels
    assert "右肩动能弱于头部" not in labels
    trigger = next(section for section in result["sections"] if section["key"] == "trigger")
    trigger_scores = {item["label"]: item["score"] for item in trigger["items"]}
    assert trigger["max"] == 3
    assert trigger_scores["触发速度"] == 3
    assert result["metrics"]["ds_qtr"] == pytest.approx(1.0)
    assert result["metrics"]["dn_qtr"] == pytest.approx(0.25)
    assert result["raw_score"] >= result["final_score"]
    assert result["grade"] in {"A", "B", "C", "D", "忽略"}


def test_pattern_score_applies_lowest_cap_and_rating_threshold() -> None:
    close = [80, 83, 86, 89, 100, 98, 95, 104, 116, 108, 96, 102, 110, 105, 100]
    df = _pattern_test_df(close, macd_hist=[2, 2, 2, 2, 4, 3, 2, 1, 0, -1, -2, -2, -2, -3, -4])
    times = pd.to_datetime(df["datetime"])
    result = calculate_pattern_score(
        df,
        left_shoulder=PivotPoint(4, times[4], 100.0, "high"),
        left_neck=PivotPoint(6, times[6], 95.0, "low"),
        head=PivotPoint(8, times[8], 116.0, "high"),
        right_neck=PivotPoint(10, times[10], 105.0, "low"),
        right_shoulder=PivotPoint(12, times[12], 110.0, "high"),
        inverse=False,
        qtr=2.0,
        trigger_index=14,
        trigger_price=100.0,
        midpoint=107.5,
    )

    assert 75 in result["caps"]
    assert result["final_score"] <= 75
    assert result["grade"] == ("B" if result["final_score"] >= 70 else "C" if result["final_score"] >= 55 else "D" if result["final_score"] >= 40 else "忽略")


def test_pattern_score_handles_qtr_anomaly_and_bad_rr() -> None:
    close = [100, 101, 102, 103, 104, 100, 96, 106, 110, 104, 96, 102, 105, 104, 103]
    df = _pattern_test_df(close)
    times = pd.to_datetime(df["datetime"])
    result = calculate_pattern_score(
        df,
        left_shoulder=PivotPoint(4, times[4], 104.0, "high"),
        left_neck=PivotPoint(6, times[6], 96.0, "low"),
        head=PivotPoint(8, times[8], 110.0, "high"),
        right_neck=PivotPoint(10, times[10], 96.0, "low"),
        right_shoulder=PivotPoint(12, times[12], 105.0, "high"),
        inverse=False,
        qtr=0.0,
        trigger_index=14,
        trigger_price=103.0,
        midpoint=100.5,
    )

    structure = next(section for section in result["sections"] if section["key"] == "structure")
    trade = next(section for section in result["sections"] if section["key"] == "trade_value")
    assert result["metrics"]["qtr_anomaly"] is True
    structure_scores = {item["label"]: item["score"] for item in structure["items"]}
    assert structure_scores["头部突出度"] == 0
    assert structure_scores["左右肩高度接近"] == 0
    assert structure_scores["左肩有效高度"] > 0
    assert structure_scores["右肩有效高度"] > 0
    assert trade["items"][0]["score"] == 0
    assert result["metrics"]["rr"] == 0


def test_pattern_score_inverse_uses_mirrored_direction_and_volume_proxy() -> None:
    close = [120, 118, 116, 113, 110, 114, 118, 108, 100, 107, 118, 114, 108, 112, 114]
    df = _pattern_test_df(
        close,
        volume=[0.0] * len(close),
        macd_hist=[-1.0, -1.1, -1.2, -1.3, -2.0, -1.8, -1.4, -1.0, -0.5, 0.0, 0.3, 0.4, 0.6, 0.8, 1.0],
    )
    times = pd.to_datetime(df["datetime"])
    result = calculate_pattern_score(
        df,
        left_shoulder=PivotPoint(4, times[4], 110.0, "low"),
        left_neck=PivotPoint(6, times[6], 118.0, "high"),
        head=PivotPoint(8, times[8], 100.0, "low"),
        right_neck=PivotPoint(10, times[10], 118.0, "high"),
        right_shoulder=PivotPoint(12, times[12], 108.0, "low"),
        inverse=True,
        qtr=4.0,
        trigger_index=14,
        trigger_price=114.0,
        midpoint=113.0,
    )

    structure = next(section for section in result["sections"] if section["key"] == "structure")
    momentum = next(section for section in result["sections"] if section["key"] == "momentum")
    structure_scores = {item["label"]: item["score"] for item in structure["items"]}
    assert structure_scores["头部突出度"] == 4
    assert result["metrics"]["rr"] > 0
    assert "波动率代理" in momentum["items"][1]["detail"]


def test_top_pullback_metrics_use_right_neck_and_head_qtr_formula() -> None:
    times = pd.date_range("2026-06-22 09:00:00", periods=3, freq="min")
    metrics = strategy_module._pullback_trade_metrics(
        {"trigger_price": 90.0, "stop": 99.0, "target": 80.0},
        head=PivotPoint(1, times[1], 110.0, "high"),
        right_neck=PivotPoint(2, times[2], 94.0, "low"),
        inverse=False,
        qtr=4.0,
        entry_price=100.0,
    )

    assert metrics["trigger_price"] == 100.0
    assert metrics["stop"] == pytest.approx(92.0)
    assert metrics["target"] == pytest.approx(114.0)
    assert metrics["risk"] == pytest.approx(8.0)
    assert metrics["reward"] == pytest.approx(14.0)
    assert metrics["rr"] == pytest.approx(14.0 / 8.0)


def test_inverse_pullback_metrics_use_right_neck_and_head_qtr_formula() -> None:
    times = pd.date_range("2026-06-22 09:00:00", periods=3, freq="min")
    metrics = strategy_module._pullback_trade_metrics(
        {"trigger_price": 120.0, "stop": 101.0, "target": 130.0},
        head=PivotPoint(1, times[1], 90.0, "low"),
        right_neck=PivotPoint(2, times[2], 106.0, "high"),
        inverse=True,
        qtr=4.0,
        entry_price=100.0,
    )

    assert metrics["trigger_price"] == 100.0
    assert metrics["stop"] == pytest.approx(108.0)
    assert metrics["target"] == pytest.approx(86.0)
    assert metrics["risk"] == pytest.approx(8.0)
    assert metrics["reward"] == pytest.approx(14.0)
    assert metrics["rr"] == pytest.approx(14.0 / 8.0)


def test_pattern_quality_threshold_blocks_scores_below_sixty() -> None:
    config = HeadShoulderTopConfig()

    assert not strategy_module._pattern_quality_allows_alert({"final_score": 59}, config)
    assert strategy_module._pattern_quality_allows_alert({"final_score": 60}, config)
    assert strategy_module._pattern_quality_allows_alert(
        {"final_score": 59},
        HeadShoulderTopConfig(enable_score=False),
    )


def test_top_midpoint_trigger_stops_when_right_shoulder_is_broken_upward() -> None:
    times = pd.date_range("2026-06-14 11:00:00", periods=4, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [99.0, 99.0, 97.0, 94.0],
        "high": [100.0, 100.0, 100.1, 99.0],
        "close": [99.0, 99.0, 100.1, 94.0],
    })
    result = check_right_shoulder_midpoint_trigger(
        df,
        PivotPoint(0, times[0], 90.0, "low"),
        PivotPoint(1, times[1], 100.0, "high"),
        HeadShoulderTopConfig(),
        inverse=False,
    )

    assert not result[0]


def test_inverse_midpoint_trigger_stops_when_price_falls_below_right_shoulder() -> None:
    times = pd.date_range("2026-06-14 12:00:00", periods=4, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [100.0, 100.0, 99.9, 101.0],
        "high": [101.0, 101.0, 103.0, 106.0],
        "close": [101.0, 101.0, 99.9, 106.0],
    })
    result = check_right_shoulder_midpoint_trigger(
        df,
        PivotPoint(0, times[0], 110.0, "high"),
        PivotPoint(1, times[1], 100.0, "low"),
        HeadShoulderTopConfig(),
        inverse=True,
    )

    assert not result[0]


def test_top_midpoint_trigger_ignores_intrabar_wicks() -> None:
    times = pd.date_range("2026-06-14 12:30:00", periods=4, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [99.0, 99.0, 94.0, 94.0],
        "high": [100.0, 100.0, 101.0, 99.0],
        "close": [99.0, 99.0, 97.0, 95.0],
    })
    result = check_right_shoulder_midpoint_trigger(
        df,
        PivotPoint(0, times[0], 90.0, "low"),
        PivotPoint(1, times[1], 100.0, "high"),
        HeadShoulderTopConfig(),
        inverse=False,
    )

    assert result == (True, 3, times[3], 95.0, 95.0)


def test_inverse_midpoint_trigger_ignores_intrabar_wicks() -> None:
    times = pd.date_range("2026-06-14 12:45:00", periods=4, freq="min")
    df = pd.DataFrame({
        "datetime": times,
        "low": [100.0, 100.0, 99.0, 101.0],
        "high": [101.0, 101.0, 106.0, 106.0],
        "close": [101.0, 101.0, 103.0, 105.0],
    })
    result = check_right_shoulder_midpoint_trigger(
        df,
        PivotPoint(0, times[0], 110.0, "high"),
        PivotPoint(1, times[1], 100.0, "low"),
        HeadShoulderTopConfig(),
        inverse=True,
    )

    assert result == (True, 3, times[3], 105.0, 105.0)


def test_top_scan_uses_later_right_shoulder_after_first_is_invalidated(monkeypatch) -> None:
    times = pd.date_range("2026-06-14 13:00:00", periods=15, freq="min")
    first_candidate = (
        PivotPoint(0, times[0], 100.0, "high"),
        PivotPoint(2, times[2], 90.0, "low"),
        PivotPoint(4, times[4], 110.0, "high"),
        PivotPoint(6, times[6], 90.0, "low"),
        PivotPoint(8, times[8], 100.0, "high"),
    )
    later_candidate = (
        *first_candidate[:4],
        PivotPoint(12, times[12], 100.2, "high"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [96.0] * len(times),
        "high": [99.0] * len(times),
        "low": [96.0] * len(times),
        "close": [96.0] * len(times),
        "volume": [1000] * len(times),
    })
    df.loc[9, "close"] = 100.1
    df.loc[13, "close"] = 95.0

    monkeypatch.setattr(strategy_module, "find_structure_pivots_for_timeframe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        strategy_module,
        "iter_timeframe_pattern_candidates",
        lambda *_args, **_kwargs: [first_candidate, later_candidate],
    )
    monkeypatch.setattr(
        strategy_module,
        "validate_head_shoulders_structure",
        lambda *_args, **_kwargs: (True, [], 0),
    )
    monkeypatch.setattr(
        strategy_module,
        "validate_candle_close_constraints",
        lambda *_args, **_kwargs: (True, ""),
    )

    signals = scan_head_shoulders_top(
        df,
        "rb2610",
        "15m",
        HeadShoulderTopConfig(enable_score=False),
    )

    assert len(signals) == 1
    assert signals[0].right_shoulder.index == 12
    assert signals[0].retest_time == times[13]


def test_inverse_scan_uses_later_right_shoulder_after_first_is_invalidated(monkeypatch) -> None:
    times = pd.date_range("2026-06-14 14:00:00", periods=15, freq="min")
    first_candidate = (
        PivotPoint(0, times[0], 100.0, "low"),
        PivotPoint(2, times[2], 110.0, "high"),
        PivotPoint(4, times[4], 90.0, "low"),
        PivotPoint(6, times[6], 110.0, "high"),
        PivotPoint(8, times[8], 100.0, "low"),
    )
    later_candidate = (
        *first_candidate[:4],
        PivotPoint(12, times[12], 99.8, "low"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [104.0] * len(times),
        "high": [104.0] * len(times),
        "low": [101.0] * len(times),
        "close": [104.0] * len(times),
        "volume": [1000] * len(times),
    })
    df.loc[1, "high"] = 111.0
    df.loc[9, "close"] = 99.9
    df.loc[13, "close"] = 105.0

    monkeypatch.setattr(strategy_module, "find_structure_pivots_for_timeframe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        strategy_module,
        "iter_timeframe_pattern_candidates",
        lambda *_args, **_kwargs: [first_candidate, later_candidate],
    )
    monkeypatch.setattr(
        strategy_module,
        "validate_inverse_head_shoulders_structure",
        lambda *_args, **_kwargs: (True, [], 0),
    )
    monkeypatch.setattr(
        strategy_module,
        "validate_candle_close_constraints",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(strategy_module, "inverse_prior_high_exceeds_left_neck", lambda *_args, **_kwargs: True)

    signals = scan_inverse_head_shoulders(
        df,
        "rb2610",
        "15m",
        HeadShoulderTopConfig(enable_score=False),
    )

    assert len(signals) == 1
    assert signals[0].right_shoulder.index == 12
    assert signals[0].retest_time == times[13]


def test_top_scan_accepts_two_bar_shoulder_neck_spans() -> None:
    times = pd.date_range("2026-01-01 09:00:00", periods=20, freq="min")
    rows = []
    closes = [
        98, 100, 99, 94, 95,
        105, 103, 94, 95, 100,
        98, 94, 96, 100, 98,
        94, 96, 99, 97, 93,
    ]
    pivot_highs = {1: 100, 5: 105, 9: 100, 13: 100, 17: 99}
    pivot_lows = {3: 94, 7: 94, 11: 94, 15: 94}
    for index, close in enumerate(closes):
        high = pivot_highs.get(index, close + 0.2)
        low = pivot_lows.get(index, close - 0.2)
        rows.append({
            "datetime": times[index],
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        })
    df = pd.DataFrame(rows)
    config = HeadShoulderTopConfig(
        pivot_left=1,
        pivot_right=1,
        max_shoulder_diff_pct=0.02,
        min_right_leg_to_left_leg_ratio=0.5,
        max_right_leg_to_left_leg_ratio=2.0,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=False,
        enable_score=False,
    )

    signals = scan_head_shoulders_top(df, "rb2405", "15m", config)
    same_setup_signals = [
        signal for signal in signals
        if (
            signal.left_shoulder.index,
            signal.left_neck.index,
            signal.head.index,
            signal.right_neck.index,
        ) == (1, 3, 5, 7)
    ]

    assert len(same_setup_signals) == 1
    assert same_setup_signals[0].right_shoulder.index == 9


def test_top_scan_emits_multiple_right_shoulders_for_same_setup(monkeypatch) -> None:
    times = pd.date_range("2026-06-25 13:30:00", periods=18, freq="3min")
    setup = (
        PivotPoint(0, times[0], 4648.0, "high"),
        PivotPoint(2, times[2], 4634.0, "low"),
        PivotPoint(6, times[6], 4656.0, "high"),
        PivotPoint(10, times[10], 4636.0, "low"),
    )
    first_candidate = (*setup, PivotPoint(12, times[12], 4644.0, "high"))
    later_candidate = (*setup, PivotPoint(16, times[16], 4648.0, "high"))
    df = pd.DataFrame({
        "datetime": times,
        "open": [4642.0] * len(times),
        "high": [4644.0] * len(times),
        "low": [4638.0] * len(times),
        "close": [4642.0] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_structure_pivots_for_timeframe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        strategy_module,
        "iter_timeframe_pattern_candidates",
        lambda *_args, **_kwargs: [first_candidate, later_candidate],
    )
    monkeypatch.setattr(
        strategy_module,
        "validate_head_shoulders_structure",
        lambda *_args, **_kwargs: (True, [], 0),
    )
    monkeypatch.setattr(
        strategy_module,
        "validate_candle_close_constraints",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        strategy_module,
        "check_right_shoulder_midpoint_trigger",
        lambda _df, _right_neck, right_shoulder, _config, *, inverse: (
            True,
            right_shoulder.index + 1,
            times[right_shoulder.index + 1],
            4638.0 if right_shoulder.index == 12 else 4640.0,
            4640.0,
        ),
    )
    monkeypatch.setattr(
        strategy_module,
        "calculate_pattern_score",
        lambda *_args, **_kwargs: {
            "final_score": 80,
            "raw_score": 80,
            "grade": "A",
            "caps": [],
            "sections": [],
            "metrics": {},
        },
    )

    signals = scan_head_shoulders_top(
        df,
        "SHFE.sp2609",
        "3m",
        HeadShoulderTopConfig(enable_score=False),
    )

    assert [signal.right_shoulder.index for signal in signals] == [12, 16]
    assert [signal.retest_time for signal in signals] == [times[13], times[17]]


def test_short_timeframe_candidates_use_five_bar_structure_and_three_bar_right_shoulder() -> None:
    times = pd.date_range("2026-01-01 09:00:00", periods=36, freq="min")
    rows = []
    for index in range(len(times)):
        high = 100.0 + index * 0.01
        low = 90.0 + index * 0.01
        if index == 6:
            high = 110.0
        elif index == 12:
            low = 82.0
        elif index == 18:
            high = 116.0
        elif index == 24:
            low = 83.0
        elif index == 28:
            high = 109.0
        elif index == 32:
            high = 111.0
        rows.append({
            "datetime": times[index],
            "open": (high + low) / 2,
            "high": high,
            "low": low,
            "close": (high + low) / 2,
            "volume": 1000,
        })
    df = pd.DataFrame(rows)

    candidates = strategy_module.iter_timeframe_pattern_candidates(
        df,
        "3m",
        HeadShoulderTopConfig(pivot_left=3, pivot_right=3),
        ["high", "low", "high", "low", "high"],
    )

    candidate_indexes = [(p1.index, p2.index, p3.index, p4.index, p5.index) for p1, p2, p3, p4, p5 in candidates]
    assert (6, 12, 18, 24, 28) in candidate_indexes


def test_one_minute_head_neck_bar_limit_applies_to_top_and_inverse_patterns() -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="min")
    left_neck = PivotPoint(10, times[10], 100, "low")
    head_allowed = PivotPoint(69, times[69], 110, "high")
    right_neck_allowed = PivotPoint(128, times[128], 100, "low")
    head_left_too_far = PivotPoint(70, times[70], 110, "high")
    right_neck_too_far = PivotPoint(129, times[129], 100, "low")

    assert passes_one_minute_head_neck_bar_limit("1m", left_neck, head_allowed, right_neck_allowed)
    assert not passes_one_minute_head_neck_bar_limit("1m", left_neck, head_left_too_far, right_neck_allowed)
    assert not passes_one_minute_head_neck_bar_limit("1m", left_neck, head_allowed, right_neck_too_far)
    assert passes_head_neck_bar_limit("15m", left_neck, head_left_too_far, right_neck_too_far)


def test_head_and_neck_cannot_be_adjacent_candles_on_any_timeframe() -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=20, freq="15min")
    left_neck = PivotPoint(10, times[10], 100, "low")
    head_adjacent_to_left_neck = PivotPoint(11, times[11], 110, "high")
    head = PivotPoint(12, times[12], 110, "high")
    right_neck_adjacent_to_head = PivotPoint(13, times[13], 100, "low")
    right_neck = PivotPoint(14, times[14], 100, "low")

    assert not passes_head_neck_bar_limit("15m", left_neck, head_adjacent_to_left_neck, right_neck)
    assert not passes_head_neck_bar_limit("15m", left_neck, head, right_neck_adjacent_to_head)
    assert passes_head_neck_bar_limit("15m", left_neck, head, right_neck)


def test_five_minute_head_neck_bar_limit_applies_to_top_and_inverse_patterns() -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="5min")
    left_neck = PivotPoint(10, times[10], 100, "low")
    head_allowed = PivotPoint(69, times[69], 110, "high")
    right_neck_allowed = PivotPoint(128, times[128], 100, "low")
    head_left_too_far = PivotPoint(70, times[70], 110, "high")
    right_neck_too_far = PivotPoint(129, times[129], 100, "low")

    assert passes_head_neck_bar_limit("5m", left_neck, head_allowed, right_neck_allowed)
    assert not passes_head_neck_bar_limit("5m", left_neck, head_left_too_far, right_neck_allowed)
    assert not passes_head_neck_bar_limit("5m", left_neck, head_allowed, right_neck_too_far)


def test_three_minute_head_neck_bar_limit_applies_to_top_and_inverse_patterns() -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="3min")
    left_neck = PivotPoint(10, times[10], 100, "low")
    head_allowed = PivotPoint(69, times[69], 110, "high")
    right_neck_allowed = PivotPoint(128, times[128], 100, "low")
    head_left_too_far = PivotPoint(70, times[70], 110, "high")
    right_neck_too_far = PivotPoint(129, times[129], 100, "low")

    assert passes_head_neck_bar_limit("3m", left_neck, head_allowed, right_neck_allowed)
    assert not passes_head_neck_bar_limit("3m", left_neck, head_left_too_far, right_neck_allowed)
    assert not passes_head_neck_bar_limit("3m", left_neck, head_allowed, right_neck_too_far)


def test_one_minute_top_scan_filters_candidates_with_wide_head_neck_distance(monkeypatch) -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="min")
    candidate = (
        PivotPoint(0, times[0], 100, "high"),
        PivotPoint(10, times[10], 90, "low"),
        PivotPoint(70, times[70], 110, "high"),
        PivotPoint(80, times[80], 90, "low"),
        PivotPoint(90, times[90], 100, "high"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100] * len(times),
        "high": [101] * len(times),
        "low": [99] * len(times),
        "close": [100] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_pivots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "compress_pivots", lambda pivots: pivots)
    monkeypatch.setattr(strategy_module, "iter_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_head_shoulders_top(df, "a2607", "1m", HeadShoulderTopConfig()) == []


def test_one_minute_inverse_scan_filters_candidates_with_wide_head_neck_distance(monkeypatch) -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="min")
    candidate = (
        PivotPoint(0, times[0], 100, "low"),
        PivotPoint(10, times[10], 110, "high"),
        PivotPoint(70, times[70], 90, "low"),
        PivotPoint(80, times[80], 110, "high"),
        PivotPoint(90, times[90], 100, "low"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100] * len(times),
        "high": [101] * len(times),
        "low": [99] * len(times),
        "close": [100] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_pivots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "compress_pivots", lambda pivots: pivots)
    monkeypatch.setattr(strategy_module, "iter_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_inverse_head_shoulders(df, "a2607", "1m", HeadShoulderTopConfig()) == []


def test_five_minute_top_scan_filters_candidates_with_wide_head_neck_distance(monkeypatch) -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="5min")
    candidate = (
        PivotPoint(0, times[0], 100, "high"),
        PivotPoint(10, times[10], 90, "low"),
        PivotPoint(70, times[70], 110, "high"),
        PivotPoint(80, times[80], 90, "low"),
        PivotPoint(90, times[90], 100, "high"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100] * len(times),
        "high": [101] * len(times),
        "low": [99] * len(times),
        "close": [100] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_pivots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "compress_pivots", lambda pivots: pivots)
    monkeypatch.setattr(strategy_module, "iter_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_head_shoulders_top(df, "a2607", "5m", HeadShoulderTopConfig()) == []


def test_five_minute_inverse_scan_filters_candidates_with_wide_head_neck_distance(monkeypatch) -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="5min")
    candidate = (
        PivotPoint(0, times[0], 100, "low"),
        PivotPoint(10, times[10], 110, "high"),
        PivotPoint(70, times[70], 90, "low"),
        PivotPoint(80, times[80], 110, "high"),
        PivotPoint(90, times[90], 100, "low"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100] * len(times),
        "high": [101] * len(times),
        "low": [99] * len(times),
        "close": [100] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_pivots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "compress_pivots", lambda pivots: pivots)
    monkeypatch.setattr(strategy_module, "iter_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_inverse_head_shoulders(df, "a2607", "5m", HeadShoulderTopConfig()) == []


def test_three_minute_top_scan_filters_candidates_with_wide_head_neck_distance(monkeypatch) -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="3min")
    candidate = (
        PivotPoint(0, times[0], 100, "high"),
        PivotPoint(10, times[10], 90, "low"),
        PivotPoint(70, times[70], 110, "high"),
        PivotPoint(80, times[80], 90, "low"),
        PivotPoint(90, times[90], 100, "high"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100] * len(times),
        "high": [101] * len(times),
        "low": [99] * len(times),
        "close": [100] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_pivots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "compress_pivots", lambda pivots: pivots)
    monkeypatch.setattr(strategy_module, "iter_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_head_shoulders_top(df, "a2607", "3m", HeadShoulderTopConfig()) == []


def test_three_minute_inverse_scan_filters_candidates_with_wide_head_neck_distance(monkeypatch) -> None:
    times = pd.date_range("2026-05-25 09:00:00", periods=130, freq="3min")
    candidate = (
        PivotPoint(0, times[0], 100, "low"),
        PivotPoint(10, times[10], 110, "high"),
        PivotPoint(70, times[70], 90, "low"),
        PivotPoint(80, times[80], 110, "high"),
        PivotPoint(90, times[90], 100, "low"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100] * len(times),
        "high": [101] * len(times),
        "low": [99] * len(times),
        "close": [100] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_pivots", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "compress_pivots", lambda pivots: pivots)
    monkeypatch.setattr(strategy_module, "iter_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_inverse_head_shoulders(df, "a2607", "3m", HeadShoulderTopConfig()) == []


def test_mirrored_sample_finds_confirmed_inverse_signal() -> None:
    df = pd.read_csv(SAMPLE)
    pivot_price = df["high"].max() + df["low"].min()
    mirrored = df.copy()
    mirrored["open"] = pivot_price - df["open"]
    mirrored["high"] = pivot_price - df["low"]
    mirrored["low"] = pivot_price - df["high"]
    mirrored["close"] = pivot_price - df["close"]
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_inverse_head_shoulders(mirrored, "rb2405", "5m", config)
    signal = next(signal for signal in signals if signal.alert_type == "right_shoulder_confirmed")
    midpoint_price = (signal.right_neck.price + signal.right_shoulder.price) / 2
    assert signal.qtr is not None and signal.qtr > 0
    assert signal.retest_time is not None
    assert signal.retest_time > signal.right_shoulder.time
    assert signal.retest_price is not None and signal.retest_price >= midpoint_price


@pytest.mark.parametrize("timeframe", ["3m", "5m"])
def test_inverse_prior_high_rule_applies_to_three_and_five_minute_timeframes(
    monkeypatch,
    timeframe: str,
) -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=45, freq="min")
    candidate = (
        PivotPoint(25, times[25], 100, "low"),
        PivotPoint(28, times[28], 110, "high"),
        PivotPoint(31, times[31], 90, "low"),
        PivotPoint(34, times[34], 110, "high"),
        PivotPoint(37, times[37], 100, "low"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100.0] * len(times),
        "high": [110.0] * len(times),
        "low": [99.0] * len(times),
        "close": [100.0] * len(times),
        "volume": [1000] * len(times),
    })

    monkeypatch.setattr(strategy_module, "find_structure_pivots_for_timeframe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "iter_timeframe_pattern_candidates", lambda *_args, **_kwargs: [candidate])
    config = HeadShoulderTopConfig(enable_score=False)

    df.loc[38, "close"] = 105.0
    df.loc[4, "high"] = 111.0
    assert scan_inverse_head_shoulders(df, "a2607", timeframe, config) == []

    df.loc[5, "high"] = 111.0
    assert scan_inverse_head_shoulders(df, "a2607", timeframe, config)


@pytest.mark.parametrize("timeframe", ["1m", "15m", "30m", "1h"])
def test_inverse_prior_high_rule_does_not_apply_to_other_timeframes(
    monkeypatch,
    timeframe: str,
) -> None:
    times = pd.date_range("2026-06-14 09:00:00", periods=45, freq="min")
    candidate = (
        PivotPoint(25, times[25], 100, "low"),
        PivotPoint(28, times[28], 110, "high"),
        PivotPoint(31, times[31], 90, "low"),
        PivotPoint(34, times[34], 110, "high"),
        PivotPoint(37, times[37], 100, "low"),
    )
    df = pd.DataFrame({
        "datetime": times,
        "open": [100.0] * len(times),
        "high": [110.0] * len(times),
        "low": [99.0] * len(times),
        "close": [100.0] * len(times),
        "volume": [1000] * len(times),
    })
    df.loc[38, "close"] = 105.0

    monkeypatch.setattr(strategy_module, "find_structure_pivots_for_timeframe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(strategy_module, "iter_timeframe_pattern_candidates", lambda *_args, **_kwargs: [candidate])

    assert scan_inverse_head_shoulders(
        df,
        "a2607",
        timeframe,
        HeadShoulderTopConfig(enable_score=False),
    )


def test_combined_scan_returns_pattern_field() -> None:
    df = pd.read_csv(SAMPLE)
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders(df, "rb2405", "5m", config)
    assert any(signal.pattern == "head_shoulders_top" for signal in signals)


def test_combined_scan_includes_inverse_pattern() -> None:
    df = pd.read_csv(SAMPLE)
    pivot_price = df["high"].max() + df["low"].min()
    mirrored = df.copy()
    mirrored["open"] = pivot_price - df["open"]
    mirrored["high"] = pivot_price - df["low"]
    mirrored["low"] = pivot_price - df["high"]
    mirrored["close"] = pivot_price - df["close"]
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders(mirrored, "rb2405", "5m", config)
    assert any(signal.pattern == "inverse_head_shoulders" for signal in signals)


def test_bearish_ma_trend_score_uses_new_fifty_point_system() -> None:
    closes = [180 - i * 0.2 for i in range(40)] + [172 - (i - 39) * 3 for i in range(40, 80)]
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-03-14 09:00:00", periods=80, freq="h"),
        "open": closes,
        "high": [close + 1 for close in closes],
        "low": [close - 1 for close in closes],
        "close": closes,
        "volume": [1000] * 80,
    })
    enriched = strategy_module.add_ma_columns(df, HeadShoulderTopConfig())

    score, reasons = calculate_ma_trend_score(enriched, len(enriched) - 1, bullish=False)

    assert score == 50
    assert any("均线排列目标为空头排列" in reason and "15.0/15" in reason for reason in reasons)
    assert any("收盘价跌破 MA60 确认项：5.0/5" in reason for reason in reasons)


def test_bullish_ma_trend_score_uses_new_fifty_point_system() -> None:
    closes = [100 + i * 0.2 for i in range(40)] + [108 + (i - 39) * 3 for i in range(40, 80)]
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-06-01 09:00:00", periods=80, freq="min"),
        "open": closes,
        "high": [close + 1 for close in closes],
        "low": [close - 1 for close in closes],
        "close": closes,
        "volume": [1000] * 80,
    })
    enriched = strategy_module.add_ma_columns(df, HeadShoulderTopConfig())

    score, reasons = calculate_ma_trend_score(enriched, len(enriched) - 1, bullish=True)

    assert score == 50
    assert any("均线排列目标为多头排列" in reason and "15.0/15" in reason for reason in reasons)
    assert any("收盘价站上 MA60 确认项：5.0/5" in reason for reason in reasons)


def test_bullish_ma_slope_score_uses_ma10_and_ma20() -> None:
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-06-01 09:00:00", periods=80, freq="h"),
        "open": [100] * 80,
        "high": [101] * 80,
        "low": [99] * 80,
        "close": [100] * 80,
        "volume": [1000] * 80,
    })
    for period in [5, 10, 20, 30, 60]:
        df[f"ma{period}"] = 100.0
    index = len(df) - 1
    df.loc[index - 5, "ma10"] = 98.0
    df.loc[index, "ma10"] = 99.0
    df.loc[index - 5, "ma20"] = 98.0
    df.loc[index, "ma20"] = 99.0
    df.loc[index - 5, "ma60"] = 96.0
    df.loc[index, "ma60"] = 95.0

    score, reasons = calculate_ma_trend_score(df, index, bullish=True)

    assert any("MA10/MA20 斜率目标为向上" in reason and "10.0/10" in reason for reason in reasons)
    assert score > 0


def test_combined_trend_score_adds_hourly_and_daily_scores_to_one_hundred() -> None:
    hourly_closes = [180 - i * 0.2 for i in range(40)] + [172 - (i - 39) * 3 for i in range(40, 80)]
    hourly = pd.DataFrame({
        "datetime": pd.date_range("2026-06-01 09:00:00", periods=80, freq="min"),
        "open": hourly_closes,
        "high": [close + 1 for close in hourly_closes],
        "low": [close - 1 for close in hourly_closes],
        "close": hourly_closes,
        "volume": [1000] * 80,
    })
    daily_closes = [180 - i * 0.2 for i in range(40)] + [172 - (i - 39) * 3 for i in range(40, 80)]
    daily = pd.DataFrame({
        "datetime": pd.date_range("2026-03-14", periods=80, freq="D"),
        "open": daily_closes,
        "high": [close + 1 for close in daily_closes],
        "low": [close - 1 for close in daily_closes],
        "close": daily_closes,
        "volume": [1000] * 80,
    })

    score, reasons = calculate_combined_trend_score(
        hourly,
        bullish=False,
        signal_time=hourly["datetime"].iloc[-1],
        daily_df=daily,
    )

    assert score == 100
    assert "小时线评分：50/50" in reasons
    assert "日线评分：50/50" in reasons


def test_key_zone_trend_score_gives_hourly_full_score_for_top_resistance_touch() -> None:
    times = pd.date_range("2026-06-01 09:00:00", periods=12, freq="h")
    hourly = pd.DataFrame({
        "datetime": times,
        "open": [100.0] * len(times),
        "high": [105.0, 105.0, 105.0, 105.0, 111.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0],
        "low": [95.0] * len(times),
        "close": [100.0] * len(times),
        "volume": [1.0] * len(times),
    })
    config = HeadShoulderTopConfig(
        enable_key_zone_trend_score=True,
        resistance_zone_min=110.0,
        resistance_zone_max=112.0,
    )

    score, reasons = calculate_combined_trend_score(
        hourly,
        bullish=False,
        signal_time=times[6],
        head_time=times[6],
        config=config,
    )

    assert score == 50
    assert any("阻挡区间" in reason for reason in reasons)


def test_key_zone_trend_score_gives_hourly_full_score_for_inverse_support_touch() -> None:
    times = pd.date_range("2026-06-01 09:00:00", periods=12, freq="h")
    hourly = pd.DataFrame({
        "datetime": times,
        "open": [100.0] * len(times),
        "high": [105.0] * len(times),
        "low": [95.0, 95.0, 95.0, 95.0, 89.5, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0],
        "close": [100.0] * len(times),
        "volume": [1.0] * len(times),
    })
    config = HeadShoulderTopConfig(
        enable_key_zone_trend_score=True,
        support_zone_min=88.0,
        support_zone_max=90.0,
    )

    score, reasons = calculate_combined_trend_score(
        hourly,
        bullish=True,
        signal_time=times[6],
        head_time=times[6],
        config=config,
    )

    assert score == 50
    assert any("支撑区间" in reason for reason in reasons)


def test_trend_label_maps_score_by_pattern_direction() -> None:
    assert trend_label_from_score(85, bullish=False) == "强空头趋势"
    assert trend_label_from_score(70, bullish=False) == "空头趋势"
    assert trend_label_from_score(60, bullish=False) == "空头趋势下震荡"
    assert trend_label_from_score(45, bullish=False) == "震荡趋势"
    assert trend_label_from_score(30, bullish=False) == "多头趋势下震荡"
    assert trend_label_from_score(10, bullish=False) == "多头趋势"

    assert trend_label_from_score(85, bullish=True) == "强多头趋势"
    assert trend_label_from_score(70, bullish=True) == "多头趋势"
    assert trend_label_from_score(60, bullish=True) == "多头趋势下震荡"
    assert trend_label_from_score(45, bullish=True) == "震荡趋势"
    assert trend_label_from_score(30, bullish=True) == "空头趋势下震荡"
    assert trend_label_from_score(10, bullish=True) == "空头趋势"


def test_pivots_and_neckline() -> None:
    df = pd.read_csv(SAMPLE)
    df["datetime"] = pd.to_datetime(df["datetime"])
    pivots = find_pivots(df, 2, 2)
    assert len(pivots) >= 5
    lows = [p for p in pivots if p.kind == "low"]
    price = calculate_neckline_price(lows[0], lows[1], lows[1].index)
    assert price == lows[1].price


def test_pattern_candidates_can_skip_minor_swings() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=9, freq="h")
    pivots = [
        PivotPoint(604, times[0], 3330, "high"),
        PivotPoint(614, times[1], 3291, "low"),
        PivotPoint(623, times[2], 3342, "high"),
        PivotPoint(626, times[3], 3315, "low"),
        PivotPoint(632, times[4], 3340, "high"),
        PivotPoint(638, times[5], 3311, "low"),
        PivotPoint(640, times[6], 3327, "high"),
        PivotPoint(646, times[7], 3301, "low"),
        PivotPoint(652, times[8], 3329, "high"),
    ]
    candidates = iter_pattern_candidates(pivots, ["high", "low", "high", "low", "high"])
    assert any(
        [point.index for point in candidate] == [604, 614, 623, 646, 652]
        for candidate in candidates
    )


def test_pattern_candidates_can_skip_minor_swings_between_left_shoulder_and_neck() -> None:
    times = pd.date_range("2026-04-08 09:15:00", periods=7, freq="h")
    pivots = [
        PivotPoint(8, times[0], 3265, "low"),
        PivotPoint(12, times[1], 3275, "high"),
        PivotPoint(14, times[2], 3268, "low"),
        PivotPoint(24, times[3], 3288, "high"),
        PivotPoint(54, times[4], 3261, "low"),
        PivotPoint(92, times[5], 3288, "high"),
        PivotPoint(105, times[6], 3272, "low"),
    ]
    candidates = iter_pattern_candidates(pivots, ["low", "high", "low", "high", "low"])
    assert any(
        [point.index for point in candidate] == [8, 24, 54, 92, 105]
        for candidate in candidates
    )


def _close_constraint_case(inverse: bool = False) -> tuple[pd.DataFrame, list[PivotPoint]]:
    times = pd.date_range("2026-06-10 09:00:00", periods=13, freq="min")
    if inverse:
        closes = [105, 104, 103, 104, 108, 104, 97, 104, 107, 104, 102, 104, 105]
        prices = [103, 108, 96, 107, 102]
        kinds = ["low", "high", "low", "high", "low"]
    else:
        closes = [95, 96, 97, 96, 92, 96, 103, 96, 93, 96, 98, 96, 95]
        prices = [97, 92, 104, 93, 98]
        kinds = ["high", "low", "high", "low", "high"]
    closes = [float(close) for close in closes]
    df = pd.DataFrame({
        "datetime": times,
        "open": closes,
        "high": [close + 1 for close in closes],
        "low": [close - 1 for close in closes],
        "close": closes,
        "volume": [1000] * len(times),
    })
    indexes = [2, 4, 6, 8, 10]
    points = [
        PivotPoint(index, times[index], price, kind)
        for index, price, kind in zip(indexes, prices, kinds)
    ]
    return df, points


def test_top_candle_close_constraints_cover_head_and_all_three_regions() -> None:
    df, points = _close_constraint_case()
    assert validate_candle_close_constraints(df, points, inverse=False)[0]

    violations = [
        (3, 97.01),
        (3, 91.99),
        (5, 104.01),
        (5, 91.99),
        (7, 92.99),
        (9, 98.01),
        (9, 92.99),
    ]
    for index, close in violations:
        invalid = df.copy()
        invalid.loc[index, "close"] = close
        assert not validate_candle_close_constraints(invalid, points, inverse=False)[0]


def test_inverse_candle_close_constraints_cover_head_and_all_three_regions() -> None:
    df, points = _close_constraint_case(inverse=True)
    assert validate_candle_close_constraints(df, points, inverse=True)[0]

    violations = [
        (3, 102.99),
        (3, 108.01),
        (5, 95.99),
        (5, 108.01),
        (7, 107.01),
        (9, 101.99),
        (9, 107.01),
    ]
    for index, close in violations:
        invalid = df.copy()
        invalid.loc[index, "close"] = close
        assert not validate_candle_close_constraints(invalid, points, inverse=True)[0]


def test_head_close_does_not_need_to_exceed_shoulder_extremes() -> None:
    top_df, top_points = _close_constraint_case()
    top_df.loc[top_points[2].index, "close"] = max(top_points[0].price, top_points[4].price)
    assert validate_candle_close_constraints(top_df, top_points, inverse=False)[0]

    inverse_df, inverse_points = _close_constraint_case(inverse=True)
    inverse_df.loc[inverse_points[2].index, "close"] = min(inverse_points[0].price, inverse_points[4].price)
    assert validate_candle_close_constraints(inverse_df, inverse_points, inverse=True)[0]


def test_candle_close_region_thresholds_allow_equal_prices() -> None:
    top_df, top_points = _close_constraint_case()
    for index, close in [
        (3, top_points[0].price),
        (3, top_points[1].price),
        (5, top_points[2].price),
        (5, top_points[1].price),
        (7, top_points[3].price),
        (9, top_points[4].price),
        (9, top_points[3].price),
    ]:
        allowed = top_df.copy()
        allowed.loc[index, "close"] = close
        assert validate_candle_close_constraints(allowed, top_points, inverse=False)[0]

    inverse_df, inverse_points = _close_constraint_case(inverse=True)
    for index, close in [
        (3, inverse_points[0].price),
        (3, inverse_points[1].price),
        (5, inverse_points[2].price),
        (5, inverse_points[1].price),
        (7, inverse_points[3].price),
        (9, inverse_points[4].price),
        (9, inverse_points[3].price),
    ]:
        allowed = inverse_df.copy()
        allowed.loc[index, "close"] = close
        assert validate_candle_close_constraints(allowed, inverse_points, inverse=True)[0]


def test_head_shoulders_requires_shoulders_within_0_4_pct_but_not_neckline_diff() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        max_shoulder_diff_pct=0.004,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 100.00, "high"),
        PivotPoint(3, times[1], 95.00, "low"),
        PivotPoint(6, times[2], 101.20, "high"),
        PivotPoint(9, times[3], 95.30, "low"),
        PivotPoint(12, times[4], 100.35, "high"),
    ], config)
    assert ok

    shoulder_too_far = [
        PivotPoint(0, times[0], 100.00, "high"),
        PivotPoint(3, times[1], 95.00, "low"),
        PivotPoint(6, times[2], 101.20, "high"),
        PivotPoint(9, times[3], 95.30, "low"),
        PivotPoint(12, times[4], 100.45, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(shoulder_too_far, config)
    assert not ok

    neck_diff_no_longer_hard_filters = [
        PivotPoint(0, times[0], 100.00, "high"),
        PivotPoint(3, times[1], 95.00, "low"),
        PivotPoint(6, times[2], 101.20, "high"),
        PivotPoint(9, times[3], 95.40, "low"),
        PivotPoint(12, times[4], 100.35, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(neck_diff_no_longer_hard_filters, config)
    assert ok


def test_top_and_inverse_require_both_shoulder_neck_spans_above_one_bar() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.02,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    top_prices = [100, 90, 110, 90, 100]
    inverse_prices = [100, 110, 90, 110, 100]
    top_kinds = ["high", "low", "high", "low", "high"]
    inverse_kinds = ["low", "high", "low", "high", "low"]

    for indexes in ([0, 1, 4, 7, 9], [0, 2, 5, 8, 9]):
        top_points = [
            PivotPoint(index, times[position], price, kind)
            for position, (index, price, kind) in enumerate(zip(indexes, top_prices, top_kinds))
        ]
        inverse_points = [
            PivotPoint(index, times[position], price, kind)
            for position, (index, price, kind) in enumerate(zip(indexes, inverse_prices, inverse_kinds))
        ]

        top_ok, top_reasons, _ = validate_head_shoulders_structure(top_points, config)
        inverse_ok, inverse_reasons, _ = validate_inverse_head_shoulders_structure(inverse_points, config)

        assert not top_ok
        assert not inverse_ok
        assert "K线数量都必须大于1" in top_reasons[0]
        assert "K线数量都必须大于1" in inverse_reasons[0]

    valid_indexes = [0, 2, 5, 8, 10]
    valid_top = [
        PivotPoint(index, times[position], price, kind)
        for position, (index, price, kind) in enumerate(zip(valid_indexes, top_prices, top_kinds))
    ]
    valid_inverse = [
        PivotPoint(index, times[position], price, kind)
        for position, (index, price, kind) in enumerate(zip(valid_indexes, inverse_prices, inverse_kinds))
    ]

    assert validate_head_shoulders_structure(valid_top, config)[0]
    assert validate_inverse_head_shoulders_structure(valid_inverse, config)[0]


def test_head_neck_bar_ratio_no_longer_filters_top_or_inverse() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.02,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=20,
    )

    top_ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 100, "high"),
        PivotPoint(10, times[1], 90, "low"),
        PivotPoint(11, times[2], 110, "high"),
        PivotPoint(21, times[3], 90, "low"),
        PivotPoint(31, times[4], 100, "high"),
    ], config)
    inverse_ok, _, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 100, "low"),
        PivotPoint(10, times[1], 110, "high"),
        PivotPoint(11, times[2], 90, "low"),
        PivotPoint(21, times[3], 110, "high"),
        PivotPoint(31, times[4], 100, "low"),
    ], config)

    assert top_ok
    assert inverse_ok


def test_right_leg_ratio_filter_can_be_disabled() -> None:
    times = pd.date_range("2026-06-09 14:57:00", periods=5, freq="h")
    points = [
        PivotPoint(0, times[0], 4336, "high"),
        PivotPoint(3, times[1], 4291, "low"),
        PivotPoint(14, times[2], 4369, "high"),
        PivotPoint(34, times[3], 4275, "low"),
        PivotPoint(55, times[4], 4336, "high"),
    ]
    strict_config = HeadShoulderTopConfig(
        max_shoulder_diff_pct=0.005,
        require_head_beyond_shoulders_and_necks=True,
        require_shoulders_between_opposite_neck_and_head=True,
    )
    ok, reasons, _ = validate_head_shoulders_structure(points, strict_config)
    assert not ok
    assert "右颈到右肩K线数量不匹配" in reasons[0]

    disabled_config = HeadShoulderTopConfig(
        max_shoulder_diff_pct=0.005,
        require_head_beyond_shoulders_and_necks=True,
        require_shoulders_between_opposite_neck_and_head=True,
        enable_right_leg_ratio_filter=False,
    )
    ok, _, _ = validate_head_shoulders_structure(points, disabled_config)
    assert ok


def test_head_shoulders_requires_price_tier_head_to_neck_height() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    cases = [
        (2000.0, 5.0),
        (3000.0, 8.0),
        (5000.0, 10.0),
    ]

    for head_price, required_height in cases:
        exact_threshold = [
            PivotPoint(0, times[0], head_price - 1, "high"),
            PivotPoint(3, times[1], head_price - required_height, "low"),
            PivotPoint(6, times[2], head_price, "high"),
            PivotPoint(9, times[3], head_price - required_height, "low"),
            PivotPoint(12, times[4], head_price - 1, "high"),
        ]
        ok, _, _ = validate_head_shoulders_structure(exact_threshold, config)
        assert not ok

        one_side_above_threshold = [
            PivotPoint(0, times[0], head_price - 1, "high"),
            PivotPoint(3, times[1], head_price - required_height - 0.01, "low"),
            PivotPoint(6, times[2], head_price, "high"),
            PivotPoint(9, times[3], head_price - required_height, "low"),
            PivotPoint(12, times[4], head_price - 1, "high"),
        ]
        ok, _, _ = validate_head_shoulders_structure(one_side_above_threshold, config)
        assert ok


def test_inverse_head_shoulders_requires_price_tier_head_to_neck_height() -> None:
    times = pd.date_range("2026-05-15 13:33:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    ok, reasons, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 3449, "low"),
        PivotPoint(3, times[1], 3455, "high"),
        PivotPoint(6, times[2], 3448, "low"),
        PivotPoint(9, times[3], 3454, "high"),
        PivotPoint(12, times[4], 3450, "low"),
    ], config)

    assert not ok
    assert "高度不足" in reasons[0]


def test_inverse_head_shoulders_price_tier_height_boundaries() -> None:
    times = pd.date_range("2026-05-15 13:33:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    cases = [
        (2000, 5),
        (3000, 8),
        (5000, 10),
    ]
    for head_price, required_height in cases:
        exact_threshold = [
            PivotPoint(0, times[0], head_price + 1, "low"),
            PivotPoint(3, times[1], head_price + required_height, "high"),
            PivotPoint(6, times[2], head_price, "low"),
            PivotPoint(9, times[3], head_price + required_height, "high"),
            PivotPoint(12, times[4], head_price + 1, "low"),
        ]
        ok, _, _ = validate_inverse_head_shoulders_structure(exact_threshold, config)
        assert not ok

        one_side_above_threshold = [
            PivotPoint(0, times[0], head_price + 1, "low"),
            PivotPoint(3, times[1], head_price + required_height + 0.01, "high"),
            PivotPoint(6, times[2], head_price, "low"),
            PivotPoint(9, times[3], head_price + required_height, "high"),
            PivotPoint(12, times[4], head_price + 1, "low"),
        ]
        ok, _, _ = validate_inverse_head_shoulders_structure(one_side_above_threshold, config)
        assert ok


def test_head_shoulders_requires_at_least_one_shoulder_height_ratio() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.3,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 96.5, "high"),
        PivotPoint(3, times[1], 94.0, "low"),
        PivotPoint(6, times[2], 100.0, "high"),
        PivotPoint(9, times[3], 94.0, "low"),
        PivotPoint(12, times[4], 96.5, "high"),
    ], config)
    assert ok

    left_side_too_small = [
        PivotPoint(0, times[0], 95.7, "high"),
        PivotPoint(3, times[1], 94.0, "low"),
        PivotPoint(6, times[2], 100.0, "high"),
        PivotPoint(9, times[3], 94.0, "low"),
        PivotPoint(12, times[4], 96.5, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(left_side_too_small, config)
    assert ok

    right_side_too_small = [
        PivotPoint(0, times[0], 96.5, "high"),
        PivotPoint(3, times[1], 94.0, "low"),
        PivotPoint(6, times[2], 100.0, "high"),
        PivotPoint(9, times[3], 94.0, "low"),
        PivotPoint(12, times[4], 95.7, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(right_side_too_small, config)
    assert ok

    both_sides_too_small = [
        PivotPoint(0, times[0], 95.7, "high"),
        PivotPoint(3, times[1], 94.0, "low"),
        PivotPoint(6, times[2], 100.0, "high"),
        PivotPoint(9, times[3], 94.0, "low"),
        PivotPoint(12, times[4], 95.7, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(both_sides_too_small, config)
    assert not ok


def test_inverse_head_shoulders_requires_at_least_one_shoulder_height_ratio() -> None:
    times = pd.date_range("2026-05-15 21:49:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.3,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
    )

    ok, _, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 5019, "low"),
        PivotPoint(3, times[1], 5030, "high"),
        PivotPoint(6, times[2], 5006, "low"),
        PivotPoint(9, times[3], 5030, "high"),
        PivotPoint(12, times[4], 5023, "low"),
    ], config)
    assert ok


def test_head_shoulders_strict_one_minute_price_rules() -> None:
    times = pd.date_range("2026-05-15 09:00:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.1,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        require_head_beyond_shoulders_and_necks=True,
        require_shoulders_between_opposite_neck_and_head=True,
        min_shoulder_to_neck_height=4,
    )

    ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 96, "high"),
        PivotPoint(3, times[1], 92, "low"),
        PivotPoint(6, times[2], 100, "high"),
        PivotPoint(9, times[3], 93, "low"),
        PivotPoint(12, times[4], 97, "high"),
    ], config)
    assert ok

    head_not_above_neck = [
        PivotPoint(0, times[0], 96, "high"),
        PivotPoint(3, times[1], 92, "low"),
        PivotPoint(6, times[2], 100, "high"),
        PivotPoint(9, times[3], 101, "low"),
        PivotPoint(12, times[4], 97, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(head_not_above_neck, config)
    assert not ok

    right_shoulder_outside_left_neck_to_head = [
        PivotPoint(0, times[0], 96, "high"),
        PivotPoint(3, times[1], 92, "low"),
        PivotPoint(6, times[2], 100, "high"),
        PivotPoint(9, times[3], 93, "low"),
        PivotPoint(12, times[4], 91, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(right_shoulder_outside_left_neck_to_head, config)
    assert not ok

    left_shoulder_outside_head_to_right_neck = [
        PivotPoint(0, times[0], 92, "high"),
        PivotPoint(3, times[1], 88, "low"),
        PivotPoint(6, times[2], 100, "high"),
        PivotPoint(9, times[3], 93, "low"),
        PivotPoint(12, times[4], 97, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(left_shoulder_outside_head_to_right_neck, config)
    assert not ok

    shoulder_neck_diff_too_small = [
        PivotPoint(0, times[0], 95.9, "high"),
        PivotPoint(3, times[1], 92, "low"),
        PivotPoint(6, times[2], 100, "high"),
        PivotPoint(9, times[3], 93, "low"),
        PivotPoint(12, times[4], 97, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(shoulder_neck_diff_too_small, config)
    assert not ok


def test_inverse_head_shoulders_strict_one_minute_price_rules() -> None:
    times = pd.date_range("2026-05-15 09:00:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.1,
        max_shoulder_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        require_head_beyond_shoulders_and_necks=True,
        require_shoulders_between_opposite_neck_and_head=True,
        min_shoulder_to_neck_height=4,
    )

    ok, _, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 104, "low"),
        PivotPoint(3, times[1], 108, "high"),
        PivotPoint(6, times[2], 100, "low"),
        PivotPoint(9, times[3], 107, "high"),
        PivotPoint(12, times[4], 103, "low"),
    ], config)
    assert ok

    head_not_below_neck = [
        PivotPoint(0, times[0], 104, "low"),
        PivotPoint(3, times[1], 108, "high"),
        PivotPoint(6, times[2], 100, "low"),
        PivotPoint(9, times[3], 99, "high"),
        PivotPoint(12, times[4], 103, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(head_not_below_neck, config)
    assert not ok

    right_shoulder_outside_left_neck_to_head = [
        PivotPoint(0, times[0], 104, "low"),
        PivotPoint(3, times[1], 108, "high"),
        PivotPoint(6, times[2], 100, "low"),
        PivotPoint(9, times[3], 107, "high"),
        PivotPoint(12, times[4], 109, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(right_shoulder_outside_left_neck_to_head, config)
    assert not ok

    left_shoulder_outside_head_to_right_neck = [
        PivotPoint(0, times[0], 108, "low"),
        PivotPoint(3, times[1], 112, "high"),
        PivotPoint(6, times[2], 100, "low"),
        PivotPoint(9, times[3], 107, "high"),
        PivotPoint(12, times[4], 103, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(left_shoulder_outside_head_to_right_neck, config)
    assert not ok

    shoulder_neck_diff_too_small = [
        PivotPoint(0, times[0], 104.1, "low"),
        PivotPoint(3, times[1], 108, "high"),
        PivotPoint(6, times[2], 100, "low"),
        PivotPoint(9, times[3], 107, "high"),
        PivotPoint(12, times[4], 103, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(shoulder_neck_diff_too_small, config)
    assert not ok


def test_deduplicate_overlapping_signals_keeps_highest_ranked() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=7, freq="h")
    left_shoulder = PivotPoint(604, times[0], 3330, "high")
    left_neck = PivotPoint(614, times[1], 3291, "low")
    head = PivotPoint(623, times[2], 3342, "high")
    right_neck = PivotPoint(646, times[3], 3301, "low")
    right_shoulder = PivotPoint(652, times[4], 3329, "high")
    weaker = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="1h",
        pattern="head_shoulders_top",
        left_shoulder=left_shoulder,
        left_neck=left_neck,
        head=head,
        right_neck=right_neck,
        right_shoulder=right_shoulder,
        neckline_price=3300,
        confirmed=True,
        score=80,
        reasons=[],
        break_time=times[5],
        break_price=3296,
    )
    stronger = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="1h",
        pattern="head_shoulders_top",
        left_shoulder=left_shoulder,
        left_neck=left_neck,
        head=head,
        right_neck=right_neck,
        right_shoulder=right_shoulder,
        neckline_price=3300,
        confirmed=True,
        score=90,
        reasons=[],
        break_time=times[6],
        break_price=3292,
    )
    assert deduplicate_overlapping_signals([weaker, stronger]) == [stronger]


def test_deduplicate_keeps_same_head_with_different_pivots() -> None:
    times = pd.date_range("2026-02-25 10:00:00", periods=10, freq="h")
    shared_head = PivotPoint(623, times[4], 3342, "high")
    asymmetric = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="1h",
        pattern="head_shoulders_top",
        left_shoulder=PivotPoint(616, times[0], 3310, "high"),
        left_neck=PivotPoint(620, times[1], 3293, "low"),
        head=shared_head,
        right_neck=PivotPoint(638, times[6], 3311, "low"),
        right_shoulder=PivotPoint(640, times[7], 3327, "high"),
        neckline_price=3300,
        confirmed=True,
        score=105,
        reasons=[],
        break_time=times[8],
        break_price=3296,
    )
    symmetric = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="1h",
        pattern="head_shoulders_top",
        left_shoulder=PivotPoint(604, times[2], 3330, "high"),
        left_neck=PivotPoint(614, times[3], 3291, "low"),
        head=shared_head,
        right_neck=PivotPoint(646, times[6], 3301, "low"),
        right_shoulder=PivotPoint(652, times[7], 3329, "high"),
        neckline_price=3300,
        confirmed=True,
        score=90,
        reasons=[],
        break_time=times[9],
        break_price=3296,
    )
    assert deduplicate_overlapping_signals([asymmetric, symmetric]) == [asymmetric, symmetric]


def test_deduplicate_m2609_keeps_same_head_with_different_pivots() -> None:
    times = pd.to_datetime([
        "2026-06-08 13:42:00",
        "2026-06-08 14:42:00",
        "2026-06-08 21:24:00",
        "2026-06-08 21:48:00",
        "2026-06-08 22:09:00",
        "2026-06-09 09:15:00",
        "2026-06-09 09:30:00",
        "2026-06-09 11:03:00",
        "2026-06-09 11:21:00",
    ])
    head = PivotPoint(723, times[4], 2918, "high")
    expected = HeadShoulderTopSignal(
        symbol="m2609",
        timeframe="3m",
        pattern="head_shoulders_top",
        left_shoulder=PivotPoint(708, times[2], 2913, "high"),
        left_neck=PivotPoint(716, times[3], 2899, "low"),
        head=head,
        right_neck=PivotPoint(745, times[5], 2903, "low"),
        right_shoulder=PivotPoint(750, times[6], 2912, "high"),
        neckline_price=2901,
        confirmed=False,
        score=90,
        reasons=[],
    )
    wider = HeadShoulderTopSignal(
        symbol="m2609",
        timeframe="3m",
        pattern="head_shoulders_top",
        left_shoulder=PivotPoint(674, times[0], 2910, "high"),
        left_neck=PivotPoint(694, times[1], 2896, "low"),
        head=head,
        right_neck=PivotPoint(776, times[7], 2897, "low"),
        right_shoulder=PivotPoint(782, times[8], 2901, "high"),
        neckline_price=2897,
        confirmed=False,
        score=95,
        reasons=[],
    )

    assert deduplicate_overlapping_signals([wider, expected]) == [expected, wider]


def test_deduplicate_keeps_same_head_with_different_right_side() -> None:
    times = pd.date_range("2026-05-07 22:00:00", periods=10, freq="h")
    left_shoulder = PivotPoint(10, times[0], 5466, "high")
    left_neck = PivotPoint(18, times[1], 5450, "low")
    head = PivotPoint(32, times[2], 5480, "high")
    early_right = HeadShoulderTopSignal(
        symbol="SR2609",
        timeframe="5m",
        pattern="head_shoulders_top",
        left_shoulder=left_shoulder,
        left_neck=left_neck,
        head=head,
        right_neck=PivotPoint(48, times[4], 5442, "low"),
        right_shoulder=PivotPoint(52, times[5], 5452, "high"),
        neckline_price=5446,
        confirmed=False,
        score=95,
        reasons=[],
    )
    later_right = HeadShoulderTopSignal(
        symbol="SR2609",
        timeframe="5m",
        pattern="head_shoulders_top",
        left_shoulder=left_shoulder,
        left_neck=left_neck,
        head=head,
        right_neck=PivotPoint(58, times[7], 5439, "low"),
        right_shoulder=PivotPoint(64, times[8], 5462, "high"),
        neckline_price=5444,
        confirmed=False,
        score=80,
        reasons=[],
    )
    assert deduplicate_overlapping_signals([early_right, later_right]) == [early_right, later_right]


def test_deduplicate_inverse_prefers_time_symmetry_before_head_depth() -> None:
    times = pd.date_range("2026-04-08 09:00:00", periods=10, freq="h")
    broad = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="15m",
        pattern="inverse_head_shoulders",
        left_shoulder=PivotPoint(14, times[0], 3268, "low"),
        left_neck=PivotPoint(24, times[1], 3288, "high"),
        head=PivotPoint(54, times[2], 3261, "low"),
        right_neck=PivotPoint(92, times[5], 3288, "high"),
        right_shoulder=PivotPoint(105, times[6], 3272, "low"),
        neckline_price=3288,
        confirmed=True,
        score=90,
        reasons=[],
        break_time=times[8],
        break_price=3309,
    )
    narrow = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="15m",
        pattern="inverse_head_shoulders",
        left_shoulder=PivotPoint(95, times[3], 3280, "low"),
        left_neck=PivotPoint(100, times[4], 3287, "high"),
        head=PivotPoint(105, times[5], 3250, "low"),
        right_neck=PivotPoint(115, times[6], 3287, "high"),
        right_shoulder=PivotPoint(124, times[7], 3280, "low"),
        neckline_price=3287,
        confirmed=True,
        score=105,
        reasons=[],
        break_time=times[8],
        break_price=3309,
    )
    assert deduplicate_overlapping_signals([narrow, broad]) == [broad]


def test_deduplicate_uses_head_depth_after_time_symmetry() -> None:
    times = pd.date_range("2026-04-09 09:00:00", periods=7, freq="h")
    shallow = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="15m",
        pattern="inverse_head_shoulders",
        left_shoulder=PivotPoint(10, times[0], 98, "low"),
        left_neck=PivotPoint(20, times[1], 100, "high"),
        head=PivotPoint(30, times[2], 94, "low"),
        right_neck=PivotPoint(40, times[3], 100, "high"),
        right_shoulder=PivotPoint(50, times[4], 98, "low"),
        neckline_price=100,
        confirmed=True,
        score=105,
        reasons=[],
        break_time=times[5],
        break_price=101,
    )
    deep = replace(
        shallow,
        head=PivotPoint(30, times[2], 90, "low"),
        score=80,
        break_time=times[6],
    )

    assert deduplicate_overlapping_signals([shallow, deep]) == [deep]


def test_deduplicate_uses_neckline_height_after_equal_depth() -> None:
    times = pd.date_range("2026-04-10 09:00:00", periods=7, freq="h")
    lower_neckline = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="15m",
        pattern="inverse_head_shoulders",
        left_shoulder=PivotPoint(10, times[0], 95, "low"),
        left_neck=PivotPoint(20, times[1], 100, "high"),
        head=PivotPoint(30, times[2], 90, "low"),
        right_neck=PivotPoint(40, times[3], 100, "high"),
        right_shoulder=PivotPoint(50, times[4], 95, "low"),
        neckline_price=100,
        confirmed=True,
        score=105,
        reasons=[],
        break_time=times[5],
        break_price=101,
    )
    higher_neckline = replace(
        lower_neckline,
        left_shoulder=PivotPoint(10, times[0], 104.5, "low"),
        left_neck=PivotPoint(20, times[1], 110, "high"),
        head=PivotPoint(30, times[2], 99, "low"),
        right_neck=PivotPoint(40, times[3], 110, "high"),
        right_shoulder=PivotPoint(50, times[4], 104.5, "low"),
        neckline_price=110,
        score=80,
        break_time=times[6],
        break_price=111,
    )

    assert deduplicate_overlapping_signals([lower_neckline, higher_neckline]) == [higher_neckline]


def test_deduplicate_inverse_keeps_same_head_with_different_left_setup() -> None:
    times = pd.date_range("2026-06-11 09:00:00", periods=8, freq="h")
    shared_left_neck = PivotPoint(56, times[1], 4712, "high")
    shared_head = PivotPoint(76, times[2], 4644, "low")
    shared_right_neck = PivotPoint(84, times[3], 4718, "high")
    shared_right_shoulder = PivotPoint(94, times[4], 4694, "low")
    broad = HeadShoulderTopSignal(
        symbol="v2609",
        timeframe="3m",
        pattern="inverse_head_shoulders",
        left_shoulder=PivotPoint(20, times[0], 4682, "low"),
        left_neck=shared_left_neck,
        head=shared_head,
        right_neck=shared_right_neck,
        right_shoulder=shared_right_shoulder,
        neckline_price=4720,
        confirmed=False,
        score=90,
        reasons=[],
    )
    near = HeadShoulderTopSignal(
        symbol="v2609",
        timeframe="3m",
        pattern="inverse_head_shoulders",
        left_shoulder=PivotPoint(50, times[0], 4683, "low"),
        left_neck=shared_left_neck,
        head=shared_head,
        right_neck=shared_right_neck,
        right_shoulder=shared_right_shoulder,
        neckline_price=4720,
        confirmed=False,
        score=80,
        reasons=[],
    )
    assert deduplicate_overlapping_signals([broad, near]) == [near, broad]


def test_deduplicate_inverse_keeps_same_head_with_different_right_side() -> None:
    times = pd.date_range("2026-05-15 21:00:00", periods=10, freq="min")
    left_shoulder = PivotPoint(867, times[0], 3442, "low")
    left_neck = PivotPoint(880, times[1], 3449, "high")
    head = PivotPoint(888, times[2], 3437, "low")
    early_right_neck = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="1m",
        pattern="inverse_head_shoulders",
        alert_type="right_shoulder_confirmed",
        left_shoulder=left_shoulder,
        left_neck=left_neck,
        head=head,
        right_neck=PivotPoint(892, times[3], 3443, "high"),
        right_shoulder=PivotPoint(900, times[4], 3439, "low"),
        neckline_price=3439,
        confirmed=False,
        score=60,
        reasons=[],
    )
    higher_right_neck = HeadShoulderTopSignal(
        symbol="hc2610",
        timeframe="1m",
        pattern="inverse_head_shoulders",
        alert_type="right_shoulder_confirmed",
        left_shoulder=left_shoulder,
        left_neck=left_neck,
        head=head,
        right_neck=PivotPoint(904, times[5], 3449, "high"),
        right_shoulder=PivotPoint(910, times[6], 3443, "low"),
        neckline_price=3449,
        confirmed=False,
        score=60,
        reasons=[],
    )

    assert deduplicate_overlapping_signals([early_right_neck, higher_right_neck]) == [early_right_neck, higher_right_neck]


def test_api_scan() -> None:
    client = TestClient(app)
    with SAMPLE.open("rb") as f:
        response = client.post(
            "/api/scan",
            data={
                "symbol": "rb2405",
                "timeframe": "5m",
                "config_overrides": json.dumps({
                    "pivot_left": 2,
                    "pivot_right": 2,
                    "max_shoulder_diff_pct": 0.06,
                    "min_score_to_alert": 70,
                    "require_ma_bearish_alignment": False,
                }),
            },
            files={"file": ("sample.csv", f, "text/csv")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] > 0
    assert any(signal["alert_type"] == "right_shoulder_confirmed" for signal in body["signals"])


def test_market_scan_fetches_daily_klines_for_combined_score(monkeypatch) -> None:
    from app import main

    calls: list[tuple[str, int]] = []
    sample_df = pd.read_csv(SAMPLE)

    async def fake_fetch_kline(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
        calls.append((period, limit))
        return sample_df.copy()

    monkeypatch.setattr(main, "fetch_kline_from_market", fake_fetch_kline)

    client = TestClient(app)
    response = client.get("/api/market/scan", params={"symbol": "rb2405", "timeframe": "5m", "limit": 120})

    assert response.status_code == 200
    assert ("5m", 120) in calls
    assert ("1h", 120) in calls
    assert ("1d", 120) in calls


def test_scan_response_filters_same_head_same_timeframe_by_increasing_pattern_score() -> None:
    from app import main

    times = pd.date_range("2026-06-21 09:00:00", periods=8, freq="min")
    left_shoulder = PivotPoint(0, times[0], 100, "high")
    left_neck = PivotPoint(1, times[1], 92, "low")
    head = PivotPoint(2, times[2], 110, "high")
    right_neck = PivotPoint(3, times[3], 93, "low")

    def signal(timeframe: str, right_index: int, pattern_score: int) -> HeadShoulderTopSignal:
        return HeadShoulderTopSignal(
            symbol="SHFE.sp2609",
            timeframe=timeframe,
            pattern="head_shoulders_top",
            alert_type="right_shoulder_confirmed",
            left_shoulder=left_shoulder,
            left_neck=left_neck,
            head=head,
            right_neck=right_neck,
            right_shoulder=PivotPoint(right_index, times[right_index], 104 + right_index, "high"),
            neckline_price=94,
            confirmed=False,
            score=0,
            pattern_score=pattern_score,
            reasons=[],
        )

    kept = main.filter_scan_signals_by_head_score_progression([
        signal("5m", 4, 80),
        signal("5m", 5, 80),
        signal("5m", 6, 79),
        signal("5m", 7, 81),
        signal("3m", 5, 80),
    ])

    assert [(item.timeframe, item.right_shoulder.index, item.pattern_score) for item in kept] == [
        ("5m", 4, 80),
        ("5m", 7, 81),
        ("3m", 5, 80),
    ]


def test_scan_response_keeps_earliest_pullback_per_same_head() -> None:
    from app import main

    times = pd.date_range("2026-06-21 09:00:00", periods=8, freq="min")
    left_shoulder = PivotPoint(0, times[0], 100, "low")
    left_neck = PivotPoint(1, times[1], 110, "high")
    head = PivotPoint(2, times[2], 90, "low")
    right_neck = PivotPoint(3, times[3], 109, "high")

    def signal(right_index: int, retest_index: int, pattern_score: int) -> HeadShoulderTopSignal:
        return HeadShoulderTopSignal(
            symbol="DCE.a2609",
            timeframe="3m",
            pattern="inverse_head_shoulders",
            alert_type="inverse_head_shoulders_pullback",
            left_shoulder=left_shoulder,
            left_neck=left_neck,
            head=head,
            right_neck=right_neck,
            right_shoulder=PivotPoint(right_index, times[right_index], 98 + right_index, "low"),
            neckline_price=109,
            confirmed=True,
            score=0,
            pattern_score=pattern_score,
            break_time=times[4],
            break_price=111,
            retest_time=times[retest_index],
            retest_price=102,
            reasons=[],
        )

    later = signal(5, 7, 95)
    earlier = signal(4, 6, 90)
    kept = main.filter_scan_signals_by_head_score_progression([later, earlier])

    assert kept == [earlier]
