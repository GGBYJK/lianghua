from __future__ import annotations

import asyncio
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from app import kline_service, kline_store
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
