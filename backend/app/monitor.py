from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd
import httpx

from .alert_keys import build_signal_unique_key
from .config import load_head_shoulder_config
from .market_client import MarketApiError, fetch_kline_from_market
from .strategy import add_macd_columns, add_ma_columns, find_pivots, prepare_chart_payload, scan_head_shoulders
from .watch_pool_store import (
    WatchPoolStoreError,
    insert_head_shoulders_alert_if_new,
    list_enabled_watch_pool_items,
)


logger = logging.getLogger("app.monitor")
WATCH_POOL_TIMEZONE = ZoneInfo(os.getenv("WATCH_POOL_TIMEZONE", "Asia/Shanghai"))
WECHAT_WORKBOT_WEBHOOK_URL = os.getenv("WECHAT_WORKBOT_WEBHOOK_URL", "").strip()
WECHAT_WORKBOT_MENTIONED_LIST = [
    item.strip()
    for item in os.getenv("WECHAT_WORKBOT_MENTIONED_LIST", "").split(",")
    if item.strip()
]
WECHAT_WORKBOT_TIMEOUT_SECONDS = float(os.getenv("WECHAT_WORKBOT_TIMEOUT_SECONDS", "8"))
WATCH_POOL_TRADING_SESSIONS: dict[str, tuple[tuple[time, time], ...]] = {
    "day": (
        (time(9, 0), time(11, 30)),
        (time(13, 30), time(15, 0)),
    ),
    "night": (
        (time(21, 0), time(23, 0)),
    ),
}
def selected_trading_windows(trading_sessions: str | None = None) -> tuple[tuple[time, time], ...]:
    keys = [part.strip() for part in (trading_sessions or "day,night").split(",") if part.strip()]
    windows: list[tuple[time, time]] = []
    for key in keys:
        windows.extend(WATCH_POOL_TRADING_SESSIONS.get(key, ()))
    return tuple(windows) or tuple(window for group in WATCH_POOL_TRADING_SESSIONS.values() for window in group)


def current_trading_window(
    now: datetime | None = None,
    trading_sessions: str | None = None,
) -> tuple[datetime, datetime] | None:
    current = (now or datetime.now(WATCH_POOL_TIMEZONE)).astimezone(WATCH_POOL_TIMEZONE)
    for start, end in selected_trading_windows(trading_sessions):
        if start <= end:
            start_at = datetime.combine(current.date(), start, tzinfo=WATCH_POOL_TIMEZONE)
            end_at = datetime.combine(current.date(), end, tzinfo=WATCH_POOL_TIMEZONE)
        elif current.time() >= start:
            start_at = datetime.combine(current.date(), start, tzinfo=WATCH_POOL_TIMEZONE)
            end_at = datetime.combine(current.date(), end, tzinfo=WATCH_POOL_TIMEZONE) + timedelta(days=1)
        else:
            start_at = datetime.combine(current.date(), start, tzinfo=WATCH_POOL_TIMEZONE) - timedelta(days=1)
            end_at = datetime.combine(current.date(), end, tzinfo=WATCH_POOL_TIMEZONE)
        if start_at <= current <= end_at:
            return start_at.astimezone(ZoneInfo("UTC")), end_at.astimezone(ZoneInfo("UTC"))
    return None


def is_in_trading_session(now: datetime | None = None, trading_sessions: str | None = None) -> bool:
    return current_trading_window(now=now, trading_sessions=trading_sessions) is not None


def _zh(value: str) -> str:
    if "\\u" not in value:
        return value
    return value.encode("ascii").decode("unicode_escape")


ZH = {
    "inverse_pattern": "\u53cd\u5411\u5934\u80a9\u5f62\u6001",
    "top_pattern": "\u5934\u80a9\u9876",
    "inverse_pattern_short": "\u53cd\u5411\u5934\u80a9",
    "right_shoulder_confirmed": "\u53f3\u80a9\u786e\u8ba4",
    "neckline_break": "\u8dcc\u7834\u9888\u7ebf\u786e\u8ba4",
    "shape_alert": "\u5f62\u6001\u63d0\u9192",
    "new_alert": "\u76d1\u63a7\u5230\u65b0\u7684\u5f62\u6001\u63d0\u9192",
    "new_pattern": "\u65b0\u5f62\u6001",
    "symbol": "\u54c1\u79cd",
    "timeframe": "\u5468\u671f",
    "pattern": "\u5f62\u6001",
    "alert": "\u63d0\u9192",
    "score": "\u8bc4\u5206",
    "right_shoulder_price": "\u53f3\u80a9\u4ef7",
    "right_shoulder_time": "\u53f3\u80a9\u65f6\u95f4",
    "trigger_time": "\u89e6\u53d1\u65f6\u95f4",
    "trigger_price": "\u89e6\u53d1\u4ef7",
    "break_price": "\u8dcc\u7834\u4ef7",
    "neckline_price": "\u9888\u7ebf\u4ef7",
    "message": "\u8bf4\u660e",
    "colon": "\uff1a",
    "left_paren": "\uff08",
    "right_paren": "\uff09",
}
ZH = {key: _zh(value) for key, value in ZH.items()}


