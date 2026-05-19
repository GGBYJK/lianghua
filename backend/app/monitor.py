from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd

from .config import load_head_shoulder_config
from .market_client import MarketApiError, fetch_kline_from_market
from .strategy import add_macd_columns, add_ma_columns, find_pivots, prepare_chart_payload, scan_head_shoulders
from .watch_pool_store import (
    WatchPoolStoreError,
    ensure_watch_pool_item,
    insert_head_shoulders_alert_if_new,
    list_enabled_watch_pool_items,
)


logger = logging.getLogger("app.monitor")
WATCH_POOL_TIMEZONE = ZoneInfo(os.getenv("WATCH_POOL_TIMEZONE", "Asia/Shanghai"))
WATCH_POOL_TRADING_SESSIONS = (
    (time(9, 0), time(11, 30)),
    (time(13, 30), time(15, 0)),
    (time(21, 0), time(23, 0)),
)
DEFAULT_WATCH_POOL_ITEMS = (
    {"name": "热卷2610 1分钟", "symbol": "hc2610", "timeframe": "1m", "enabled": True, "monitor_minutes": 3},
    {"name": "热卷2610 5分钟", "symbol": "hc2610", "timeframe": "5m", "enabled": True, "monitor_minutes": 3},
)


def is_in_trading_session(now: datetime | None = None) -> bool:
    current = now or datetime.now(WATCH_POOL_TIMEZONE)
    current_time = current.astimezone(WATCH_POOL_TIMEZONE).time()
    return any(start <= current_time <= end for start, end in WATCH_POOL_TRADING_SESSIONS)


def ensure_default_watch_pool_items() -> None:
    for item in DEFAULT_WATCH_POOL_ITEMS:
        ensure_watch_pool_item(item)


def build_signal_unique_key(signal: dict[str, Any]) -> str:
    trigger_time = signal.get("break_time") or signal.get("retest_time") or signal["right_shoulder"]["time"]
    parts = [
        signal["symbol"],
        signal["timeframe"],
        signal["pattern"],
        signal.get("alert_type", "neckline_break"),
        signal["left_shoulder"]["time"],
        signal["head"]["time"],
        signal["right_shoulder"]["time"],
        trigger_time,
    ]
    return "|".join(str(part) for part in parts)


def scan_dataframe_payload(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config_overrides: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    config = load_head_shoulder_config(symbol=symbol, timeframe=timeframe, overrides=config_overrides)
    signals = scan_head_shoulders(df, symbol=symbol, timeframe=timeframe, config=config)
    enriched_df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_pivots(enriched_df, left=config.pivot_left, right=config.pivot_right)
    chart = prepare_chart_payload(enriched_df, pivots, signals, config)
    return [signal.to_dict() for signal in signals], chart


async def scan_watch_pool_once(limit: int = 420) -> int:
    inserted = 0
    for item in list_enabled_watch_pool_items():
        try:
            df = await fetch_kline_from_market(symbol=item["symbol"], period=item["timeframe"], limit=limit)
            signals, chart = scan_dataframe_payload(df, symbol=item["symbol"], timeframe=item["timeframe"])
            for signal in signals:
                if insert_head_shoulders_alert_if_new({
                    "watch_pool_id": item["id"],
                    "symbol": signal["symbol"],
                    "timeframe": signal["timeframe"],
                    "pattern": signal["pattern"],
                    "alert_type": signal.get("alert_type", "neckline_break"),
                    "score": signal["score"],
                    "message": signal["message"],
                    "unique_key": build_signal_unique_key(signal),
                    "signal_payload": signal,
                    "chart_payload": chart,
                }):
                    inserted += 1
        except (MarketApiError, WatchPoolStoreError, Exception) as exc:
            logger.warning("watch pool scan failed: item=%s error=%s", item, exc)
    return inserted


async def monitor_watch_pool_loop(stop_event: asyncio.Event) -> None:
    last_scan_by_item: dict[str, float] = {}
    poll_seconds = max(5, int(os.getenv("WATCH_POOL_POLL_SECONDS", "10")))
    kline_limit = max(30, int(os.getenv("WATCH_POOL_KLINE_LIMIT", "420")))
    loop = asyncio.get_running_loop()

    while not stop_event.is_set():
        try:
            if not is_in_trading_session():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
                except asyncio.TimeoutError:
                    pass
                continue

            now = loop.time()
            for item in list_enabled_watch_pool_items():
                interval = max(1, int(item["monitor_minutes"])) * 60
                last_scan = last_scan_by_item.get(item["id"], 0)
                if now - last_scan < interval:
                    continue
                last_scan_by_item[item["id"]] = now
                try:
                    df = await fetch_kline_from_market(symbol=item["symbol"], period=item["timeframe"], limit=kline_limit)
                    signals, chart = scan_dataframe_payload(df, symbol=item["symbol"], timeframe=item["timeframe"])
                    for signal in signals:
                        insert_head_shoulders_alert_if_new({
                            "watch_pool_id": item["id"],
                            "symbol": signal["symbol"],
                            "timeframe": signal["timeframe"],
                            "pattern": signal["pattern"],
                            "alert_type": signal.get("alert_type", "neckline_break"),
                            "score": signal["score"],
                            "message": signal["message"],
                            "unique_key": build_signal_unique_key(signal),
                            "signal_payload": signal,
                            "chart_payload": chart,
                        })
                except (MarketApiError, WatchPoolStoreError, Exception) as exc:
                    logger.warning("watch pool item scan failed: item=%s error=%s", item, exc)
        except (WatchPoolStoreError, Exception) as exc:
            logger.warning("watch pool loop failed: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
