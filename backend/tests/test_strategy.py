from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
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
    tqsdk_dataframe_to_kline,
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
    deduplicate_overlapping_signals,
    find_pivots,
    iter_pattern_candidates,
    validate_head_shoulders_structure,
    passes_head_neck_bar_limit,
    passes_one_minute_head_neck_bar_limit,
    scan_head_shoulders,
    scan_head_shoulders_top,
    scan_inverse_head_shoulders,
    validate_inverse_head_shoulders_structure,
)
from app.monitor import build_signal_unique_key
from app.monitor import build_wechat_workbot_content


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample_data" / "head_shoulders_sample.csv"


def test_symbol_prefix() -> None:
    assert get_symbol_prefix("rb2405") == "rb"
    assert get_symbol_prefix("SA405") == "sa"


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


def test_one_minute_config_enables_strict_shape_rules() -> None:
    config_1m = load_head_shoulder_config("rb2405", "1m")
    config_5m = load_head_shoulder_config("rb2405", "5m")

    assert config_1m.require_head_beyond_shoulders_and_necks is True
    assert config_1m.require_shoulders_between_opposite_neck_and_head is True
    assert config_1m.min_shoulder_to_neck_height == 4
    assert config_5m.require_head_beyond_shoulders_and_necks is False
    assert config_5m.require_shoulders_between_opposite_neck_and_head is False
    assert config_5m.min_shoulder_to_neck_height == 0


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
    assert normalize_period("5m") == 2
    assert normalize_period("1h") == 5
    assert normalize_period("1d") == 8


def test_aliyun_period_mapping_and_symbol_hints() -> None:
    assert normalize_aliyun_period("1h") == "60"
    assert normalize_aliyun_period("1d") == "day"
    hints = aliyun_symbol_hints("c0")
    assert hints == [{"symbol": "c0", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn continuous"}]


def test_tqsdk_period_mapping_symbol_hints_and_dataframe() -> None:
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
        "right_shoulder": {"time": normalized["datetime"].iloc[0].isoformat(), "price": 3329},
    }

    content = build_wechat_workbot_content(signal, {"name": "c0"})

    assert content == "新形态：c0，1m，头肩顶，20260428 11:40"


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
        max_neck_diff_pct=0.04,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders_top(df, "rb2405", "5m", config)
    assert any(signal.alert_type == "right_shoulder_confirmed" for signal in signals)


def test_monitor_unique_key_uses_pattern_key_points_and_trigger_time() -> None:
    signal = {
        "symbol": "c0",
        "timeframe": "1m",
        "pattern": "head_shoulders_top",
        "alert_type": "neckline_break",
        "left_shoulder": {"time": "2026-05-12T09:00:00"},
        "head": {"time": "2026-05-12T09:10:00"},
        "right_shoulder": {"time": "2026-05-12T09:20:00"},
        "break_time": "2026-05-12T09:25:00",
        "retest_time": None,
    }
    assert build_signal_unique_key(signal) == (
        "c0|1m|head_shoulders_top|neckline_break|"
        "2026-05-12T09:00:00|2026-05-12T09:10:00|"
        "2026-05-12T09:20:00|2026-05-12T09:25:00"
    )


def test_top_scan_emits_right_shoulder_alert_type_only() -> None:
    df = pd.read_csv(SAMPLE)
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        max_neck_diff_pct=0.04,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders_top(df, "rb2405", "5m", config)
    alert_types = {signal.alert_type for signal in signals}
    assert "right_shoulder_confirmed" in alert_types
    assert "neckline_break" not in alert_types


def test_top_scan_keeps_first_right_shoulder_for_same_left_setup() -> None:
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
        max_neck_diff_pct=0.02,
        min_right_leg_to_left_leg_ratio=0.5,
        max_right_leg_to_left_leg_ratio=2.0,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=False,
        enable_score=False,
    )

    signals = scan_head_shoulders_top(df, "rb2405", "1m", config)
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
        max_neck_diff_pct=0.04,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_inverse_head_shoulders(mirrored, "rb2405", "5m", config)
    assert any(signal.alert_type == "right_shoulder_confirmed" for signal in signals)


