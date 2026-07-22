from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .kline_store import (
    claim_next_kline_sync_job,
    enqueue_scheduled_kline_syncs,
    find_kline_dataset,
    finish_kline_sync_job,
    get_kline_dataset,
    read_cached_klines,
    trim_kline_dataset,
    upsert_kline_frame,
)
from .market_client import fetch_kline_from_market, get_market_settings


logger = logging.getLogger("app.kline_service")
MARKET_FETCH_CONCURRENCY = max(1, int(os.getenv("KLINE_FETCH_CONCURRENCY", "1")))
WRITE_BATCH_SIZE = max(100, int(os.getenv("KLINE_WRITE_BATCH_SIZE", "500")))
SYNC_OVERLAP_BARS = max(20, int(os.getenv("KLINE_SYNC_OVERLAP_BARS", "120")))
SCHEDULE_TIMEZONE = ZoneInfo(os.getenv("KLINE_SCHEDULE_TIMEZONE", "Asia/Shanghai"))
SCHEDULE_HOUR = max(0, min(23, int(os.getenv("KLINE_SCHEDULE_HOUR", "3"))))
_market_fetch_gate = asyncio.Semaphore(MARKET_FETCH_CONCURRENCY)
_last_schedule_check: object | None = None


def analysis_prewarm_counts() -> list[int]:
    values: set[int] = set()
    for raw in os.getenv("KLINE_ANALYSIS_PREWARM_COUNTS", "1000").split(","):
        try:
            value = int(raw.strip())
        except ValueError:
            continue
        if value > 0:
            values.add(value)
    return sorted(values)


def backtest_analysis_prewarm_counts() -> list[int]:
    values: set[int] = set()
    for raw in os.getenv("KLINE_BACKTEST_PREWARM_COUNTS", "8000").split(","):
        try:
            value = int(raw.strip())
        except ValueError:
            continue
        if value > 0:
            values.add(value)
    return sorted(values)


def current_market_provider() -> str:
    return str(get_market_settings()["provider"])


def timeframe_seconds(timeframe: str) -> int:
    normalized = timeframe.strip().lower()
    if normalized in {"1d", "day"}:
        return 86400
    if normalized in {"1h", "60m"}:
        return 3600
    if normalized.endswith("m") and normalized[:-1].isdigit():
        return int(normalized[:-1]) * 60
    return 60


def sync_request_count(dataset: dict[str, Any]) -> int:
    target = int(dataset["target_count"])
    if not dataset.get("end_time") or int(dataset.get("row_count") or 0) == 0:
        return target
    end_time = dataset["end_time"]
    if getattr(end_time, "tzinfo", None) is not None:
        end_time = end_time.replace(tzinfo=None)
    missing_estimate = max(0, int((datetime.now() - end_time).total_seconds() / timeframe_seconds(dataset["timeframe"])))
    return min(target, max(SYNC_OVERLAP_BARS, missing_estimate + SYNC_OVERLAP_BARS))


