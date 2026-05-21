from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd
import httpx

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
DEFAULT_WATCH_POOL_ITEMS = (
    {"name": "热卷2610 1分钟", "symbol": "hc2610", "timeframe": "1m", "enabled": True, "monitor_minutes": 3, "trading_sessions": "day,night"},
    {"name": "热卷2610 5分钟", "symbol": "hc2610", "timeframe": "5m", "enabled": True, "monitor_minutes": 3, "trading_sessions": "day,night"},
)


def selected_trading_windows(trading_sessions: str | None = None) -> tuple[tuple[time, time], ...]:
    keys = [part.strip() for part in (trading_sessions or "day,night").split(",") if part.strip()]
    windows: list[tuple[time, time]] = []
    for key in keys:
        windows.extend(WATCH_POOL_TRADING_SESSIONS.get(key, ()))
    return tuple(windows) or tuple(window for group in WATCH_POOL_TRADING_SESSIONS.values() for window in group)


def is_in_trading_session(now: datetime | None = None, trading_sessions: str | None = None) -> bool:
    current = now or datetime.now(WATCH_POOL_TIMEZONE)
    current_time = current.astimezone(WATCH_POOL_TIMEZONE).time()
    return any(start <= current_time <= end for start, end in selected_trading_windows(trading_sessions))


def ensure_default_watch_pool_items() -> None:
    for item in DEFAULT_WATCH_POOL_ITEMS:
        ensure_watch_pool_item(item)


def build_signal_unique_key(signal: dict[str, Any]) -> str:
    alert_type = signal.get("alert_type", "neckline_break")
    if alert_type == "right_shoulder_retest":
        trigger_time = "first_right_shoulder_retest"
    else:
        trigger_time = signal.get("break_time") or signal["right_shoulder"]["time"]
    parts = [
        signal["symbol"],
        signal["timeframe"],
        signal["pattern"],
        alert_type,
        signal["left_shoulder"]["time"],
        signal["head"]["time"],
        signal["right_shoulder"]["time"],
        trigger_time,
    ]
    return "|".join(str(part) for part in parts)


