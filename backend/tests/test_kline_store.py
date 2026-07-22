from __future__ import annotations

import asyncio
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from app import kline_service, kline_store
from app.config import load_head_shoulder_config
from app.strategy import (
    KLINE_FEATURE_VERSION,
    add_ma_columns,
    add_macd_columns,
    indicator_feature_config_hash,
)
from app.trading_db import metadata, users


@pytest.fixture()
def store_engine(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(users).values(
            id=1,
            username="admin",
            display_name="Admin",
            password_hash="hash",
            status="ACTIVE",
        ))
    monkeypatch.setattr(kline_store, "get_engine", lambda: engine)
    yield engine
    engine.dispose()


def sample_frame(count: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "datetime": pd.date_range("2026-07-22 09:00:00", periods=count, freq="3min"),
        "open": range(100, 100 + count),
        "high": range(101, 101 + count),
        "low": range(99, 99 + count),
        "close": range(100, 100 + count),
        "volume": [1000] * count,
    })


def test_dataset_creation_enqueues_initial_sync(store_engine) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "3m", "tqsdk", 10000, True)
    jobs = kline_store.list_kline_sync_jobs()

    assert dataset["status"] == "QUEUED"
    assert dataset["target_count"] == 10000
    assert len(jobs) == 1
    assert jobs[0]["trigger_type"] == "INITIAL"
    assert jobs[0]["symbol"] == "DCE.a2609"


def test_batch_creation_queues_direct_periods_before_derived_periods(store_engine) -> None:
    datasets = kline_store.create_kline_datasets(
        1,
        "DCE.a2609",
        ["1d", "3m", "1h", "5m"],
        "tqsdk",
        10000,
        True,
    )
    initial_jobs = [
        job for job in kline_store.list_kline_sync_jobs()
        if job["trigger_type"] == "INITIAL"
    ]

    assert [dataset["timeframe"] for dataset in datasets] == ["3m", "5m", "1h", "1d"]
    assert [
        job["timeframe"] for job in sorted(initial_jobs, key=lambda item: item["sequence"])
    ] == ["3m", "5m", "1h", "1d"]


def test_batch_creation_is_atomic_when_one_period_already_exists(store_engine) -> None:
    kline_store.create_kline_dataset(1, "DCE.a2609", "5m", "tqsdk", 10000, True)

    with pytest.raises(kline_store.KlineStoreError, match="5m"):
        kline_store.create_kline_datasets(
            1,
            "DCE.a2609",
            ["3m", "5m", "1h"],
            "tqsdk",
            10000,
            True,
        )

    assert [dataset["timeframe"] for dataset in kline_store.list_kline_datasets()] == ["5m"]


def test_cache_returns_latest_rows_in_ascending_order_and_trims(store_engine) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "3m", "tqsdk", 120, True)
    kline_store.upsert_kline_frame(dataset["id"], sample_frame(5), batch_size=2)
    kline_store.trim_kline_dataset(dataset["id"], 3)

    cached = kline_store.read_cached_klines("DCE.a2609", "3m", "tqsdk", 2)
    refreshed = kline_store.get_kline_dataset(dataset["id"])

    assert cached is not None
    assert list(cached["open"]) == [103.0, 104.0]
    assert cached["datetime"].is_monotonic_increasing
    assert refreshed["row_count"] == 3


def test_upsert_revises_existing_bar(store_engine) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "3m", "tqsdk", 120, True)
    original = sample_frame(2)
    revised = original.tail(1).copy()
    revised.loc[:, "close"] = 888

    kline_store.upsert_kline_frame(dataset["id"], original)
    kline_store.upsert_kline_frame(dataset["id"], revised)
    kline_store.trim_kline_dataset(dataset["id"], 120)
    cached = kline_store.read_cached_klines("DCE.a2609", "3m", "tqsdk", 2)

    assert cached is not None
    assert list(cached["close"]) == [100.0, 888.0]


