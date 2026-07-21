from __future__ import annotations

import json
import logging
import os
import time
import zlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, desc, func, insert, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from .trading_db import (
    backtest_errors,
    backtest_orders,
    backtest_rule_summaries,
    backtest_runs,
    backtest_series,
    backtest_symbol_groups,
    get_engine,
    utc_now,
)


class BacktestStoreError(ValueError):
    pass


class BacktestLeaseLostError(RuntimeError):
    pass


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("app.backtest_store")
BACKTEST_STALE_SECONDS = int(os.getenv("BACKTEST_STALE_SECONDS", "120"))
BACKTEST_MAX_ATTEMPTS = int(os.getenv("BACKTEST_MAX_ATTEMPTS", "3"))
BACKTEST_DB_RETRY_ATTEMPTS = int(os.getenv("BACKTEST_DB_RETRY_ATTEMPTS", "3"))
BACKTEST_DB_RETRY_DELAY = float(os.getenv("BACKTEST_DB_RETRY_DELAY", "0.25"))
T = TypeVar("T")


def _retry_database_write(name: str, operation: Callable[[], T]) -> T:
    for attempt in range(1, BACKTEST_DB_RETRY_ATTEMPTS + 1):
        try:
            return operation()
        except DBAPIError:
            if attempt >= BACKTEST_DB_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "backtest database write failed; retrying: operation=%s attempt=%s",
                name,
                attempt,
                exc_info=True,
            )
            get_engine().dispose()
            time.sleep(BACKTEST_DB_RETRY_DELAY * attempt)
    raise RuntimeError("unreachable")


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
            status="PENDING",
            progress=0,
            request_json=_dump(payload),
            total_combinations=total,
        ))
        row = connection.execute(select(backtest_runs).where(backtest_runs.c.id == run_id)).mappings().one()
    return _run_dict(row)


def _symbol_group_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["symbols"] = json.loads(item.pop("symbols_json"))
    for field in ("created_at", "updated_at"):
        item[field] = _utc_iso(item.get(field))
    return item


def list_backtest_symbol_groups(user_id: int) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(backtest_symbol_groups)
            .where(backtest_symbol_groups.c.user_id == user_id)
            .order_by(desc(backtest_symbol_groups.c.updated_at), backtest_symbol_groups.c.name)
        ).mappings()
        return [_symbol_group_dict(row) for row in rows]