def _zh(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


ZH = {
    "inverse_pattern": "\u53cd\u5411\u5934\u80a9\u5f62\u6001",
    "top_pattern": "\u5934\u80a9\u9876",
    "right_shoulder_confirmed": "\u53f3\u80a9\u786e\u8ba4",
    "right_shoulder_retest": "\u53f3\u80a9\u786e\u8ba4\u540e\u91cd\u65b0\u89e6\u53ca/\u8d85\u8fc7\u53f3\u80a9\u4ef7",
    "neckline_break": "\u8dcc\u7834\u9888\u7ebf\u786e\u8ba4",
    "shape_alert": "\u5f62\u6001\u63d0\u9192",
    "new_alert": "\u76d1\u63a7\u5230\u65b0\u7684\u5f62\u6001\u63d0\u9192",
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
}
ZH = {key: _zh(value) for key, value in ZH.items()}


def pattern_label(pattern: str | None) -> str:
    if pattern == "inverse_head_shoulders":
        return ZH["inverse_pattern"]
    return ZH["top_pattern"]


def alert_type_label(alert_type: str | None) -> str:
    if alert_type == "right_shoulder_confirmed":
        return ZH["right_shoulder_confirmed"]
    if alert_type == "right_shoulder_retest":
        return ZH["right_shoulder_retest"]
    if alert_type == "neckline_break":
        return ZH["neckline_break"]
    return alert_type or ZH["shape_alert"]


def format_signal_time(value: str | None) -> str:
    parsed = parse_signal_time(value)
    if parsed is None:
        return "-"
    return parsed.astimezone(WATCH_POOL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def build_wechat_workbot_content(signal: dict[str, Any], item: dict[str, Any]) -> str:
    right_shoulder = signal.get("right_shoulder", {})
    lines = [
        ZH["new_alert"],
        f"{ZH['symbol']}?{item.get('name') or signal.get('symbol')}?{signal.get('symbol')}?",
        f"{ZH['timeframe']}?{signal.get('timeframe')}",
        f"{ZH['pattern']}?{pattern_label(signal.get('pattern'))}",
        f"{ZH['alert']}?{alert_type_label(signal.get('alert_type'))}",
        f"{ZH['score']}?{signal.get('score', '-')}",
        f"{ZH['right_shoulder_price']}?{float(right_shoulder.get('price', 0)):.2f}" if right_shoulder.get("price") is not None else f"{ZH['right_shoulder_price']}?-",
        f"{ZH['right_shoulder_time']}?{format_signal_time(right_shoulder.get('time'))}",
    ]
    if signal.get("retest_time"):
        lines.extend([
            f"{ZH['trigger_time']}?{format_signal_time(signal.get('retest_time'))}",
            f"{ZH['trigger_price']}?{float(signal['retest_price']):.2f}" if signal.get("retest_price") is not None else f"{ZH['trigger_price']}?-",
        ])
    elif signal.get("break_time"):
        lines.extend([
            f"{ZH['trigger_time']}?{format_signal_time(signal.get('break_time'))}",
            f"{ZH['break_price']}?{float(signal['break_price']):.2f}" if signal.get("break_price") is not None else f"{ZH['break_price']}?-",
        ])
    if signal.get("neckline_price") is not None:
        lines.append(f"{ZH['neckline_price']}?{float(signal['neckline_price']):.2f}")
    if signal.get("message"):
        lines.append(f"{ZH['message']}?{signal['message']}")
    return "\n".join(lines)


async def send_wechat_workbot_notification(signal: dict[str, Any], item: dict[str, Any]) -> None:
    if not WECHAT_WORKBOT_WEBHOOK_URL:
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
        async with httpx.AsyncClient(timeout=WECHAT_WORKBOT_TIMEOUT_SECONDS) as client:
            response = await client.post(WECHAT_WORKBOT_WEBHOOK_URL, json=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("errcode") not in (0, None):
                logger.warning("wechat workbot notification failed: %s", body)
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


def should_emit_signal_for_item(signal: dict[str, Any], item: dict[str, Any]) -> bool:
    monitor_started_at = parse_monitor_started_at(item.get("monitor_started_at"))
    if monitor_started_at is None:
        return True
    latest_detection_time = signal_latest_detection_time(signal)
    if latest_detection_time is None:
        return True
    return latest_detection_time > monitor_started_at


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
            config_overrides = {}
            if float(item.get("min_head_to_neck_height", 0)) > 0:
                config_overrides["min_head_to_neck_height"] = float(item["min_head_to_neck_height"])
            signals, chart = scan_dataframe_payload(df, symbol=item["symbol"], timeframe=item["timeframe"], config_overrides=config_overrides or None)
            for signal in signals:
                if not should_emit_signal_for_item(signal, item):
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
            now = loop.time()
            for item in list_enabled_watch_pool_items():
                if not is_in_trading_session(trading_sessions=item.get("trading_sessions")):
                    continue
                interval = max(1, int(item["monitor_minutes"])) * 60
                last_scan = last_scan_by_item.get(item["id"], 0)
                if now - last_scan < interval:
                    continue
                last_scan_by_item[item["id"]] = now
                try:
                    df = await fetch_kline_from_market(symbol=item["symbol"], period=item["timeframe"], limit=kline_limit)
                    config_overrides = {}
                    if float(item.get("min_head_to_neck_height", 0)) > 0:
                        config_overrides["min_head_to_neck_height"] = float(item["min_head_to_neck_height"])
                    signals, chart = scan_dataframe_payload(df, symbol=item["symbol"], timeframe=item["timeframe"], config_overrides=config_overrides or None)
                    for signal in signals:
                        if not should_emit_signal_for_item(signal, item):
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
                            await send_wechat_workbot_notification(signal, item)
                except (MarketApiError, WatchPoolStoreError, Exception) as exc:
                    logger.warning("watch pool item scan failed: item=%s error=%s", item, exc)
        except (WatchPoolStoreError, Exception) as exc:
            logger.warning("watch pool loop failed: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