def pattern_label(pattern: str | None) -> str:
    if pattern == "inverse_head_shoulders":
        return ZH["inverse_pattern"]
    return ZH["top_pattern"]


def compact_pattern_label(pattern: str | None) -> str:
    if pattern == "inverse_head_shoulders":
        return ZH["inverse_pattern_short"]
    return ZH["top_pattern"]


def alert_type_label(alert_type: str | None) -> str:
    if alert_type == "right_shoulder_confirmed":
        return ZH["right_shoulder_confirmed"]
    if alert_type == "neckline_break":
        return ZH["neckline_break"]
    return alert_type or ZH["shape_alert"]


def format_signal_time(value: str | None) -> str:
    parsed = parse_signal_time(value)
    if parsed is None:
        return "-"
    return parsed.astimezone(WATCH_POOL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def format_compact_signal_time(value: str | None) -> str:
    parsed = parse_signal_time(value)
    if parsed is None:
        return "-"
    return parsed.astimezone(WATCH_POOL_TIMEZONE).strftime("%Y%m%d %H:%M")


def signal_notification_time(signal: dict[str, Any]) -> str | None:
    return (
        signal.get("retest_time")
        or signal.get("break_time")
        or signal.get("right_shoulder", {}).get("time")
    )


def build_wechat_workbot_content(signal: dict[str, Any], item: dict[str, Any]) -> str:
    colon = ZH["colon"]
    comma = "\uff0c"
    return (
        f"{ZH['new_pattern']}{colon}"
        f"{signal.get('symbol')}{comma}"
        f"{signal.get('timeframe')}{comma}"
        f"{compact_pattern_label(signal.get('pattern'))}{comma}"
        f"{format_compact_signal_time(signal_notification_time(signal))}"
    )


async def send_wechat_workbot_notification(signal: dict[str, Any], item: dict[str, Any]) -> None:
    if not WECHAT_WORKBOT_WEBHOOK_URL:
        logger.warning(
            "wechat workbot webhook is empty, skip notification: symbol=%s timeframe=%s alert_type=%s",
            signal.get("symbol"),
            signal.get("timeframe"),
            signal.get("alert_type"),
        )
        return
    payload: dict[str, Any] = {
        "msgtype": "text",
        "text": {
            "content": build_wechat_workbot_content(signal, item),
        },
    }
    if WECHAT_WORKBOT_MENTIONED_LIST:
        payload["text"]["mentioned_list"] = WECHAT_WORKBOT_MENTIONED_LIST

    try:
        logger.info(
            "sending wechat workbot notification: symbol=%s timeframe=%s alert_type=%s",
            signal.get("symbol"),
            signal.get("timeframe"),
            signal.get("alert_type"),
        )
        async with httpx.AsyncClient(timeout=WECHAT_WORKBOT_TIMEOUT_SECONDS) as client:
            response = await client.post(WECHAT_WORKBOT_WEBHOOK_URL, json=payload)
            response.raise_for_status()
            try:
                body = response.json()
            except Exception:
                body = {"text": response.text}
            if body.get("errcode") not in (0, None):
                logger.warning("wechat workbot notification failed: %s", body)
            else:
                logger.info("wechat workbot notification sent successfully: %s", body)
    except Exception as exc:
        logger.warning("wechat workbot notification request failed: %s", exc)


def parse_signal_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WATCH_POOL_TIMEZONE)
    return parsed.astimezone(ZoneInfo("UTC"))


def parse_monitor_started_at(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo("UTC"))


def signal_latest_detection_time(signal: dict[str, Any]) -> datetime | None:
    values = [
        signal.get("left_shoulder", {}).get("time"),
        signal.get("left_neck", {}).get("time"),
        signal.get("head", {}).get("time"),
        signal.get("right_neck", {}).get("time"),
        signal.get("right_shoulder", {}).get("time"),
        signal.get("break_time"),
        signal.get("retest_time"),
    ]
    times = [parsed for value in values if (parsed := parse_signal_time(value))]
    return max(times) if times else None