def create_backtest_symbol_group(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    group_id = str(uuid4())
    try:
        with get_engine().begin() as connection:
            connection.execute(insert(backtest_symbol_groups).values(
                id=group_id,
                user_id=user_id,
                name=payload["name"],
                symbols_json=_dump(payload["symbols"]),
            ))
            row = connection.execute(
                select(backtest_symbol_groups).where(backtest_symbol_groups.c.id == group_id)
            ).mappings().one()
    except IntegrityError as exc:
        raise BacktestStoreError("已存在同名品种分组") from exc
    return _symbol_group_dict(row)


def update_backtest_symbol_group(user_id: int, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        with get_engine().begin() as connection:
            result = connection.execute(update(backtest_symbol_groups).where(and_(
                backtest_symbol_groups.c.id == group_id,
                backtest_symbol_groups.c.user_id == user_id,
            )).values(
                name=payload["name"],
                symbols_json=_dump(payload["symbols"]),
                updated_at=utc_now(),
            ))
            if result.rowcount == 0:
                raise BacktestStoreError("品种分组不存在")
            row = connection.execute(
                select(backtest_symbol_groups).where(backtest_symbol_groups.c.id == group_id)
            ).mappings().one()
    except IntegrityError as exc:
        raise BacktestStoreError("已存在同名品种分组") from exc
    return _symbol_group_dict(row)


def delete_backtest_symbol_group(user_id: int, group_id: str) -> None:
    with get_engine().begin() as connection:
        result = connection.execute(delete(backtest_symbol_groups).where(and_(
            backtest_symbol_groups.c.id == group_id,
            backtest_symbol_groups.c.user_id == user_id,
        )))
        if result.rowcount == 0:
            raise BacktestStoreError("品种分组不存在")


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


def claim_next_backtest_run(worker_id: str) -> dict[str, Any] | None:
    with get_engine().begin() as connection:
        row = connection.execute(
            select(backtest_runs)
            .where(backtest_runs.c.status == "PENDING")
            .order_by(backtest_runs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).mappings().first()
        if row is None:
            return None
        now = utc_now()
        connection.execute(update(backtest_runs).where(backtest_runs.c.id == row["id"]).values(
            status="RUNNING", started_at=now, updated_at=now, heartbeat_at=now,
            worker_id=worker_id, attempt_count=backtest_runs.c.attempt_count + 1,
            error_message=None, progress=1,
        ))
        claimed = dict(row)
        claimed["status"] = "RUNNING"
        claimed["worker_id"] = worker_id
        claimed["attempt_count"] = int(claimed.get("attempt_count") or 0) + 1
        claimed["request"] = json.loads(claimed.pop("request_json"))
        return claimed


def touch_backtest_heartbeat(run_id: str, worker_id: str) -> bool:
    def write() -> bool:
        now = utc_now()
        with get_engine().begin() as connection:
            result = connection.execute(update(backtest_runs).where(and_(
                backtest_runs.c.id == run_id,
                backtest_runs.c.status == "RUNNING",
                backtest_runs.c.worker_id == worker_id,
            )).values(heartbeat_at=now, updated_at=now))
            return result.rowcount == 1

    return _retry_database_write("heartbeat", write)


def update_backtest_progress(
    run_id: str,
    completed: int,
    signals: int,
    orders: int,
    worker_id: str | None = None,
) -> None:
    def write() -> None:
        with get_engine().begin() as connection:
            total = connection.execute(
                select(backtest_runs.c.total_combinations).where(backtest_runs.c.id == run_id)
            ).scalar_one()
            progress = 99 if completed >= total else max(1, int(completed * 100 / max(total, 1)))
            conditions = [backtest_runs.c.id == run_id, backtest_runs.c.status == "RUNNING"]
            if worker_id is not None:
                conditions.append(backtest_runs.c.worker_id == worker_id)
            result = connection.execute(update(backtest_runs).where(and_(*conditions)).values(
                completed_combinations=completed,
                signal_count=signals,
                order_count=orders,
                progress=progress,
                heartbeat_at=utc_now(),
                updated_at=utc_now(),
            ))
            if result.rowcount != 1:
                raise BacktestLeaseLostError(f"backtest lease lost: {run_id}")

    _retry_database_write("progress", write)


def finish_backtest_run(
    run_id: str,
    status: str,
    signals: int,
    orders: int,
    error_message: str | None = None,
    worker_id: str | None = None,
) -> None:
    def write() -> None:
        with get_engine().begin() as connection:
            conditions = [backtest_runs.c.id == run_id]
            if worker_id is not None:
                conditions.extend((backtest_runs.c.status == "RUNNING", backtest_runs.c.worker_id == worker_id))
            result = connection.execute(update(backtest_runs).where(and_(*conditions)).values(
                status=status,
                progress=100,
                signal_count=signals,
                order_count=orders,
                worker_id=None,
                heartbeat_at=None,
                error_message=error_message,
                completed_at=utc_now(),
                updated_at=utc_now(),
            ))
            if result.rowcount != 1:
                raise BacktestLeaseLostError(f"backtest lease lost: {run_id}")

    _retry_database_write("finish", write)


def _stale_run_condition(cutoff: datetime) -> Any:
    return or_(
        backtest_runs.c.heartbeat_at < cutoff,
        and_(backtest_runs.c.heartbeat_at.is_(None), backtest_runs.c.updated_at < cutoff),
    )


def recover_stale_backtest_runs(
    stale_seconds: int = BACKTEST_STALE_SECONDS,
    max_attempts: int = BACKTEST_MAX_ATTEMPTS,
) -> dict[str, int]:
    cutoff = utc_now() - timedelta(seconds=stale_seconds)

    def write() -> dict[str, int]:
        result = {"requeued": 0, "cancelled": 0, "failed": 0}
        with get_engine().begin() as connection:
            rows = connection.execute(
                select(backtest_runs)
                .where(and_(backtest_runs.c.status == "RUNNING", _stale_run_condition(cutoff)))
                .with_for_update(skip_locked=True)
            ).mappings().all()
            for row in rows:
                run_id = str(row["id"])
                now = utc_now()
                if row["cancel_requested"]:
                    connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                        status="CANCELLED", progress=100, worker_id=None, heartbeat_at=None,
                        completed_at=now, updated_at=now,
                    ))
                    result["cancelled"] += 1
                    continue
                if int(row["attempt_count"] or 0) >= max_attempts:
                    connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                        status="FAILED", progress=100, worker_id=None, heartbeat_at=None,
                        error_message="Backtest worker stopped repeatedly; automatic retry limit reached.",
                        completed_at=now, updated_at=now,
                    ))
                    result["failed"] += 1
                    continue

                connection.execute(delete(backtest_orders).where(backtest_orders.c.run_id == run_id))
                connection.execute(delete(backtest_rule_summaries).where(backtest_rule_summaries.c.run_id == run_id))
                connection.execute(delete(backtest_errors).where(backtest_errors.c.run_id == run_id))
                connection.execute(delete(backtest_series).where(backtest_series.c.run_id == run_id))
                connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                    status="PENDING", progress=0, completed_combinations=0, signal_count=0, order_count=0,
                    worker_id=None, heartbeat_at=None, started_at=None, completed_at=None,
                    error_message="Backtest worker stopped; task was automatically requeued.", updated_at=now,
                ))
                result["requeued"] += 1
        return result

    return _retry_database_write("stale recovery", write)


