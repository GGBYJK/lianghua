from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .analysis_cache_store import load_analysis_cache, save_analysis_cache
from .config import load_head_shoulder_config
from .kline_service import current_market_provider, load_kline_for_backtest
from .kline_store import find_kline_dataset
from .schemas import ScanResponse
from .strategy import (
    HeadShoulderTopSignal,
    add_macd_columns,
    add_ma_columns,
    find_pivots,
    prepare_chart_payload,
    scan_head_shoulders,
)


logger = logging.getLogger("app.scan_analysis")
ANALYSIS_ALGORITHM_VERSION = "scan-v2-ma-context-20260722"
ANALYSIS_CACHE_BUCKET_SECONDS = max(30, int(os.getenv("ANALYSIS_CACHE_BUCKET_SECONDS", "300")))
ANALYSIS_MAX_CONCURRENCY = max(1, int(os.getenv("ANALYSIS_MAX_CONCURRENCY", "1")))
_analysis_gate = asyncio.Semaphore(ANALYSIS_MAX_CONCURRENCY)


def _signal_pattern_score(signal: HeadShoulderTopSignal) -> int:
    return int(signal.pattern_score) if signal.pattern_score is not None else -1


def _signal_head_key(signal: HeadShoulderTopSignal) -> tuple[str, str, str, str, str]:
    return (
        signal.symbol,
        signal.timeframe,
        signal.pattern,
        signal.alert_type,
        f"{float(signal.head.price):.8f}",
    )


def _is_pullback(alert_type: str | None) -> bool:
    return alert_type in {"head_shoulders_top_pullback", "inverse_head_shoulders_pullback"}


def _signal_time_priority(signal: HeadShoulderTopSignal) -> tuple[Any, int]:
    return (
        signal.retest_time or signal.break_time or signal.right_shoulder.time,
        signal.right_shoulder.index,
    )


def filter_scan_signals(signals: list[HeadShoulderTopSignal]) -> list[HeadShoulderTopSignal]:
    best_score_by_head: dict[tuple[str, str, str, str, str], int] = {}
    earliest_pullback_by_head: dict[tuple[str, str, str, str, str], HeadShoulderTopSignal] = {}
    for signal in signals:
        if not _is_pullback(signal.alert_type):
            continue
        key = _signal_head_key(signal)
        current = earliest_pullback_by_head.get(key)
        if current is None or _signal_time_priority(signal) < _signal_time_priority(current):
            earliest_pullback_by_head[key] = signal

    seen_pullback_heads: set[tuple[str, str, str, str, str]] = set()
    filtered: list[HeadShoulderTopSignal] = []
    for signal in signals:
        key = _signal_head_key(signal)
        if _is_pullback(signal.alert_type):
            if key in seen_pullback_heads or earliest_pullback_by_head.get(key) is not signal:
                continue
            seen_pullback_heads.add(key)
            filtered.append(signal)
            continue
        score = _signal_pattern_score(signal)
        best_score = best_score_by_head.get(key)
        if best_score is not None and score <= best_score:
            continue
        best_score_by_head[key] = score
        filtered.append(signal)
    return filtered


def build_scan_response(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    overrides: dict[str, Any] | None,
    hourly_df: pd.DataFrame | None = None,
    daily_df: pd.DataFrame | None = None,
) -> ScanResponse:
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    if hourly_df is not None:
        hourly_df = hourly_df.copy()
        hourly_df["datetime"] = pd.to_datetime(hourly_df["datetime"])
    if daily_df is not None:
        daily_df = daily_df.copy()
        daily_df["datetime"] = pd.to_datetime(daily_df["datetime"])
    config = load_head_shoulder_config(symbol=symbol, timeframe=timeframe, overrides=overrides)
    signals = filter_scan_signals(scan_head_shoulders(
        df,
        symbol=symbol,
        timeframe=timeframe,
        config=config,
        hourly_df=hourly_df,
        daily_df=daily_df,
    ))
    enriched_df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_pivots(enriched_df, left=config.pivot_left, right=config.pivot_right)
    chart = prepare_chart_payload(enriched_df, pivots, signals, config, timeframe=timeframe)
    return ScanResponse(
        symbol=symbol,
        timeframe=timeframe,
        rows=len(df),
        start_time=df["datetime"].iloc[0].isoformat() if len(df) else None,
        end_time=df["datetime"].iloc[-1].isoformat() if len(df) else None,
        config=config.to_dict(),
        signals=[signal.to_dict() for signal in signals],
        chart=chart,
    )


