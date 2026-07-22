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
    list_kline_datasets,
    read_cached_klines,
    read_kline_dataset_frame,
    read_kline_dataset_window,
    read_kline_feature_seed,
    replace_kline_features,
    trim_kline_dataset,
    update_kline_trend_features,
    upsert_kline_frame,
    upsert_kline_features,
    upsert_kline_frame_with_range,
)
from .config import load_head_shoulder_config
from .market_client import fetch_kline_from_market, get_market_settings
from .strategy import (
    KLINE_FEATURE_VERSION,
    add_ma_columns,
    add_macd_columns,
    calculate_combined_trend_score_series,
    indicator_feature_config_hash,
)


logger = logging.getLogger("app.kline_service")
MARKET_FETCH_CONCURRENCY = max(1, int(os.getenv("KLINE_FETCH_CONCURRENCY", "1")))
WRITE_BATCH_SIZE = max(100, int(os.getenv("KLINE_WRITE_BATCH_SIZE", "500")))
SYNC_OVERLAP_BARS = max(20, int(os.getenv("KLINE_SYNC_OVERLAP_BARS", "120")))
TREND_FEATURE_TIMEFRAMES = {"1m", "3m", "5m"}
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


def _build_kline_feature_frame(
    frame: pd.DataFrame,
    config: Any,
    hourly: pd.DataFrame | None,
    daily: pd.DataFrame | None,
) -> pd.DataFrame:
    enriched = add_macd_columns(add_ma_columns(frame, config), config)
    if hourly is not None and daily is not None:
        scores = calculate_combined_trend_score_series(
            hourly,
            daily,
            enriched["datetime"].tolist(),
        )
        enriched["trend_bullish"] = [item["bullish"] for item in scores]
        enriched["trend_bearish"] = [item["bearish"] for item in scores]
    else:
        enriched["trend_bullish"] = None
        enriched["trend_bearish"] = None
    return enriched


def _add_incremental_macd_columns(
    frame: pd.DataFrame,
    config: Any,
    seed: dict[str, Any] | None,
) -> pd.DataFrame:
    enriched = frame.copy()
    alpha_fast = 2.0 / (int(config.macd_fast) + 1.0)
    alpha_slow = 2.0 / (int(config.macd_slow) + 1.0)
    alpha_signal = 2.0 / (int(config.macd_signal) + 1.0)
    previous_fast = seed.get("ema_fast") if seed else None
    previous_slow = seed.get("ema_slow") if seed else None
    previous_dea = seed.get("macd_dea") if seed else None
    ema_fast_values: list[float] = []
    ema_slow_values: list[float] = []
    dif_values: list[float] = []
    dea_values: list[float] = []
    hist_values: list[float] = []

    for raw_close in enriched["close"]:
        close = float(raw_close)
        ema_fast = close if previous_fast is None else alpha_fast * close + (1.0 - alpha_fast) * previous_fast
        ema_slow = close if previous_slow is None else alpha_slow * close + (1.0 - alpha_slow) * previous_slow
        dif = ema_fast - ema_slow
        dea = dif if previous_dea is None else alpha_signal * dif + (1.0 - alpha_signal) * previous_dea
        ema_fast_values.append(ema_fast)
        ema_slow_values.append(ema_slow)
        dif_values.append(dif)
        dea_values.append(dea)
        hist_values.append(2.0 * (dif - dea))
        previous_fast = ema_fast
        previous_slow = ema_slow
        previous_dea = dea

    enriched["ema_fast"] = ema_fast_values
    enriched["ema_slow"] = ema_slow_values
    enriched["macd_dif"] = dif_values
    enriched["macd_dea"] = dea_values
    enriched["macd_hist"] = hist_values
    return enriched