async def _fetch_market(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    async with _market_fetch_gate:
        return await fetch_kline_from_market(symbol, timeframe, limit)


async def load_kline_for_backtest(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    provider = current_market_provider()
    cached = await asyncio.to_thread(read_cached_klines, symbol, timeframe, provider, limit)
    if cached is not None:
        logger.info(
            "backtest K-line cache hit: symbol=%s timeframe=%s requested=%s returned=%s",
            symbol, timeframe, limit, len(cached),
        )
        return cached

    logger.info("backtest K-line cache miss: symbol=%s timeframe=%s limit=%s", symbol, timeframe, limit)
    frame = await _fetch_market(symbol, timeframe, limit)
    dataset = await asyncio.to_thread(find_kline_dataset, symbol, timeframe, provider)
    if dataset is not None and not frame.empty:
        await asyncio.to_thread(upsert_kline_frame, dataset["id"], frame, WRITE_BATCH_SIZE)
        await asyncio.to_thread(trim_kline_dataset, dataset["id"], int(dataset["target_count"]))
    return frame


async def process_next_kline_sync_job(worker_id: str) -> bool:
    job = await asyncio.to_thread(claim_next_kline_sync_job, worker_id)
    if job is None:
        return False
    dataset_id = str(job["dataset_id"])
    fetched_count = 0
    written_count = 0
    try:
        dataset = await asyncio.to_thread(get_kline_dataset, dataset_id)
        provider = current_market_provider()
        if dataset["provider"] != provider:
            raise RuntimeError(f"数据集行情源为 {dataset['provider']}，当前行情源为 {provider}")
        request_count = sync_request_count(dataset)
        frame = await _fetch_market(str(dataset["symbol"]), str(dataset["timeframe"]), request_count)
        fetched_count = len(frame)
        written_count = await asyncio.to_thread(upsert_kline_frame, dataset_id, frame, WRITE_BATCH_SIZE)
        await asyncio.to_thread(trim_kline_dataset, dataset_id, int(dataset["target_count"]))
        await asyncio.to_thread(
            finish_kline_sync_job,
            str(job["id"]),
            dataset_id,
            fetched_count=fetched_count,
            written_count=written_count,
        )
        logger.info(
            "K-line sync completed: symbol=%s timeframe=%s fetched=%s written=%s",
            dataset["symbol"], dataset["timeframe"], fetched_count, written_count,
        )
        # Keep prewarming inside the single sync worker so large scans never fan out.
        from .scan_analysis import scan_market_cached

        for count in analysis_prewarm_counts():
            if count > int(dataset["target_count"]):
                continue
            try:
                await scan_market_cached(
                    str(dataset["symbol"]),
                    str(dataset["timeframe"]),
                    count,
                    {"max_signal_age_bars": 0},
                )
                logger.info(
                    "market analysis prewarmed: symbol=%s timeframe=%s count=%s",
                    dataset["symbol"], dataset["timeframe"], count,
                )
            except Exception:
                logger.exception(
                    "market analysis prewarm failed: symbol=%s timeframe=%s count=%s",
                    dataset["symbol"], dataset["timeframe"], count,
                )
        from .backtest_service import prewarm_backtest_analysis

        for count in backtest_analysis_prewarm_counts():
            if count > int(dataset["target_count"]):
                continue
            try:
                cache_hit = await prewarm_backtest_analysis(
                    str(dataset["symbol"]), str(dataset["timeframe"]), count,
                )
                logger.info(
                    "backtest analysis prewarmed: symbol=%s timeframe=%s count=%s cache_hit=%s",
                    dataset["symbol"], dataset["timeframe"], count, cache_hit,
                )
            except Exception:
                logger.exception(
                    "backtest analysis prewarm failed: symbol=%s timeframe=%s count=%s",
                    dataset["symbol"], dataset["timeframe"], count,
                )
    except Exception as exc:
        logger.exception("K-line sync failed: job=%s dataset=%s", job["id"], dataset_id)
        await asyncio.to_thread(
            finish_kline_sync_job,
            str(job["id"]),
            dataset_id,
            fetched_count=fetched_count,
            written_count=written_count,
            error_message=str(exc),
        )
    return True


async def enqueue_due_scheduled_kline_jobs(now: datetime | None = None) -> int:
    global _last_schedule_check
    local_now = now.astimezone(SCHEDULE_TIMEZONE) if now and now.tzinfo else now or datetime.now(SCHEDULE_TIMEZONE)
    if local_now.time() < time(hour=SCHEDULE_HOUR):
        return 0
    if _last_schedule_check == local_now.date():
        return 0
    created = await asyncio.to_thread(enqueue_scheduled_kline_syncs, local_now.date())
    _last_schedule_check = local_now.date()
    if created:
        logger.info("scheduled K-line jobs queued: date=%s count=%s", local_now.date(), created)
    return created
