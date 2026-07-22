from __future__ import annotations

import asyncio

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import analysis_cache_store, scan_analysis
from app.schemas import ScanResponse
from app.trading_db import metadata


@pytest.fixture()
def cache_engine(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    metadata.create_all(engine)
    monkeypatch.setattr(analysis_cache_store, "get_engine", lambda: engine)
    yield engine
    engine.dispose()


def cache_values(payload: dict) -> dict:
    return {
        "cache_key": "a" * 64,
        "symbol": "CZCE.CF609",
        "timeframe": "3m",
        "requested_count": 1000,
        "provider": "tqsdk",
        "config_hash": "b" * 64,
        "algorithm_version": "test-v1",
        "main_signature": "main-v1",
        "hourly_signature": "hour-v1",
        "daily_signature": "day-v1",
        "calculation_ms": 12000,
        "payload": payload,
    }


def test_analysis_cache_is_compressed_counted_and_clearable(cache_engine) -> None:
    payload = {"symbol": "CZCE.CF609", "signals": [{"value": "x" * 2000}]}
    analysis_cache_store.save_analysis_cache(**cache_values(payload))

    assert analysis_cache_store.load_analysis_cache("a" * 64) == payload
    stats = analysis_cache_store.analysis_cache_stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["bytes"] < len(str(payload))
    assert analysis_cache_store.clear_analysis_cache() == 1


def test_scan_market_second_request_uses_complete_result_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cached_payload: dict | None = None
    load_calls: list[tuple[str, str, int]] = []
    context = {
        "cache_key": "a" * 64,
        "symbol": "CZCE.CF609",
        "timeframe": "3m",
        "limit": 1000,
        "provider": "tqsdk",
        "config_hash": "b" * 64,
        "algorithm_version": "test-v1",
        "main": "main-v1",
        "hourly": "hour-v1",
        "daily": "day-v1",
        "data_sources": {"main": "database", "hourly": "market", "daily": "market"},
    }

    async def fake_context(*_args):
        return context

    async def fake_load(symbol: str, timeframe: str, limit: int):
        load_calls.append((symbol, timeframe, limit))
        return pd.DataFrame({
            "datetime": pd.date_range("2026-07-22", periods=3, freq="3min"),
            "open": [1, 2, 3], "high": [2, 3, 4], "low": [0, 1, 2],
            "close": [1, 2, 3], "volume": [10, 10, 10],
        })

    def fake_build(*_args, **_kwargs):
        return ScanResponse(
            symbol="CZCE.CF609", timeframe="3m", rows=3,
            start_time=None, end_time=None, config={}, signals=[], chart={},
        )

    def fake_cache_load(_key: str):
        return cached_payload

    def fake_cache_save(**values):
        nonlocal cached_payload
        cached_payload = values["payload"]

    monkeypatch.setattr(scan_analysis, "_cache_context", fake_context)
    monkeypatch.setattr(scan_analysis, "load_kline_for_backtest", fake_load)
    monkeypatch.setattr(scan_analysis, "build_scan_response", fake_build)
    monkeypatch.setattr(scan_analysis, "load_analysis_cache", fake_cache_load)
    monkeypatch.setattr(scan_analysis, "save_analysis_cache", fake_cache_save)

    first = asyncio.run(scan_analysis.scan_market_cached("CZCE.CF609", "3m", 1000))
    second = asyncio.run(scan_analysis.scan_market_cached("CZCE.CF609", "3m", 1000))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.analysis_ms == 0
    assert load_calls == [
        ("CZCE.CF609", "3m", 1000),
        ("CZCE.CF609", "1h", 240),
        ("CZCE.CF609", "1d", 240),
    ]
