from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .trading_db import get_engine, kline_bars, kline_datasets, kline_sync_jobs, utc_now


class KlineStoreError(RuntimeError):
    pass


ACTIVE_JOB_STATUSES = {"QUEUED", "RUNNING"}


def _dataset_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def get_kline_dataset(dataset_id: str) -> dict[str, Any]:
    with get_engine().connect() as connection:
        row = connection.execute(
            select(kline_datasets).where(kline_datasets.c.id == dataset_id)
        ).mappings().first()
    if row is None:
        raise KlineStoreError("K线数据集不存在")
    return _dataset_dict(row)


def find_kline_dataset(symbol: str, timeframe: str, provider: str) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        row = connection.execute(select(kline_datasets).where(and_(
            kline_datasets.c.provider == provider,
            kline_datasets.c.symbol == symbol,
            kline_datasets.c.timeframe == timeframe,
        ))).mappings().first()
    return _dataset_dict(row) if row is not None else None


def list_kline_datasets() -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(kline_datasets).order_by(kline_datasets.c.symbol, kline_datasets.c.timeframe)
        ).mappings()
        return [_dataset_dict(row) for row in rows]


def create_kline_dataset(
    user_id: int,
    symbol: str,
    timeframe: str,
    provider: str,
    target_count: int,
    auto_update: bool,
) -> dict[str, Any]:
    dataset_id = str(uuid4())
    job_id = str(uuid4())
    now = utc_now()
    with get_engine().begin() as connection:
        existing = connection.execute(select(kline_datasets.c.id).where(and_(
            kline_datasets.c.provider == provider,
            kline_datasets.c.symbol == symbol,
            kline_datasets.c.timeframe == timeframe,
        ))).first()
        if existing is not None:
            raise KlineStoreError("该品种和周期已经存在维护配置")
        connection.execute(insert(kline_datasets).values(
            id=dataset_id,
            symbol=symbol,
            timeframe=timeframe,
            provider=provider,
            target_count=target_count,
            auto_update=auto_update,
            status="QUEUED",
            created_by=user_id,
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(kline_sync_jobs).values(
            id=job_id,
            dataset_id=dataset_id,
            trigger_type="INITIAL",
            sequence=0,
            status="QUEUED",
            requested_count=target_count,
            created_at=now,
        ))
    return get_kline_dataset(dataset_id)


def update_kline_dataset(
    dataset_id: str,
    *,
    target_count: int | None = None,
    auto_update: bool | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {"updated_at": utc_now()}
    if target_count is not None:
        values["target_count"] = target_count
    if auto_update is not None:
        values["auto_update"] = auto_update
    with get_engine().begin() as connection:
        result = connection.execute(
            update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(**values)
        )
        if result.rowcount != 1:
            raise KlineStoreError("K线数据集不存在")
    if target_count is not None:
        trim_kline_dataset(dataset_id, target_count)
    return get_kline_dataset(dataset_id)


def delete_kline_dataset(dataset_id: str) -> None:
    with get_engine().begin() as connection:
        running = connection.execute(select(kline_sync_jobs.c.id).where(and_(
            kline_sync_jobs.c.dataset_id == dataset_id,
            kline_sync_jobs.c.status == "RUNNING",
        ))).first()
        if running is not None:
            raise KlineStoreError("数据集正在更新，暂时不能删除")
        result = connection.execute(delete(kline_datasets).where(kline_datasets.c.id == dataset_id))
        if result.rowcount != 1:
            raise KlineStoreError("K线数据集不存在")


def enqueue_kline_sync(dataset_id: str, trigger_type: str = "MANUAL") -> dict[str, Any]:
    job_id = str(uuid4())
    now = utc_now()
    with get_engine().begin() as connection:
        dataset = connection.execute(
            select(kline_datasets).where(kline_datasets.c.id == dataset_id).with_for_update()
        ).mappings().first()
        if dataset is None:
            raise KlineStoreError("K线数据集不存在")
        active = connection.execute(select(kline_sync_jobs).where(and_(
            kline_sync_jobs.c.dataset_id == dataset_id,
            kline_sync_jobs.c.status.in_(ACTIVE_JOB_STATUSES),
        )).order_by(kline_sync_jobs.c.created_at).limit(1)).mappings().first()
        if active is not None:
            return dict(active)
        connection.execute(insert(kline_sync_jobs).values(
            id=job_id,
            dataset_id=dataset_id,
            trigger_type=trigger_type,
            sequence=0,
            status="QUEUED",
            requested_count=int(dataset["target_count"]),
            created_at=now,
        ))
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            status="QUEUED", last_error=None, updated_at=now,
        ))
    return get_kline_sync_job(job_id)


def enqueue_all_kline_syncs(trigger_type: str = "MANUAL") -> list[dict[str, Any]]:
    return [enqueue_kline_sync(item["id"], trigger_type) for item in list_kline_datasets()]


