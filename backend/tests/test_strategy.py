from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.config import get_symbol_prefix, load_head_shoulder_config
from app.csv_loader import normalize_kline_dataframe
from app.main import app
from app.market_client import (
    extract_rows,
    normalize_period,
    normalize_aliyun_period,
    normalize_symbol_row,
    normalize_tushare_symbol,
    normalize_tushare_symbol_row,
    tushare_dataframe_to_kline,
    rows_to_dataframe,
    aliyun_symbol_hints,
)
from app.strategy import (
    HeadShoulderTopConfig,
    calculate_neckline_price,
    find_pivots,
    scan_head_shoulders,
    scan_head_shoulders_range_top,
    scan_head_shoulders_top,
    scan_inverse_head_shoulders,
)


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
    assert config.min_head_above_shoulder_pct == 0.025
    assert config.max_signal_age_bars == 0
    assert config.min_score_to_alert == 65


def test_1h_config_allows_head_range_without_volume_hard_filter() -> None:
    config = load_head_shoulder_config("hc2610", "1h")
    assert config.min_head_above_shoulder_pct == 0.003
    assert config.enable_right_shoulder_volume_weak is False


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
        min_head_above_shoulder_pct=0.03,
        max_shoulder_diff_pct=0.06,
        max_neck_diff_pct=0.04,
        volume_compare_window=2,
        right_shoulder_volume_ratio=0.85,
        break_volume_window=5,
        break_volume_ratio=1.05,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_head_shoulders_top(df, "rb2405", "5m", config)
    assert any(signal.confirmed for signal in signals)


def test_head_range_top_finds_confirmed_signal() -> None:
    df = pd.DataFrame({
        "datetime": pd.date_range("2026-05-08 09:00:00", periods=11, freq="5min"),
        "open": [94, 96, 94, 108, 106, 110, 95, 94, 93, 90, 86],
        "high": [96, 100, 95, 112, 107, 113, 96, 98, 94, 91, 87],
        "low": [93, 95, 90, 106, 104, 108, 89, 93, 91, 85, 84],
        "close": [95, 99, 91, 111, 105, 112, 90, 97, 92, 86, 85],
        "volume": [100, 120, 100, 220, 180, 210, 120, 80, 100, 300, 260],
    })
    config = HeadShoulderTopConfig(
        pivot_left=1,
        pivot_right=1,
        min_head_above_shoulder_pct=0.02,
        max_shoulder_diff_pct=0.05,
        max_neck_diff_pct=0.03,
        min_right_leg_to_left_leg_ratio=0.5,
        max_right_leg_to_left_leg_ratio=2.5,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        max_head_to_right_neck_to_left_neck_to_head_ratio=2.5,
        volume_compare_window=1,
        right_shoulder_volume_ratio=0.8,
        break_volume_window=2,
        break_volume_ratio=1.1,
        enable_ma_filter=False,
        enable_macd_divergence=False,
        min_score_to_alert=70,
    )

    signals = scan_head_shoulders_range_top(df, "c0", "5m", config)

    confirmed = [signal for signal in signals if signal.confirmed]
    assert confirmed
    assert confirmed[0].pattern == "head_shoulders_range_top"
    assert confirmed[0].score == 100


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
        min_head_above_shoulder_pct=0.03,
        max_shoulder_diff_pct=0.06,
        max_neck_diff_pct=0.04,
        volume_compare_window=2,
        right_shoulder_volume_ratio=0.85,
        break_volume_window=5,
        break_volume_ratio=1.05,
        ma_short=3,
        ma_mid=5,
        ma_long=8,
        min_head_to_right_neck_to_left_neck_to_head_ratio=0.5,
        require_ma_bearish_alignment=False,
        require_close_below_ma_long=True,
        min_score_to_alert=70,
    )
    signals = scan_inverse_head_shoulders(mirrored, "rb2405", "5m", config)
    assert any(signal.confirmed for signal in signals)


def test_combined_scan_returns_pattern_field() -> None:
    df = pd.read_csv(SAMPLE)
    config = HeadShoulderTopConfig(
        pivot_left=2,
        pivot_right=2,
        min_head_above_shoulder_pct=0.03,
        max_shoulder_diff_pct=0.06,
        max_neck_diff_pct=0.04,
        volume_compare_window=2,
        right_shoulder_volume_ratio=0.85,
        break_volume_window=5,
        break_volume_ratio=1.05,
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


def test_pivots_and_neckline() -> None:
    df = pd.read_csv(SAMPLE)
    df["datetime"] = pd.to_datetime(df["datetime"])
    pivots = find_pivots(df, 2, 2)
    assert len(pivots) >= 5
    lows = [p for p in pivots if p.kind == "low"]
    price = calculate_neckline_price(lows[0], lows[1], lows[1].index)
    assert price == lows[1].price


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
    assert any(signal["confirmed"] for signal in body["signals"])