def test_combined_scan_returns_pattern_field() -> None:
    df = pd.read_csv(SAMPLE)
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        max_shoulder_diff_pct=0.06,
        max_neck_diff_pct=0.04,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
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
        max_neck_diff_pct=0.04,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders(mirrored, "rb2405", "5m", config)
    assert any(signal.pattern == "inverse_head_shoulders" for signal in signals)


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


def test_head_shoulders_requires_shoulders_and_necks_within_0_4_pct() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        max_shoulder_diff_pct=0.004,
        max_neck_diff_pct=0.004,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
    )

    ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 100.00, "high"),
        PivotPoint(1, times[1], 95.00, "low"),
        PivotPoint(2, times[2], 101.20, "high"),
        PivotPoint(3, times[3], 95.30, "low"),
        PivotPoint(4, times[4], 100.35, "high"),
    ], config)
    assert ok

    shoulder_too_far = [
        PivotPoint(0, times[0], 100.00, "high"),
        PivotPoint(1, times[1], 95.00, "low"),
        PivotPoint(2, times[2], 101.20, "high"),
        PivotPoint(3, times[3], 95.30, "low"),
        PivotPoint(4, times[4], 100.45, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(shoulder_too_far, config)
    assert not ok

    neck_too_far = [
        PivotPoint(0, times[0], 100.00, "high"),
        PivotPoint(1, times[1], 95.00, "low"),
        PivotPoint(2, times[2], 101.20, "high"),
        PivotPoint(3, times[3], 95.40, "low"),
        PivotPoint(4, times[4], 100.35, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(neck_too_far, config)
    assert not ok


def test_head_shoulders_requires_price_tier_head_to_neck_height() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
    )

    cases = [
        (2000.0, 5.0),
        (3000.0, 8.0),
        (5000.0, 10.0),
    ]

    for head_price, required_height in cases:
        exact_threshold = [
            PivotPoint(0, times[0], head_price - 1, "high"),
            PivotPoint(1, times[1], head_price - required_height, "low"),
            PivotPoint(2, times[2], head_price, "high"),
            PivotPoint(3, times[3], head_price - required_height, "low"),
            PivotPoint(4, times[4], head_price - 1, "high"),
        ]
        ok, _, _ = validate_head_shoulders_structure(exact_threshold, config)
        assert not ok

        one_side_above_threshold = [
            PivotPoint(0, times[0], head_price - 1, "high"),
            PivotPoint(1, times[1], head_price - required_height - 0.01, "low"),
            PivotPoint(2, times[2], head_price, "high"),
            PivotPoint(3, times[3], head_price - required_height, "low"),
            PivotPoint(4, times[4], head_price - 1, "high"),
        ]
        ok, _, _ = validate_head_shoulders_structure(one_side_above_threshold, config)
        assert ok


def test_inverse_head_shoulders_requires_price_tier_head_to_neck_height() -> None:
    times = pd.date_range("2026-05-15 13:33:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
    )

    ok, reasons, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 3449, "low"),
        PivotPoint(1, times[1], 3455, "high"),
        PivotPoint(2, times[2], 3448, "low"),
        PivotPoint(3, times[3], 3454, "high"),
        PivotPoint(4, times[4], 3450, "low"),
    ], config)

    assert not ok
    assert "height is insufficient" in reasons[0]


def test_inverse_head_shoulders_price_tier_height_boundaries() -> None:
    times = pd.date_range("2026-05-15 13:33:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.01,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
    )

    cases = [
        (2000, 5),
        (3000, 8),
        (5000, 10),
    ]
    for head_price, required_height in cases:
        exact_threshold = [
            PivotPoint(0, times[0], head_price + 1, "low"),
            PivotPoint(1, times[1], head_price + required_height, "high"),
            PivotPoint(2, times[2], head_price, "low"),
            PivotPoint(3, times[3], head_price + required_height, "high"),
            PivotPoint(4, times[4], head_price + 1, "low"),
        ]
        ok, _, _ = validate_inverse_head_shoulders_structure(exact_threshold, config)
        assert not ok

        one_side_above_threshold = [
            PivotPoint(0, times[0], head_price + 1, "low"),
            PivotPoint(1, times[1], head_price + required_height + 0.01, "high"),
            PivotPoint(2, times[2], head_price, "low"),
            PivotPoint(3, times[3], head_price + required_height, "high"),
            PivotPoint(4, times[4], head_price + 1, "low"),
        ]
        ok, _, _ = validate_inverse_head_shoulders_structure(one_side_above_threshold, config)
        assert ok