async def refresh_kline_dataset_features(
    dataset: dict[str, Any],
    *,
    changed_from: datetime | None = None,
    allow_incremental: bool = False,
) -> int:
    dataset_id = str(dataset["id"])
    symbol = str(dataset["symbol"])
    timeframe = str(dataset["timeframe"])
    config = load_head_shoulder_config(symbol, timeframe)
    ma_periods = sorted(set([*config.ma_periods, config.ma_short, config.ma_mid, config.ma_long]))
    feature_config_hash = indicator_feature_config_hash(config)

    if allow_incremental and changed_from is not None and dataset.get("start_time") is not None:
        start_time = dataset["start_time"]
        if getattr(start_time, "tzinfo", None) is not None:
            start_time = start_time.replace(tzinfo=None)
        if getattr(changed_from, "tzinfo", None) is not None:
            changed_from = changed_from.replace(tzinfo=None)
        changed_from = max(changed_from, start_time)
        window = await asyncio.to_thread(
            read_kline_dataset_window,
            dataset_id,
            changed_from,
            max(ma_periods, default=1) - 1,
        )
        if window.empty:
            return 0
        enriched_window = await asyncio.to_thread(add_ma_columns, window, config)
        affected = enriched_window.loc[
            pd.to_datetime(enriched_window["datetime"]) >= pd.Timestamp(changed_from)
        ].copy()
        seed = await asyncio.to_thread(read_kline_feature_seed, dataset_id, changed_from)
        affected = await asyncio.to_thread(_add_incremental_macd_columns, affected, config, seed)

        if timeframe in TREND_FEATURE_TIMEFRAMES:
            support_limit = max(240, min(int(dataset.get("row_count") or len(window)), 600))
            hourly, daily = await asyncio.gather(
                load_kline_for_backtest(symbol, "1h", support_limit),
                load_kline_for_backtest(symbol, "1d", support_limit),
            )
            scores = await asyncio.to_thread(
                calculate_combined_trend_score_series,
                hourly,
                daily,
                affected["datetime"].tolist(),
            )
            affected["trend_bullish"] = [item["bullish"] for item in scores]
            affected["trend_bearish"] = [item["bearish"] for item in scores]
        else:
            affected["trend_bullish"] = None
            affected["trend_bearish"] = None

        written = await asyncio.to_thread(
            upsert_kline_features,
            dataset_id,
            affected,
            feature_version=KLINE_FEATURE_VERSION,
            feature_config_hash=feature_config_hash,
            ma_periods=ma_periods,
            batch_size=WRITE_BATCH_SIZE,
        )
        logger.info(
            "K-line features incrementally refreshed: symbol=%s timeframe=%s changed_from=%s rows=%s trend=%s",
            symbol,
            timeframe,
            changed_from,
            written,
            timeframe in TREND_FEATURE_TIMEFRAMES,
        )
        return written

    frame = await asyncio.to_thread(read_kline_dataset_frame, str(dataset["id"]))
    if frame.empty:
        return await asyncio.to_thread(
            replace_kline_features,
            str(dataset["id"]),
            frame,
            feature_version=KLINE_FEATURE_VERSION,
            feature_config_hash="",
            ma_periods=[],
            batch_size=WRITE_BATCH_SIZE,
        )

    hourly: pd.DataFrame | None = None
    daily: pd.DataFrame | None = None
    if timeframe in TREND_FEATURE_TIMEFRAMES:
        support_limit = max(240, min(len(frame), 600))
        hourly, daily = await asyncio.gather(
            load_kline_for_backtest(symbol, "1h", support_limit),
            load_kline_for_backtest(symbol, "1d", support_limit),
        )

    enriched = await asyncio.to_thread(
        _build_kline_feature_frame,
        frame,
        config,
        hourly,
        daily,
    )
    written = await asyncio.to_thread(
        replace_kline_features,
        dataset_id,
        enriched,
        feature_version=KLINE_FEATURE_VERSION,
        feature_config_hash=feature_config_hash,
        ma_periods=ma_periods,
        batch_size=WRITE_BATCH_SIZE,
    )
    logger.info(
        "K-line features refreshed: symbol=%s timeframe=%s rows=%s trend=%s",
        symbol,
        timeframe,
        written,
        timeframe in TREND_FEATURE_TIMEFRAMES,
    )
    return written


async def refresh_dependent_trend_features(
    symbol: str,
    provider: str,
    changed_from: datetime,
) -> None:
    datasets = await asyncio.to_thread(list_kline_datasets)
    dependents = [
        dataset for dataset in datasets
        if dataset["provider"] == provider
        and dataset["symbol"] == symbol
        and dataset["timeframe"] in TREND_FEATURE_TIMEFRAMES
        and int(dataset.get("row_count") or 0) > 0
    ]
    for dataset in dependents:
        if not dataset.get("features_ready"):
            await refresh_kline_dataset_features(dataset)
            continue
        affected = await asyncio.to_thread(
            read_kline_dataset_window,
            str(dataset["id"]),
            changed_from,
            0,
        )
        if affected.empty:
            continue
        support_limit = max(240, min(int(dataset.get("row_count") or len(affected)), 600))
        hourly, daily = await asyncio.gather(
            load_kline_for_backtest(symbol, "1h", support_limit),
            load_kline_for_backtest(symbol, "1d", support_limit),
        )
        scores = await asyncio.to_thread(
            calculate_combined_trend_score_series,
            hourly,
            daily,
            affected["datetime"].tolist(),
        )
        await asyncio.to_thread(update_kline_trend_features, str(dataset["id"]), scores)


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
        features_ready_before = bool(dataset.get("features_ready"))
        upsert_result = await asyncio.to_thread(
            upsert_kline_frame_with_range,
            dataset_id,
            frame,
            WRITE_BATCH_SIZE,
        )
        written_count = upsert_result.changed_count
        await asyncio.to_thread(
            trim_kline_dataset,
            dataset_id,
            int(dataset["target_count"]),
            data_changed=upsert_result.changed_count > 0,
        )
        dataset = await asyncio.to_thread(get_kline_dataset, dataset_id)
        if upsert_result.earliest_changed_at is not None:
            await refresh_kline_dataset_features(
                dataset,
                changed_from=upsert_result.earliest_changed_at,
                allow_incremental=features_ready_before,
            )
            if str(dataset["timeframe"]) in {"1h", "1d"}:
                await refresh_dependent_trend_features(
                    str(dataset["symbol"]),
                    str(dataset["provider"]),
                    upsert_result.earliest_changed_at,
                )
        elif not dataset.get("features_ready") and int(dataset.get("row_count") or 0) > 0:
            await refresh_kline_dataset_features(dataset)
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
