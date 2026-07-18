from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, desc, func, insert, or_, select, update

from .trading_db import (
    backtest_errors,
    backtest_orders,
    backtest_rule_summaries,
    backtest_runs,
    backtest_series,
    get_engine,
    utc_now,
)


class BacktestStoreError(ValueError):
    pass


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(f"不支持的 JSON 类型: {type(value)!r}")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def default_backtest_name(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return f"策略回测 {current.astimezone(SHANGHAI_TIMEZONE):%Y-%m-%d %H:%M}"


def _run_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["request"] = json.loads(item.pop("request_json"))
    for field in ("created_at", "started_at", "completed_at", "updated_at"):
        item[field] = _utc_iso(item.get(field))
    return item


def _utc_iso(value: Any) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def create_backtest_run(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(uuid4())
    name = str(payload.get("name") or "").strip() or default_backtest_name()
    total = len(payload["symbols"]) * len(payload["timeframes"])
    with get_engine().begin() as connection:
        connection.execute(insert(backtest_runs).values(
            id=run_id,
            user_id=user_id,
            name=name,
            status="QUEUED",
            progress=0,
            request_json=_dump(payload),
            total_combinations=total,
        ))
        row = connection.execute(select(backtest_runs).where(backtest_runs.c.id == run_id)).mappings().one()
    return _run_dict(row)


def list_backtest_runs(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(backtest_runs)
            .where(backtest_runs.c.user_id == user_id)
            .order_by(desc(backtest_runs.c.created_at))
            .limit(limit)
        ).mappings()
        return [_run_dict(row) for row in rows]


def get_backtest_run(user_id: int, run_id: str) -> dict[str, Any]:
    with get_engine().connect() as connection:
        row = connection.execute(select(backtest_runs).where(and_(
            backtest_runs.c.id == run_id, backtest_runs.c.user_id == user_id,
        ))).mappings().first()
        if row is None:
            raise BacktestStoreError("回测记录不存在")
        result = _run_dict(row)
        result["summaries"] = [dict(item) for item in connection.execute(
            select(backtest_rule_summaries)
            .where(backtest_rule_summaries.c.run_id == run_id)
            .order_by(desc(backtest_rule_summaries.c.win_rate), desc(backtest_rule_summaries.c.total_r))
        ).mappings()]
        result["errors"] = [
            {**dict(item), "created_at": _utc_iso(item["created_at"])}
            for item in connection.execute(
                select(backtest_errors).where(backtest_errors.c.run_id == run_id).order_by(backtest_errors.c.id)
            ).mappings()
        ]
        result["markets"] = [dict(item) for item in connection.execute(
            select(
                backtest_series.c.id,
                backtest_series.c.symbol,
                backtest_series.c.timeframe,
                backtest_series.c.row_count,
                backtest_series.c.start_time,
                backtest_series.c.end_time,
            ).where(backtest_series.c.run_id == run_id).order_by(backtest_series.c.symbol, backtest_series.c.timeframe)
        ).mappings()]
        return result


def claim_next_backtest_run() -> dict[str, Any] | None:
    with get_engine().begin() as connection:
        row = connection.execute(
            select(backtest_runs)
            .where(backtest_runs.c.status == "QUEUED")
            .order_by(backtest_runs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).mappings().first()
        if row is None:
            return None
        connection.execute(update(backtest_runs).where(backtest_runs.c.id == row["id"]).values(
            status="RUNNING", started_at=utc_now(), updated_at=utc_now(), progress=1,
        ))
        claimed = dict(row)
        claimed["status"] = "RUNNING"
        claimed["request"] = json.loads(claimed.pop("request_json"))
        return claimed


def update_backtest_progress(run_id: str, completed: int, signals: int, orders: int) -> None:
    with get_engine().begin() as connection:
        total = connection.execute(select(backtest_runs.c.total_combinations).where(backtest_runs.c.id == run_id)).scalar_one()
        progress = 99 if completed >= total else max(1, int(completed * 100 / max(total, 1)))
        connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
            completed_combinations=completed,
            signal_count=signals,
            order_count=orders,
            progress=progress,
            updated_at=utc_now(),
        ))


def finish_backtest_run(run_id: str, status: str, signals: int, orders: int, error_message: str | None = None) -> None:
    with get_engine().begin() as connection:
        connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
            status=status,
            progress=100,
            signal_count=signals,
            order_count=orders,
            error_message=error_message,
            completed_at=utc_now(),
            updated_at=utc_now(),
        ))