def _canonical_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dataset_signature(
    symbol: str,
    timeframe: str,
    requested_count: int,
    provider: str,
    bucket: int,
) -> tuple[str, str]:
    dataset = find_kline_dataset(symbol, timeframe, provider)
    if dataset is not None and int(dataset.get("row_count") or 0) >= requested_count:
        end_time = dataset.get("end_time")
        end_value = end_time.isoformat() if end_time is not None else "none"
        return (
            f"db:{dataset['id']}:{int(dataset.get('revision') or 0)}:{int(dataset['row_count'])}:{end_value}:{requested_count}",
            "database",
        )
    return f"market:{symbol}:{timeframe}:{requested_count}:{bucket}", "market"


async def _cache_context(
    symbol: str,
    timeframe: str,
    limit: int,
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    config = load_head_shoulder_config(symbol=symbol, timeframe=timeframe, overrides=overrides)
    support_limit = max(80, min(limit, 240))
    return await build_analysis_cache_context(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        support_limit=support_limit,
        config=config.to_dict(),
        algorithm_version=ANALYSIS_ALGORITHM_VERSION,
    )


async def build_analysis_cache_context(
    *,
    symbol: str,
    timeframe: str,
    limit: int,
    support_limit: int,
    config: dict[str, Any],
    algorithm_version: str,
    bucket_seconds: int = ANALYSIS_CACHE_BUCKET_SECONDS,
) -> dict[str, Any]:
    provider = current_market_provider()
    config_hash = _canonical_hash(config)
    bucket = int(datetime.now(timezone.utc).timestamp()) // max(30, bucket_seconds)
    main, hourly, daily = await asyncio.gather(
        asyncio.to_thread(_dataset_signature, symbol, timeframe, limit, provider, bucket),
        asyncio.to_thread(_dataset_signature, symbol, "1h", support_limit, provider, bucket),
        asyncio.to_thread(_dataset_signature, symbol, "1d", support_limit, provider, bucket),
    )
    key_payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "limit": limit,
        "provider": provider,
        "config_hash": config_hash,
        "algorithm_version": algorithm_version,
        "main": main[0],
        "hourly": hourly[0],
        "daily": daily[0],
    }
    return {
        **key_payload,
        "cache_key": _canonical_hash(key_payload),
        "data_sources": {"main": main[1], "hourly": hourly[1], "daily": daily[1]},
    }


async def scan_market_cached(
    symbol: str,
    timeframe: str,
    limit: int,
    overrides: dict[str, Any] | None = None,
) -> ScanResponse:
    context = await _cache_context(symbol, timeframe, limit, overrides)
    cached = await asyncio.to_thread(load_analysis_cache, context["cache_key"])
    if cached is not None:
        cached["cache_hit"] = True
        cached["analysis_ms"] = 0
        cached["data_sources"] = context["data_sources"]
        return ScanResponse.model_validate(cached)

    async with _analysis_gate:
        # A queued request may have been calculated while it waited for the gate.
        context = await _cache_context(symbol, timeframe, limit, overrides)
        cached = await asyncio.to_thread(load_analysis_cache, context["cache_key"])
        if cached is not None:
            cached["cache_hit"] = True
            cached["analysis_ms"] = 0
            cached["data_sources"] = context["data_sources"]
            return ScanResponse.model_validate(cached)

        started = time.perf_counter()
        support_limit = max(80, min(limit, 240))
        df, hourly_df, daily_df = await asyncio.gather(
            load_kline_for_backtest(symbol, timeframe, limit),
            load_kline_for_backtest(symbol, "1h", support_limit),
            load_kline_for_backtest(symbol, "1d", support_limit),
        )
        # A cache miss can write through into a maintained dataset and bump its revision.
        context = await _cache_context(symbol, timeframe, limit, overrides)
        response = await asyncio.to_thread(
            build_scan_response,
            df,
            symbol,
            timeframe,
            overrides,
            hourly_df,
            daily_df,
        )
        calculation_ms = max(1, int((time.perf_counter() - started) * 1000))
        response.analysis_ms = calculation_ms
        response.data_sources = context["data_sources"]
        await asyncio.to_thread(
            save_analysis_cache,
            cache_key=context["cache_key"],
            symbol=symbol,
            timeframe=timeframe,
            requested_count=limit,
            provider=context["provider"],
            config_hash=context["config_hash"],
            algorithm_version=context["algorithm_version"],
            main_signature=context["main"],
            hourly_signature=context["hourly"],
            daily_signature=context["daily"],
            calculation_ms=calculation_ms,
            payload=response.model_dump(mode="json"),
        )
        logger.info(
            "market analysis cached: symbol=%s timeframe=%s limit=%s elapsed_ms=%s",
            symbol,
            timeframe,
            limit,
            calculation_ms,
        )
        return response