def request_backtest_cancel(
    user_id: int,
    run_id: str,
    stale_seconds: int = BACKTEST_STALE_SECONDS,
) -> None:
    def write() -> None:
        with get_engine().begin() as connection:
            row = connection.execute(select(backtest_runs).where(and_(
                backtest_runs.c.id == run_id,
                backtest_runs.c.user_id == user_id,
            )).with_for_update()).mappings().first()
            if row is None or row["status"] not in {"PENDING", "QUEUED", "RUNNING"}:
                raise BacktestStoreError("回测任务不存在或已结束")
            now = utc_now()
            heartbeat = row["heartbeat_at"] or row["updated_at"]
            is_stale = heartbeat is None or heartbeat < now - timedelta(seconds=stale_seconds)
            if row["status"] in {"PENDING", "QUEUED"} or is_stale:
                connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                    status="CANCELLED",
                    cancel_requested=True,
                    progress=100,
                    worker_id=None,
                    heartbeat_at=None,
                    completed_at=now,
                    updated_at=now,
                ))
            else:
                connection.execute(update(backtest_runs).where(backtest_runs.c.id == run_id).values(
                    cancel_requested=True,
                    updated_at=now,
                ))

    _retry_database_write("cancel", write)


def is_backtest_cancel_requested(run_id: str) -> bool:
    def read() -> bool:
        with get_engine().connect() as connection:
            return bool(connection.execute(
                select(backtest_runs.c.cancel_requested).where(backtest_runs.c.id == run_id)
            ).scalar())

    return _retry_database_write("cancel check", read)


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
    def write() -> None:
        with get_engine().begin() as connection:
            connection.execute(delete(backtest_rule_summaries).where(backtest_rule_summaries.c.run_id == run_id))
            if rows:
                connection.execute(insert(backtest_rule_summaries), rows)

    _retry_database_write("summary replacement", write)


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
    alert_type: str | None = None,
    summary_entry_condition: str | None = None,
    exit_reason: str | None = None,
) -> dict[str, Any]:
    conditions = [backtest_orders.c.run_id == run_id, backtest_runs.c.user_id == user_id]
    if symbol:
        conditions.append(backtest_orders.c.symbol == symbol)
    if timeframe:
        conditions.append(backtest_orders.c.timeframe == timeframe)
    if rule_key:
        conditions.append(backtest_orders.c.rule_key == rule_key)
    if alert_type:
        conditions.append(backtest_orders.c.alert_type == alert_type)
    if summary_entry_condition:
        conditions.append(backtest_orders.c.entry_condition == summary_entry_condition)
    if exit_reason:
        conditions.append(backtest_orders.c.exit_reason == exit_reason)
    base = backtest_orders.join(backtest_runs, backtest_runs.c.id == backtest_orders.c.run_id)
    with get_engine().connect() as connection:
        total = connection.execute(select(func.count()).select_from(base).where(and_(*conditions))).scalar_one()
        totals = connection.execute(
            select(
                func.coalesce(func.sum(backtest_orders.c.net_pnl), Decimal("0")).label("net_pnl"),
                func.coalesce(func.sum(backtest_orders.c.fees), Decimal("0")).label("fees"),
            )
            .select_from(base)
            .where(and_(*conditions))
        ).mappings().one()
        rows = connection.execute(
            select(backtest_orders)
            .select_from(base)
            .where(and_(*conditions))
            .order_by(backtest_orders.c.entry_time.asc(), backtest_orders.c.rule_key)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        items = []
        for row in rows:
            item = dict(row)
            item["signal"] = json.loads(item.pop("signal_json"))
            item["created_at"] = _utc_iso(item.get("created_at"))
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size, "totals": dict(totals)}


def all_backtest_orders(user_id: int, run_id: str) -> list[dict[str, Any]]:
    return list_backtest_orders(user_id, run_id, page=1, page_size=100000)["items"]