def request_backtest_cancel(user_id: int, run_id: str) -> None:
    with get_engine().begin() as connection:
        row = connection.execute(select(backtest_runs.c.status).where(and_(
            backtest_runs.c.id == run_id,
            backtest_runs.c.user_id == user_id,
        )).with_for_update()).mappings().first()
        if row is None or row["status"] not in {"QUEUED", "RUNNING"}:
            raise BacktestStoreError("回测任务不存在或已结束")
        if row["status"] == "QUEUED":
            connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                status="CANCELLED",
                cancel_requested=True,
                progress=100,
                completed_at=utc_now(),
                updated_at=utc_now(),
            ))
        else:
            connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                cancel_requested=True,
                updated_at=utc_now(),
            ))


def is_backtest_cancel_requested(run_id: str) -> bool:
    with get_engine().connect() as connection:
        return bool(connection.execute(select(backtest_runs.c.cancel_requested).where(backtest_runs.c.id == run_id)).scalar())


def save_backtest_series(run_id: str, symbol: str, timeframe: str, payload: dict[str, Any]) -> str:
    series_id = str(uuid4())
    blob = zlib.compress(_dump(payload).encode("utf-8"), level=6)
    candles = payload.get("chart", {}).get("candles", [])
    with get_engine().begin() as connection:
        connection.execute(insert(backtest_series).values(
            id=series_id,
            run_id=run_id,
            symbol=symbol,
            timeframe=timeframe,
            row_count=len(candles),
            start_time=datetime.fromisoformat(candles[0]["time"]) if candles else None,
            end_time=datetime.fromisoformat(candles[-1]["time"]) if candles else None,
            payload_blob=blob,
        ))
    return series_id


def get_backtest_series(user_id: int, run_id: str, symbol: str, timeframe: str) -> dict[str, Any]:
    with get_engine().connect() as connection:
        row = connection.execute(
            select(backtest_series.c.payload_blob)
            .join(backtest_runs, backtest_runs.c.id == backtest_series.c.run_id)
            .where(and_(
                backtest_series.c.run_id == run_id,
                backtest_series.c.symbol == symbol,
                backtest_series.c.timeframe == timeframe,
                backtest_runs.c.user_id == user_id,
            ))
        ).first()
        if row is None:
            raise BacktestStoreError("回测K线结构不存在")
        return json.loads(zlib.decompress(row[0]).decode("utf-8"))


def save_backtest_orders(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with get_engine().begin() as connection:
        connection.execute(insert(backtest_orders), rows)


def replace_backtest_summaries(run_id: str, rows: list[dict[str, Any]]) -> None:
    with get_engine().begin() as connection:
        connection.execute(delete(backtest_rule_summaries).where(backtest_rule_summaries.c.run_id == run_id))
        if rows:
            connection.execute(insert(backtest_rule_summaries), rows)


def add_backtest_error(run_id: str, symbol: str, timeframe: str, message: str) -> None:
    with get_engine().begin() as connection:
        connection.execute(insert(backtest_errors).values(
            run_id=run_id, symbol=symbol, timeframe=timeframe, message=message,
        ))


def list_backtest_orders(
    user_id: int,
    run_id: str,
    *,
    page: int = 1,
    page_size: int = 50,
    symbol: str | None = None,
    timeframe: str | None = None,
    rule_key: str | None = None,
    exit_reason: str | None = None,
) -> dict[str, Any]:
    conditions = [backtest_orders.c.run_id == run_id, backtest_runs.c.user_id == user_id]
    if symbol:
        conditions.append(backtest_orders.c.symbol == symbol)
    if timeframe:
        conditions.append(backtest_orders.c.timeframe == timeframe)
    if rule_key:
        conditions.append(backtest_orders.c.rule_key == rule_key)
    if exit_reason:
        conditions.append(backtest_orders.c.exit_reason == exit_reason)
    base = backtest_orders.join(backtest_runs, backtest_runs.c.id == backtest_orders.c.run_id)
    with get_engine().connect() as connection:
        total = connection.execute(select(func.count()).select_from(base).where(and_(*conditions))).scalar_one()
        rows = connection.execute(
            select(backtest_orders)
            .select_from(base)
            .where(and_(*conditions))
            .order_by(desc(backtest_orders.c.entry_time), backtest_orders.c.rule_key)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        items = []
        for row in rows:
            item = dict(row)
            item["signal"] = json.loads(item.pop("signal_json"))
            item["created_at"] = _utc_iso(item.get("created_at"))
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}


def all_backtest_orders(user_id: int, run_id: str) -> list[dict[str, Any]]:
    return list_backtest_orders(user_id, run_id, page=1, page_size=100000)["items"]


def delete_backtest_run(user_id: int, run_id: str) -> None:
    with get_engine().begin() as connection:
        result = connection.execute(delete(backtest_runs).where(and_(
            backtest_runs.c.id == run_id,
            backtest_runs.c.user_id == user_id,
            ~backtest_runs.c.status.in_(["QUEUED", "RUNNING"]),
        )))
        if result.rowcount == 0:
            raise BacktestStoreError("运行中的任务不能删除，或回测记录不存在")