def test_head_shoulders_requires_at_least_one_shoulder_height_ratio() -> None:
    times = pd.date_range("2026-03-18 10:00:00", periods=5, freq="h")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.3,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
    )

    ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 96.5, "high"),
        PivotPoint(1, times[1], 94.0, "low"),
        PivotPoint(2, times[2], 100.0, "high"),
        PivotPoint(3, times[3], 94.0, "low"),
        PivotPoint(4, times[4], 96.5, "high"),
    ], config)
    assert ok

    left_side_too_small = [
        PivotPoint(0, times[0], 95.7, "high"),
        PivotPoint(1, times[1], 94.0, "low"),
        PivotPoint(2, times[2], 100.0, "high"),
        PivotPoint(3, times[3], 94.0, "low"),
        PivotPoint(4, times[4], 96.5, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(left_side_too_small, config)
    assert ok

    right_side_too_small = [
        PivotPoint(0, times[0], 96.5, "high"),
        PivotPoint(1, times[1], 94.0, "low"),
        PivotPoint(2, times[2], 100.0, "high"),
        PivotPoint(3, times[3], 94.0, "low"),
        PivotPoint(4, times[4], 95.7, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(right_side_too_small, config)
    assert ok

    both_sides_too_small = [
        PivotPoint(0, times[0], 95.7, "high"),
        PivotPoint(1, times[1], 94.0, "low"),
        PivotPoint(2, times[2], 100.0, "high"),
        PivotPoint(3, times[3], 94.0, "low"),
        PivotPoint(4, times[4], 95.7, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(both_sides_too_small, config)
    assert not ok


def test_inverse_head_shoulders_requires_at_least_one_shoulder_height_ratio() -> None:
    times = pd.date_range("2026-05-15 21:49:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.3,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
    )

    ok, _, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 5019, "low"),
        PivotPoint(1, times[1], 5030, "high"),
        PivotPoint(2, times[2], 5006, "low"),
        PivotPoint(3, times[3], 5030, "high"),
        PivotPoint(4, times[4], 5023, "low"),
    ], config)
    assert ok


def test_head_shoulders_strict_one_minute_price_rules() -> None:
    times = pd.date_range("2026-05-15 09:00:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.1,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
        require_head_beyond_shoulders_and_necks=True,
        require_shoulders_between_opposite_neck_and_head=True,
        min_shoulder_to_neck_height=4,
    )

    ok, _, _ = validate_head_shoulders_structure([
        PivotPoint(0, times[0], 96, "high"),
        PivotPoint(1, times[1], 92, "low"),
        PivotPoint(2, times[2], 100, "high"),
        PivotPoint(3, times[3], 93, "low"),
        PivotPoint(4, times[4], 97, "high"),
    ], config)
    assert ok

    head_not_above_neck = [
        PivotPoint(0, times[0], 96, "high"),
        PivotPoint(1, times[1], 92, "low"),
        PivotPoint(2, times[2], 100, "high"),
        PivotPoint(3, times[3], 101, "low"),
        PivotPoint(4, times[4], 97, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(head_not_above_neck, config)
    assert not ok

    right_shoulder_outside_left_neck_to_head = [
        PivotPoint(0, times[0], 96, "high"),
        PivotPoint(1, times[1], 92, "low"),
        PivotPoint(2, times[2], 100, "high"),
        PivotPoint(3, times[3], 93, "low"),
        PivotPoint(4, times[4], 91, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(right_shoulder_outside_left_neck_to_head, config)
    assert not ok

    left_shoulder_outside_head_to_right_neck = [
        PivotPoint(0, times[0], 92, "high"),
        PivotPoint(1, times[1], 88, "low"),
        PivotPoint(2, times[2], 100, "high"),
        PivotPoint(3, times[3], 93, "low"),
        PivotPoint(4, times[4], 97, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(left_shoulder_outside_head_to_right_neck, config)
    assert not ok

    shoulder_neck_diff_too_small = [
        PivotPoint(0, times[0], 95.9, "high"),
        PivotPoint(1, times[1], 92, "low"),
        PivotPoint(2, times[2], 100, "high"),
        PivotPoint(3, times[3], 93, "low"),
        PivotPoint(4, times[4], 97, "high"),
    ]
    ok, _, _ = validate_head_shoulders_structure(shoulder_neck_diff_too_small, config)
    assert not ok


def test_inverse_head_shoulders_strict_one_minute_price_rules() -> None:
    times = pd.date_range("2026-05-15 09:00:00", periods=5, freq="min")
    config = HeadShoulderTopConfig(
        min_shoulder_to_head_height_ratio=0.1,
        max_shoulder_diff_pct=0.5,
        max_neck_diff_pct=0.5,
        min_right_leg_to_left_leg_ratio=0.1,
        max_right_leg_to_left_leg_ratio=10,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.1,
        max_head_to_right_neck_to_left_neck_to_head_ratio=10,
        require_head_beyond_shoulders_and_necks=True,
        require_shoulders_between_opposite_neck_and_head=True,
        min_shoulder_to_neck_height=4,
    )

    ok, _, _ = validate_inverse_head_shoulders_structure([
        PivotPoint(0, times[0], 104, "low"),
        PivotPoint(1, times[1], 108, "high"),
        PivotPoint(2, times[2], 100, "low"),
        PivotPoint(3, times[3], 107, "high"),
        PivotPoint(4, times[4], 103, "low"),
    ], config)
    assert ok

    head_not_below_neck = [
        PivotPoint(0, times[0], 104, "low"),
        PivotPoint(1, times[1], 108, "high"),
        PivotPoint(2, times[2], 100, "low"),
        PivotPoint(3, times[3], 99, "high"),
        PivotPoint(4, times[4], 103, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(head_not_below_neck, config)
    assert not ok

    right_shoulder_outside_left_neck_to_head = [
        PivotPoint(0, times[0], 104, "low"),
        PivotPoint(1, times[1], 108, "high"),
        PivotPoint(2, times[2], 100, "low"),
        PivotPoint(3, times[3], 107, "high"),
        PivotPoint(4, times[4], 109, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(right_shoulder_outside_left_neck_to_head, config)
    assert not ok

    left_shoulder_outside_head_to_right_neck = [
        PivotPoint(0, times[0], 108, "low"),
        PivotPoint(1, times[1], 112, "high"),
        PivotPoint(2, times[2], 100, "low"),
        PivotPoint(3, times[3], 107, "high"),
        PivotPoint(4, times[4], 103, "low"),
    ]
    ok, _, _ = validate_inverse_head_shoulders_structure(left_shoulder_outside_head_to_right_neck, config)
    assert not ok

    shoulder_neck_diff_too_small = [
        PivotPoint(0, times[0], 104.1, "low"),
        PivotPoint(1, times[1], 108, "high"),
        PivotPoint(2, times[2], 100, "low"),
        PivotPoint(3, times[3], 107, "high"),
        PivotPoint(4, times[4], 103, "low"),
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


def test_deduplicate_prefers_nearest_left_setup_when_head_matches() -> None:
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
    assert deduplicate_overlapping_signals([asymmetric, symmetric]) == [asymmetric]


def test_deduplicate_prefers_later_right_setup_when_left_and_head_match() -> None:
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
    assert deduplicate_overlapping_signals([early_right, later_right]) == [later_right]


def test_deduplicate_prefers_broader_inverse_setup() -> None:
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
        head=PivotPoint(105, times[5], 3272, "low"),
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


def test_deduplicate_inverse_prefers_higher_right_neck_near_left_neck() -> None:
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

    assert deduplicate_overlapping_signals([early_right_neck, higher_right_neck]) == [higher_right_neck]


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
                    "max_neck_diff_pct": 0.04,
                    "min_score_to_alert": 70,
                    "min_head_to_right_neck_to_left_neck_to_head_ratio": 0.5,
                    "require_ma_bearish_alignment": False,
                }),
            },
            files={"file": ("sample.csv", f, "text/csv")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] > 0
    assert any(signal["alert_type"] == "right_shoulder_confirmed" for signal in body["signals"])