def backtest_equity_curve(
    user_id: int,
    run_id: str,
    rule_key: str,
    summary_entry_condition: str | None = None,
) -> list[dict[str, Any]]:
    base = backtest_orders.join(backtest_runs, backtest_runs.c.id == backtest_orders.c.run_id)
    conditions = [
        backtest_orders.c.run_id == run_id,
        backtest_runs.c.user_id == user_id,
        backtest_orders.c.rule_key == rule_key,
        backtest_orders.c.status == "CLOSED",
        backtest_orders.c.net_pnl.is_not(None),
    ]
    if summary_entry_condition:
        conditions.append(backtest_orders.c.entry_condition == summary_entry_condition)
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(backtest_orders.c.entry_time, backtest_orders.c.exit_time, backtest_orders.c.net_pnl)
            .select_from(base)
            .where(and_(*conditions))
            .order_by(backtest_orders.c.exit_time.asc(), backtest_orders.c.id.asc())
        ).mappings()
        cumulative = Decimal("0")
        points: list[dict[str, Any]] = []
        for row in rows:
            net_pnl = Decimal(str(row["net_pnl"]))
            cumulative += net_pnl
            points.append({
                # Backtest candles and orders use market-local, naive timestamps.
                # Keep the curve on that same timeline as the order details view.
                "entry_time": row["entry_time"].isoformat() if row["entry_time"] else None,
                "time": row["exit_time"].isoformat(),
                "net_pnl": net_pnl,
                "cumulative_net_pnl": cumulative,
            })
        return points


def _capital_usage_points(
    rows: list[dict[str, Any]],
    initial_capital: Decimal,
) -> list[dict[str, Any]]:
    events: dict[datetime, dict[str, Decimal]] = {}
    for row in rows:
        margin = Decimal(str(row.get("margin") or 0))
        entry_time = row.get("entry_time")
        if margin <= 0 or entry_time is None:
            continue
        entry_event = events.setdefault(entry_time, {
            "margin_change": Decimal("0"),
            "realized_pnl": Decimal("0"),
        })
        entry_event["margin_change"] += margin
        exit_time = row.get("exit_time")
        if exit_time is not None:
            exit_event = events.setdefault(exit_time, {
                "margin_change": Decimal("0"),
                "realized_pnl": Decimal("0"),
            })
            exit_event["margin_change"] -= margin
            exit_event["realized_pnl"] += Decimal(str(row.get("net_pnl") or 0))

    used_margin = Decimal("0")
    total_funds = initial_capital
    points: list[dict[str, Any]] = []
    for event_time in sorted(events):
        event = events[event_time]
        used_margin = max(Decimal("0"), used_margin + event["margin_change"])
        total_funds += event["realized_pnl"]
        usage_rate = used_margin / total_funds * Decimal("100") if total_funds > 0 else Decimal("0")
        points.append({
            "time": event_time.isoformat(),
            "used_margin": used_margin,
            "total_funds": total_funds,
            "usage_rate": usage_rate,
        })
    return points


def backtest_capital_usage(
    user_id: int,
    run_id: str,
    rule_key: str,
    summary_entry_condition: str | None = None,
) -> list[dict[str, Any]]:
    base = backtest_orders.join(backtest_runs, backtest_runs.c.id == backtest_orders.c.run_id)
    conditions = [
        backtest_orders.c.run_id == run_id,
        backtest_runs.c.user_id == user_id,
        backtest_orders.c.rule_key == rule_key,
        backtest_orders.c.status.in_(["CLOSED", "INCOMPLETE"]),
        backtest_orders.c.entry_time.is_not(None),
        backtest_orders.c.margin.is_not(None),
    ]
    if summary_entry_condition:
        conditions.append(backtest_orders.c.entry_condition == summary_entry_condition)
    with get_engine().connect() as connection:
        run_row = connection.execute(
            select(backtest_runs.c.request_json).where(and_(
                backtest_runs.c.id == run_id,
                backtest_runs.c.user_id == user_id,
            ))
        ).first()
        if run_row is None:
            raise BacktestStoreError("回测任务不存在")
        initial_capital = Decimal(str(json.loads(run_row[0]).get("initial_capital", 1_000_000)))
        rows = [dict(row) for row in connection.execute(
            select(
                backtest_orders.c.entry_time,
                backtest_orders.c.exit_time,
                backtest_orders.c.margin,
                backtest_orders.c.net_pnl,
            )
            .select_from(base)
            .where(and_(*conditions))
        ).mappings()]
    return _capital_usage_points(rows, initial_capital)


def delete_backtest_run(user_id: int, run_id: str) -> None:
    with get_engine().begin() as connection:
        result = connection.execute(delete(backtest_runs).where(and_(
            backtest_runs.c.id == run_id,
            backtest_runs.c.user_id == user_id,
            ~backtest_runs.c.status.in_(["PENDING", "QUEUED", "RUNNING"]),
        )))
        if result.rowcount == 0:
            raise BacktestStoreError("运行中的任务不能删除，或回测记录不存在")