def test_unchanged_overlap_preserves_revision_and_feature_cache(store_engine) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "15m", "tqsdk", 300, True)
    frame = sample_frame(300)
    kline_store.upsert_kline_frame(dataset["id"], frame)
    kline_store.trim_kline_dataset(dataset["id"], 300)
    config = load_head_shoulder_config("DCE.a2609", "15m")
    enriched = add_macd_columns(add_ma_columns(frame, config), config)
    enriched["trend_bullish"] = None
    enriched["trend_bearish"] = None
    periods = sorted(set([*config.ma_periods, config.ma_short, config.ma_mid, config.ma_long]))
    kline_store.replace_kline_features(
        dataset["id"],
        enriched,
        feature_version=KLINE_FEATURE_VERSION,
        feature_config_hash=indicator_feature_config_hash(config),
        ma_periods=periods,
    )
    before = kline_store.get_kline_dataset(dataset["id"])

    result = kline_store.upsert_kline_frame_with_range(dataset["id"], frame.tail(120))
    kline_store.trim_kline_dataset(dataset["id"], 300, data_changed=result.changed_count > 0)
    after = kline_store.get_kline_dataset(dataset["id"])

    assert result.processed_count == 120
    assert result.changed_count == 0
    assert result.earliest_changed_at is None
    assert after["revision"] == before["revision"]
    assert after["features_ready"] is True


def test_incremental_feature_refresh_matches_full_recalculation(store_engine) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "15m", "tqsdk", 300, True)
    frame = sample_frame(300)
    kline_store.upsert_kline_frame(dataset["id"], frame)
    kline_store.trim_kline_dataset(dataset["id"], 300)
    asyncio.run(kline_service.refresh_kline_dataset_features(
        kline_store.get_kline_dataset(dataset["id"])
    ))
    assert kline_store.get_kline_dataset(dataset["id"])["features_ready"] is True

    revised_tail = frame.tail(2).copy()
    revised_tail.loc[revised_tail.index[0], "close"] = 500
    result = kline_store.upsert_kline_frame_with_range(dataset["id"], revised_tail)
    kline_store.trim_kline_dataset(
        dataset["id"],
        300,
        data_changed=result.changed_count > 0,
    )
    written = asyncio.run(kline_service.refresh_kline_dataset_features(
        kline_store.get_kline_dataset(dataset["id"]),
        changed_from=result.earliest_changed_at,
        allow_incremental=True,
    ))

    expected_input = frame.copy()
    expected_input.loc[revised_tail.index[0], "close"] = 500
    config = load_head_shoulder_config("DCE.a2609", "15m")
    expected = add_macd_columns(add_ma_columns(expected_input, config), config)
    cached = kline_store.read_cached_klines("DCE.a2609", "15m", "tqsdk", 300)

    assert result.changed_count == 1
    assert result.earliest_changed_at == frame.iloc[-2]["datetime"].to_pydatetime()
    assert written == 2
    assert cached is not None
    assert kline_store.get_kline_dataset(dataset["id"])["features_ready"] is True
    for column in ("ma5", "ma60", "ma250", "ema_fast", "ema_slow", "macd_dif", "macd_dea", "macd_hist"):
        assert list(cached.tail(2)[column]) == pytest.approx(list(expected.tail(2)[column]), abs=1e-9)


def test_feature_cache_round_trip_reuses_full_dataset_indicators_and_invalidates_on_write(store_engine) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "3m", "tqsdk", 300, True)
    frame = sample_frame(300)
    kline_store.upsert_kline_frame(dataset["id"], frame)
    kline_store.trim_kline_dataset(dataset["id"], 300)
    config = load_head_shoulder_config("DCE.a2609", "3m")
    enriched = add_macd_columns(add_ma_columns(frame, config), config)
    enriched["trend_bullish"] = 63
    enriched["trend_bearish"] = 37
    periods = sorted(set([*config.ma_periods, config.ma_short, config.ma_mid, config.ma_long]))

    written = kline_store.replace_kline_features(
        dataset["id"],
        enriched,
        feature_version=KLINE_FEATURE_VERSION,
        feature_config_hash=indicator_feature_config_hash(config),
        ma_periods=periods,
    )
    cached = kline_store.read_cached_klines("DCE.a2609", "3m", "tqsdk", 2)
    details = kline_store.list_kline_bars(dataset["id"], 1, 10)

    assert written == 300
    assert cached is not None
    assert cached.attrs["feature_version"] == KLINE_FEATURE_VERSION
    assert cached["ma5"].notna().all()
    assert add_ma_columns(cached, config)["ma5"].notna().all()
    assert list(cached["trend_bullish"]) == [63, 63]
    assert details["items"][0]["ma"]["5"] == pytest.approx(float(enriched.iloc[-1]["ma5"]))
    assert details["items"][0]["macd_hist"] is not None

    revised = frame.tail(1).copy()
    revised.loc[:, "close"] = 999
    kline_store.upsert_kline_frame(dataset["id"], revised)
    invalidated = kline_store.get_kline_dataset(dataset["id"])

    assert invalidated["feature_version"] is None
    assert invalidated["feature_row_count"] == 0