def signal_emit_time(signal: dict[str, Any]) -> datetime | None:
    return parse_signal_time(signal_notification_time(signal)) or signal_latest_detection_time(signal)


def is_signal_from_current_watch_day(signal: dict[str, Any], now: datetime | None = None) -> bool:
    emit_time = signal_emit_time(signal)
    if emit_time is None:
        return True
    current = now or datetime.now(WATCH_POOL_TIMEZONE)
    signal_date = emit_time.astimezone(WATCH_POOL_TIMEZONE).date()
    current_date = current.astimezone(WATCH_POOL_TIMEZONE).date()
    return signal_date == current_date


def is_signal_from_current_trading_window(
    signal: dict[str, Any],
    item: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    emit_time = signal_emit_time(signal)
    if emit_time is None:
        return True
    window = current_trading_window(now=now, trading_sessions=item.get("trading_sessions"))
    if window is None:
        return False
    start_at, end_at = window
    return start_at <= emit_time <= end_at


def should_emit_signal_for_item(signal: dict[str, Any], item: dict[str, Any], now: datetime | None = None) -> bool:
    if not is_signal_from_current_watch_day(signal, now=now):
        return False
    if not is_signal_from_current_trading_window(signal, item, now=now):
        return False
    monitor_started_at = parse_monitor_started_at(item.get("monitor_started_at"))
    if monitor_started_at is None:
        return True
    emit_time = signal_emit_time(signal)
    if emit_time is None:
        return True
    return emit_time > monitor_started_at


def scan_dataframe_payload(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config_overrides: dict[str, Any] | None = None,
    hourly_df: pd.DataFrame | None = None,
    daily_df: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    if hourly_df is not None:
        hourly_df = hourly_df.copy().reset_index(drop=True)
        hourly_df["datetime"] = pd.to_datetime(hourly_df["datetime"])
    if daily_df is not None:
        daily_df = daily_df.copy().reset_index(drop=True)
        daily_df["datetime"] = pd.to_datetime(daily_df["datetime"])
    config = load_head_shoulder_config(symbol=symbol, timeframe=timeframe, overrides=config_overrides)
    signals = scan_head_shoulders(df, symbol=symbol, timeframe=timeframe, config=config, hourly_df=hourly_df, daily_df=daily_df)
    enriched_df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_pivots(enriched_df, left=config.pivot_left, right=config.pivot_right)
    chart = prepare_chart_payload(enriched_df, pivots, signals, config)
    return [signal.to_dict() for signal in signals], chart


def build_watch_pool_config_overrides(item: dict[str, Any]) -> dict[str, Any] | None:
    overrides: dict[str, Any] = {}
    if float(item.get("min_head_to_neck_height", 0)) > 0:
        overrides["min_head_to_neck_height"] = float(item["min_head_to_neck_height"])
    if float(item.get("min_shoulder_to_neck_height", 0)) > 0:
        overrides["min_shoulder_to_neck_height"] = float(item["min_shoulder_to_neck_height"])
    return overrides or None


async def scan_watch_pool_once(limit: int = 420) -> int:
    inserted = 0
    items = list_enabled_watch_pool_items()
    logger.info("manual watch pool scan started: enabled_items=%s limit=%s", len(items), limit)
    scanned = 0
    for item in items:
        try:
            scanned += 1
            df, hourly_df, daily_df = await asyncio.gather(
                fetch_kline_from_market(symbol=item["symbol"], period=item["timeframe"], limit=limit),
                fetch_kline_from_market(symbol=item["symbol"], period="1h", limit=max(80, min(limit, 240))),
                fetch_kline_from_market(symbol=item["symbol"], period="1d", limit=max(80, min(limit, 240))),
            )
            signals, chart = scan_dataframe_payload(
                df,
                symbol=item["symbol"],
                timeframe=item["timeframe"],
                config_overrides=build_watch_pool_config_overrides(item),
                hourly_df=hourly_df,
                daily_df=daily_df,
            )
            for signal in signals:
                if not should_emit_signal_for_item(signal, item):
                    logger.info(
                        "signal skipped before monitor start: watch_pool_id=%s symbol=%s timeframe=%s alert_type=%s",
                        item["id"],
                        signal.get("symbol"),
                        signal.get("timeframe"),
                        signal.get("alert_type"),
                    )
                    continue
                alert = {
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
                }
                if insert_head_shoulders_alert_if_new(alert):
                    inserted += 1
                    await send_wechat_workbot_notification(signal, item)
                else:
                    logger.info(
                        "duplicate alert skipped for notification: watch_pool_id=%s unique_key=%s",
                        item["id"],
                        alert["unique_key"],
                    )
        except (MarketApiError, WatchPoolStoreError, Exception) as exc:
            logger.warning("watch pool scan failed: item=%s error=%s", item, exc)
    logger.info(
        "manual watch pool scan finished: enabled_items=%s scanned=%s inserted_alerts=%s",
        len(items),
        scanned,
        inserted,
    )
    return inserted


async def monitor_watch_pool_loop(stop_event: asyncio.Event) -> None:
    last_scan_by_item: dict[str, float] = {}
    poll_seconds = max(5, int(os.getenv("WATCH_POOL_POLL_SECONDS", "10")))
    kline_limit = max(30, int(os.getenv("WATCH_POOL_KLINE_LIMIT", "420")))
    loop = asyncio.get_running_loop()
    logger.info("watch pool monitor started: poll_seconds=%s kline_limit=%s timezone=%s", poll_seconds, kline_limit, WATCH_POOL_TIMEZONE)

    while not stop_event.is_set():
        enabled_count = 0
        scanned_count = 0
        due_count = 0
        inserted_count = 0
        skipped_outside_session = 0
        skipped_interval = 0
        try:
            now = loop.time()
            items = list_enabled_watch_pool_items()
            enabled_count = len(items)
            for item in items:
                if not is_in_trading_session(trading_sessions=item.get("trading_sessions")):
                    skipped_outside_session += 1
                    continue
                interval = max(1, int(item["monitor_minutes"])) * 60
                last_scan = last_scan_by_item.get(item["id"], 0)
                if now - last_scan < interval:
                    skipped_interval += 1
                    continue
                due_count += 1
                last_scan_by_item[item["id"]] = now
                try:
                    scanned_count += 1
                    logger.info(
                        "watch pool item scan started: id=%s name=%s symbol=%s timeframe=%s interval_seconds=%s",
                        item["id"],
                        item.get("name"),
                        item["symbol"],
                        item["timeframe"],
                        interval,
                    )
                    df, hourly_df, daily_df = await asyncio.gather(
                        fetch_kline_from_market(symbol=item["symbol"], period=item["timeframe"], limit=kline_limit),
                        fetch_kline_from_market(symbol=item["symbol"], period="1h", limit=max(80, min(kline_limit, 240))),
                        fetch_kline_from_market(symbol=item["symbol"], period="1d", limit=max(80, min(kline_limit, 240))),
                    )
                    signals, chart = scan_dataframe_payload(
                        df,
                        symbol=item["symbol"],
                        timeframe=item["timeframe"],
                        config_overrides=build_watch_pool_config_overrides(item),
                        hourly_df=hourly_df,
                        daily_df=daily_df,
                    )
                    inserted_for_item = 0
                    for signal in signals:
                        if not should_emit_signal_for_item(signal, item):
                            logger.info(
                                "signal skipped before monitor start: watch_pool_id=%s symbol=%s timeframe=%s alert_type=%s",
                                item["id"],
                                signal.get("symbol"),
                                signal.get("timeframe"),
                                signal.get("alert_type"),
                            )
                            continue
                        alert = {
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
                        }
                        if insert_head_shoulders_alert_if_new(alert):
                            inserted_for_item += 1
                            inserted_count += 1
                            await send_wechat_workbot_notification(signal, item)
                        else:
                            logger.info(
                                "duplicate alert skipped for notification: watch_pool_id=%s unique_key=%s",
                                item["id"],
                                alert["unique_key"],
                            )
                    logger.info(
                        "watch pool item scan finished: id=%s symbol=%s timeframe=%s rows=%s signals=%s inserted_alerts=%s",
                        item["id"],
                        item["symbol"],
                        item["timeframe"],
                        len(df),
                        len(signals),
                        inserted_for_item,
                    )
                except (MarketApiError, WatchPoolStoreError, Exception) as exc:
                    logger.warning("watch pool item scan failed: item=%s error=%s", item, exc)
        except (WatchPoolStoreError, Exception) as exc:
            logger.warning("watch pool loop failed: %s", exc)
        logger.info(
            "watch pool monitor heartbeat: enabled_items=%s due_items=%s scanned_items=%s skipped_interval=%s skipped_outside_session=%s inserted_alerts=%s",
            enabled_count,
            due_count,
            scanned_count,
            skipped_interval,
            skipped_outside_session,
            inserted_count,
        )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info("watch pool monitor stopped")