def enqueue_scheduled_kline_syncs(schedule_date: date) -> int:
    created = 0
    now = utc_now()
    with get_engine().begin() as connection:
        datasets = connection.execute(
            select(kline_datasets)
            .where(kline_datasets.c.auto_update.is_(True))
            .order_by(kline_datasets.c.symbol, kline_datasets.c.timeframe, kline_datasets.c.created_at)
        ).mappings().all()
        for sequence, dataset in enumerate(datasets, start=1):
            exists = connection.execute(select(kline_sync_jobs.c.id).where(and_(
                kline_sync_jobs.c.dataset_id == dataset["id"],
                kline_sync_jobs.c.trigger_type == "SCHEDULED",
                kline_sync_jobs.c.schedule_date == schedule_date,
            ))).first()
            if exists is not None:
                continue
            connection.execute(insert(kline_sync_jobs).values(
                id=str(uuid4()),
                dataset_id=dataset["id"],
                trigger_type="SCHEDULED",
                schedule_date=schedule_date,
                sequence=sequence,
                status="QUEUED",
                requested_count=int(dataset["target_count"]),
                created_at=now,
            ))
            if dataset["status"] != "RUNNING":
                connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset["id"]).values(
                    status="QUEUED", last_error=None, updated_at=now,
                ))
            created += 1
    return created


def get_kline_sync_job(job_id: str) -> dict[str, Any]:
    with get_engine().connect() as connection:
        row = connection.execute(
            select(kline_sync_jobs).where(kline_sync_jobs.c.id == job_id)
        ).mappings().first()
    if row is None:
        raise KlineStoreError("K线更新任务不存在")
    return dict(row)


def list_kline_sync_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(
                kline_sync_jobs,
                kline_datasets.c.symbol.label("symbol"),
                kline_datasets.c.timeframe.label("timeframe"),
            )
            .join(kline_datasets, kline_datasets.c.id == kline_sync_jobs.c.dataset_id)
            .order_by(kline_sync_jobs.c.created_at.desc())
            .limit(limit)
        ).mappings()
        return [dict(row) for row in rows]


def claim_next_kline_sync_job(worker_id: str) -> dict[str, Any] | None:
    with get_engine().begin() as connection:
        row = connection.execute(
            select(kline_sync_jobs)
            .where(kline_sync_jobs.c.status == "QUEUED")
            .order_by(kline_sync_jobs.c.created_at, kline_sync_jobs.c.sequence)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).mappings().first()
        if row is None:
            return None
        now = utc_now()
        connection.execute(update(kline_sync_jobs).where(kline_sync_jobs.c.id == row["id"]).values(
            status="RUNNING", worker_id=worker_id, started_at=now, error_message=None,
        ))
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == row["dataset_id"]).values(
            status="RUNNING", last_error=None, updated_at=now,
        ))
        claimed = dict(row)
        claimed["status"] = "RUNNING"
        claimed["worker_id"] = worker_id
        return claimed


def recover_stale_kline_sync_jobs(stale_seconds: int = 900) -> int:
    cutoff = utc_now() - timedelta(seconds=stale_seconds)
    with get_engine().begin() as connection:
        rows = connection.execute(select(kline_sync_jobs.c.id, kline_sync_jobs.c.dataset_id).where(and_(
            kline_sync_jobs.c.status == "RUNNING",
            kline_sync_jobs.c.started_at < cutoff,
        )).with_for_update(skip_locked=True)).mappings().all()
        if not rows:
            return 0
        job_ids = [row["id"] for row in rows]
        dataset_ids = [row["dataset_id"] for row in rows]
        connection.execute(update(kline_sync_jobs).where(kline_sync_jobs.c.id.in_(job_ids)).values(
            status="QUEUED", worker_id=None, started_at=None, error_message="任务中断，已自动重新排队",
        ))
        connection.execute(update(kline_datasets).where(kline_datasets.c.id.in_(dataset_ids)).values(
            status="QUEUED", updated_at=utc_now(),
        ))
        return len(rows)


def finish_kline_sync_job(
    job_id: str,
    dataset_id: str,
    *,
    fetched_count: int,
    written_count: int,
    error_message: str | None = None,
) -> None:
    now = utc_now()
    status = "FAILED" if error_message else "COMPLETED"
    with get_engine().begin() as connection:
        connection.execute(update(kline_sync_jobs).where(kline_sync_jobs.c.id == job_id).values(
            status=status,
            fetched_count=fetched_count,
            written_count=written_count,
            error_message=error_message,
            worker_id=None,
            completed_at=now,
        ))
        has_queued_job = connection.execute(select(kline_sync_jobs.c.id).where(and_(
            kline_sync_jobs.c.dataset_id == dataset_id,
            kline_sync_jobs.c.status == "QUEUED",
        )).limit(1)).first() is not None
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            status="QUEUED" if has_queued_job else status if error_message else "IDLE",
            last_synced_at=None if error_message else now,
            last_error=error_message,
            updated_at=now,
        ))