def test_short_timeframe_feature_refresh_caches_trend_score_for_every_bar(store_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = kline_store.create_kline_dataset(1, "DCE.a2609", "3m", "tqsdk", 300, True)
    frame = sample_frame(300)
    kline_store.upsert_kline_frame(dataset["id"], frame)
    kline_store.trim_kline_dataset(dataset["id"], 300)
    hourly = pd.DataFrame({
        "datetime": pd.date_range("2026-06-01", periods=300, freq="h"),
        "open": range(300), "high": range(1, 301), "low": range(300),
        "close": range(300), "volume": [1000] * 300,
    })
    daily = pd.DataFrame({
        "datetime": pd.date_range("2025-01-01", periods=300, freq="D"),
        "open": range(300), "high": range(1, 301), "low": range(300),
        "close": range(300), "volume": [1000] * 300,
    })

    async def fake_load(_symbol: str, timeframe: str, _limit: int) -> pd.DataFrame:
        return hourly if timeframe == "1h" else daily

    monkeypatch.setattr(kline_service, "load_kline_for_backtest", fake_load)
    written = asyncio.run(kline_service.refresh_kline_dataset_features(
        kline_store.get_kline_dataset(dataset["id"])
    ))
    refreshed = kline_store.get_kline_dataset(dataset["id"])
    cached = kline_store.read_cached_klines("DCE.a2609", "3m", "tqsdk", 300)

    assert written == 300
    assert refreshed["feature_version"] == KLINE_FEATURE_VERSION
    assert refreshed["feature_row_count"] == refreshed["row_count"] == 300
    assert refreshed["features_updated_at"] is not None
    assert cached is not None
    assert cached[["trend_bullish", "trend_bearish"]].notna().all().all()
    assert ((cached["trend_bullish"] >= 0) & (cached["trend_bullish"] <= 100)).all()


def test_scheduled_jobs_are_created_once_and_in_symbol_order(store_engine) -> None:
    kline_store.create_kline_dataset(1, "SHFE.rb2610", "5m", "tqsdk", 120, True)
    kline_store.create_kline_dataset(1, "DCE.a2609", "3m", "tqsdk", 120, True)
    schedule_date = datetime(2026, 7, 22).date()

    first = kline_store.enqueue_scheduled_kline_syncs(schedule_date)
    second = kline_store.enqueue_scheduled_kline_syncs(schedule_date)
    scheduled = [job for job in kline_store.list_kline_sync_jobs() if job["trigger_type"] == "SCHEDULED"]

    assert first == 2
    assert second == 0
    assert [(job["symbol"], job["sequence"]) for job in sorted(scheduled, key=lambda item: item["sequence"])] == [
        ("DCE.a2609", 1),
        ("SHFE.rb2610", 2),
    ]


def test_backtest_cache_hit_does_not_call_market(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = sample_frame(3)
    monkeypatch.setattr(kline_service, "read_cached_klines", lambda *_args: expected)

    async def fail_fetch(*_args):
        raise AssertionError("market API should not be called")

    monkeypatch.setattr(kline_service, "_fetch_market", fail_fetch)
    result = asyncio.run(kline_service.load_kline_for_backtest("DCE.a2609", "3m", 1000))

    assert result is expected


def test_sync_request_count_uses_overlap_for_incremental_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kline_service, "SYNC_OVERLAP_BARS", 120)
    dataset = {
        "target_count": 10000,
        "row_count": 10000,
        "end_time": datetime.now(),
        "timeframe": "3m",
    }

    assert kline_service.sync_request_count(dataset) == 120