def read_cached_klines(symbol: str, timeframe: str, provider: str, limit: int) -> pd.DataFrame | None:
    with get_engine().connect() as connection:
        dataset_id = connection.execute(select(kline_datasets.c.id).where(and_(
            kline_datasets.c.provider == provider,
            kline_datasets.c.symbol == symbol,
            kline_datasets.c.timeframe == timeframe,
        ))).scalar_one_or_none()
        if dataset_id is None:
            return None
        rows = connection.execute(
            select(kline_bars)
            .where(kline_bars.c.dataset_id == dataset_id)
            .order_by(kline_bars.c.bar_time.desc())
            .limit(limit)
        ).mappings().all()
    if not rows:
        return None
    rows.reverse()
    return pd.DataFrame([{
        "datetime": row["bar_time"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
    } for row in rows])


def list_kline_bars(dataset_id: str, page: int, page_size: int) -> dict[str, Any]:
    get_kline_dataset(dataset_id)
    with get_engine().connect() as connection:
        total = int(connection.execute(
            select(func.count()).select_from(kline_bars).where(kline_bars.c.dataset_id == dataset_id)
        ).scalar_one())
        rows = connection.execute(
            select(kline_bars)
            .where(kline_bars.c.dataset_id == dataset_id)
            .order_by(kline_bars.c.bar_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


def upsert_kline_frame(dataset_id: str, frame: pd.DataFrame, batch_size: int = 500) -> int:
    required = ["datetime", "open", "high", "low", "close", "volume"]
    normalized = frame[required].dropna().sort_values("datetime").drop_duplicates("datetime", keep="last")
    rows: list[dict[str, Any]] = []
    for item in normalized.to_dict("records"):
        raw_time = item["datetime"]
        bar_time = raw_time.to_pydatetime() if hasattr(raw_time, "to_pydatetime") else raw_time
        if not isinstance(bar_time, datetime):
            continue
        if bar_time.tzinfo is not None:
            bar_time = bar_time.replace(tzinfo=None)
        rows.append({
            "dataset_id": dataset_id,
            "bar_time": bar_time,
            "open": Decimal(str(item["open"])),
            "high": Decimal(str(item["high"])),
            "low": Decimal(str(item["low"])),
            "close": Decimal(str(item["close"])),
            "volume": Decimal(str(item["volume"])),
            "updated_at": utc_now(),
        })
    if not rows:
        return 0
    with get_engine().begin() as connection:
        dialect = connection.dialect.name
        for start in range(0, len(rows), max(1, batch_size)):
            batch = rows[start:start + batch_size]
            if dialect == "mysql":
                statement = mysql_insert(kline_bars).values(batch)
                statement = statement.on_duplicate_key_update(
                    open=statement.inserted.open,
                    high=statement.inserted.high,
                    low=statement.inserted.low,
                    close=statement.inserted.close,
                    volume=statement.inserted.volume,
                    updated_at=statement.inserted.updated_at,
                )
            elif dialect == "sqlite":
                statement = sqlite_insert(kline_bars).values(batch)
                statement = statement.on_conflict_do_update(
                    index_elements=["dataset_id", "bar_time"],
                    set_={key: getattr(statement.excluded, key) for key in ("open", "high", "low", "close", "volume", "updated_at")},
                )
            else:
                statement = insert(kline_bars).values(batch)
            connection.execute(statement)
    return len(rows)


def trim_kline_dataset(dataset_id: str, target_count: int) -> None:
    with get_engine().begin() as connection:
        cutoff = connection.execute(
            select(kline_bars.c.bar_time)
            .where(kline_bars.c.dataset_id == dataset_id)
            .order_by(kline_bars.c.bar_time.desc())
            .offset(max(target_count - 1, 0))
            .limit(1)
        ).scalar_one_or_none()
        if cutoff is not None:
            connection.execute(delete(kline_bars).where(and_(
                kline_bars.c.dataset_id == dataset_id,
                kline_bars.c.bar_time < cutoff,
            )))
    refresh_kline_dataset_stats(dataset_id)


def refresh_kline_dataset_stats(dataset_id: str) -> None:
    with get_engine().begin() as connection:
        stats = connection.execute(select(
            func.count().label("row_count"),
            func.min(kline_bars.c.bar_time).label("start_time"),
            func.max(kline_bars.c.bar_time).label("end_time"),
        ).where(kline_bars.c.dataset_id == dataset_id)).mappings().one()
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            row_count=int(stats["row_count"]),
            start_time=stats["start_time"],
            end_time=stats["end_time"],
            revision=kline_datasets.c.revision + 1,
            updated_at=utc_now(),
        ))
