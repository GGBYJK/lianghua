from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import math
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .config import load_head_shoulder_config
from .strategy import KLINE_FEATURE_VERSION, indicator_feature_config_hash
from .trading_db import get_engine, kline_bar_features, kline_bars, kline_datasets, kline_sync_jobs, utc_now


class KlineStoreError(RuntimeError):
    pass


ACTIVE_JOB_STATUSES = {"QUEUED", "RUNNING"}
KLINE_SYNC_TIMEFRAME_ORDER = {
    "1m": 1,
    "3m": 2,
    "5m": 3,
    "15m": 4,
    "30m": 5,
    "1h": 6,
    "1d": 7,
}


@dataclass(frozen=True)
class KlineUpsertResult:
    processed_count: int
    changed_count: int
    earliest_changed_at: datetime | None
    latest_changed_at: datetime | None


def _dataset_dict(row: Any) -> dict[str, Any]:
    dataset = dict(row)
    config = load_head_shoulder_config(str(dataset["symbol"]), str(dataset["timeframe"]))
    dataset["features_ready"] = bool(
        dataset.get("feature_version") == KLINE_FEATURE_VERSION
        and dataset.get("feature_config_hash") == indicator_feature_config_hash(config)
        and int(dataset.get("feature_row_count") or 0) == int(dataset.get("row_count") or 0)
        and int(dataset.get("row_count") or 0) > 0
    )
    return dataset


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


def _sync_order(dataset: dict[str, Any]) -> tuple[str, int, str]:
    timeframe = str(dataset["timeframe"])
    return (
        str(dataset["symbol"]),
        KLINE_SYNC_TIMEFRAME_ORDER.get(timeframe, 99),
        timeframe,
    )


def create_kline_datasets(
    user_id: int,
    symbol: str,
    timeframes: list[str],
    provider: str,
    target_count: int,
    auto_update: bool,
) -> list[dict[str, Any]]:
    ordered_timeframes = sorted(
        dict.fromkeys(timeframes),
        key=lambda value: (KLINE_SYNC_TIMEFRAME_ORDER.get(value, 99), value),
    )
    if not ordered_timeframes:
        raise KlineStoreError("至少需要选择一个K线周期")

    now = utc_now()
    dataset_ids: list[str] = []
    with get_engine().begin() as connection:
        existing = connection.execute(select(kline_datasets.c.timeframe).where(and_(
            kline_datasets.c.provider == provider,
            kline_datasets.c.symbol == symbol,
            kline_datasets.c.timeframe.in_(ordered_timeframes),
        ))).scalars().all()
        if existing:
            periods = "、".join(sorted(existing, key=lambda value: KLINE_SYNC_TIMEFRAME_ORDER.get(value, 99)))
            raise KlineStoreError(f"该品种已存在以下周期的维护配置：{periods}")

        for sequence, timeframe in enumerate(ordered_timeframes, start=1):
            dataset_id = str(uuid4())
            dataset_ids.append(dataset_id)
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
                id=str(uuid4()),
                dataset_id=dataset_id,
                trigger_type="INITIAL",
                sequence=sequence,
                status="QUEUED",
                requested_count=target_count,
                created_at=now,
            ))

    return [get_kline_dataset(dataset_id) for dataset_id in dataset_ids]


def create_kline_dataset(
    user_id: int,
    symbol: str,
    timeframe: str,
    provider: str,
    target_count: int,
    auto_update: bool,
) -> dict[str, Any]:
    return create_kline_datasets(
        user_id,
        symbol,
        [timeframe],
        provider,
        target_count,
        auto_update,
    )[0]


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
    datasets = sorted(list_kline_datasets(), key=_sync_order)
    return [enqueue_kline_sync(item["id"], trigger_type) for item in datasets]


def enqueue_scheduled_kline_syncs(schedule_date: date) -> int:
    created = 0
    now = utc_now()
    with get_engine().begin() as connection:
        datasets = connection.execute(
            select(kline_datasets)
            .where(kline_datasets.c.auto_update.is_(True))
        ).mappings().all()
        datasets = sorted((dict(dataset) for dataset in datasets), key=_sync_order)
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
        dataset = connection.execute(select(kline_datasets).where(and_(
            kline_datasets.c.provider == provider,
            kline_datasets.c.symbol == symbol,
            kline_datasets.c.timeframe == timeframe,
        ))).mappings().first()
        if dataset is None:
            return None
        features_valid = bool(
            dataset["feature_version"]
            and int(dataset["feature_row_count"] or 0) == int(dataset["row_count"] or 0)
        )
        columns = [kline_bars]
        if features_valid:
            columns.extend([
                kline_bar_features.c.ma_json,
                kline_bar_features.c.ema_fast,
                kline_bar_features.c.ema_slow,
                kline_bar_features.c.macd_dif,
                kline_bar_features.c.macd_dea,
                kline_bar_features.c.macd_hist,
                kline_bar_features.c.trend_bullish,
                kline_bar_features.c.trend_bearish,
            ])
        statement = (
            select(*columns)
            .where(kline_bars.c.dataset_id == dataset["id"])
            .order_by(kline_bars.c.bar_time.desc())
            .limit(limit)
        )
        if features_valid:
            statement = statement.outerjoin(kline_bar_features, and_(
                kline_bar_features.c.dataset_id == kline_bars.c.dataset_id,
                kline_bar_features.c.bar_time == kline_bars.c.bar_time,
            ))
        rows = connection.execute(statement).mappings().all()
    if not rows:
        return None
    rows.reverse()
    frame = pd.DataFrame([{
        "datetime": row["bar_time"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
        **({
            "macd_dif": float(row["macd_dif"]) if row["macd_dif"] is not None else None,
            "macd_dea": float(row["macd_dea"]) if row["macd_dea"] is not None else None,
            "macd_hist": float(row["macd_hist"]) if row["macd_hist"] is not None else None,
            "ema_fast": float(row["ema_fast"]) if row["ema_fast"] is not None else None,
            "ema_slow": float(row["ema_slow"]) if row["ema_slow"] is not None else None,
            "trend_bullish": row["trend_bullish"],
            "trend_bearish": row["trend_bearish"],
            **{
                f"ma{period}": value
                for period, value in json.loads(row["ma_json"] or "{}").items()
            },
        } if features_valid else {}),
    } for row in rows])
    if features_valid:
        frame.attrs.update({
            "feature_version": dataset["feature_version"],
            "feature_config_hash": dataset["feature_config_hash"],
        })
    return frame


def read_kline_dataset_frame(dataset_id: str) -> pd.DataFrame:
    get_kline_dataset(dataset_id)
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(kline_bars)
            .where(kline_bars.c.dataset_id == dataset_id)
            .order_by(kline_bars.c.bar_time)
        ).mappings().all()
    return pd.DataFrame([{
        "datetime": row["bar_time"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
    } for row in rows])


def read_kline_dataset_window(
    dataset_id: str,
    start_time: datetime,
    warmup_bars: int,
) -> pd.DataFrame:
    get_kline_dataset(dataset_id)
    with get_engine().connect() as connection:
        preceding = connection.execute(
            select(kline_bars)
            .where(and_(
                kline_bars.c.dataset_id == dataset_id,
                kline_bars.c.bar_time < start_time,
            ))
            .order_by(kline_bars.c.bar_time.desc())
            .limit(max(0, warmup_bars))
        ).mappings().all()
        affected = connection.execute(
            select(kline_bars)
            .where(and_(
                kline_bars.c.dataset_id == dataset_id,
                kline_bars.c.bar_time >= start_time,
            ))
            .order_by(kline_bars.c.bar_time)
        ).mappings().all()
    rows = [*reversed(preceding), *affected]
    return pd.DataFrame([{
        "datetime": row["bar_time"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
    } for row in rows])


def read_kline_feature_seed(dataset_id: str, before_time: datetime) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        row = connection.execute(
            select(kline_bar_features)
            .where(and_(
                kline_bar_features.c.dataset_id == dataset_id,
                kline_bar_features.c.bar_time < before_time,
            ))
            .order_by(kline_bar_features.c.bar_time.desc())
            .limit(1)
        ).mappings().first()
    if row is None:
        return None
    return {
        "bar_time": row["bar_time"],
        "ema_fast": _finite_float(row["ema_fast"]),
        "ema_slow": _finite_float(row["ema_slow"]),
        "macd_dea": _finite_float(row["macd_dea"]),
    }


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _optional_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _kline_feature_rows(
    dataset_id: str,
    frame: pd.DataFrame,
    ma_periods: list[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = utc_now()
    for item in frame.to_dict("records"):
        raw_time = item["datetime"]
        bar_time = raw_time.to_pydatetime() if hasattr(raw_time, "to_pydatetime") else raw_time
        if not isinstance(bar_time, datetime):
            continue
        if bar_time.tzinfo is not None:
            bar_time = bar_time.replace(tzinfo=None)
        ma_values = {
            str(period): value
            for period in ma_periods
            if (value := _finite_float(item.get(f"ma{period}"))) is not None
        }
        rows.append({
            "dataset_id": dataset_id,
            "bar_time": bar_time,
            "ma_json": json.dumps(ma_values, separators=(",", ":")),
            "ema_fast": _finite_float(item.get("ema_fast")),
            "ema_slow": _finite_float(item.get("ema_slow")),
            "macd_dif": _finite_float(item.get("macd_dif")),
            "macd_dea": _finite_float(item.get("macd_dea")),
            "macd_hist": _finite_float(item.get("macd_hist")),
            "trend_bullish": _optional_int(item.get("trend_bullish")),
            "trend_bearish": _optional_int(item.get("trend_bearish")),
            "updated_at": now,
        })
    return rows


def replace_kline_features(
    dataset_id: str,
    frame: pd.DataFrame,
    *,
    feature_version: str,
    feature_config_hash: str,
    ma_periods: list[int],
    batch_size: int = 500,
) -> int:
    rows = _kline_feature_rows(dataset_id, frame, ma_periods)
    now = utc_now()
    with get_engine().begin() as connection:
        connection.execute(delete(kline_bar_features).where(kline_bar_features.c.dataset_id == dataset_id))
        for start in range(0, len(rows), max(1, batch_size)):
            connection.execute(insert(kline_bar_features), rows[start:start + batch_size])
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            feature_version=feature_version,
            feature_config_hash=feature_config_hash,
            feature_row_count=len(rows),
            features_updated_at=now,
            updated_at=now,
        ))
    return len(rows)


def upsert_kline_features(
    dataset_id: str,
    frame: pd.DataFrame,
    *,
    feature_version: str,
    feature_config_hash: str,
    ma_periods: list[int],
    batch_size: int = 500,
) -> int:
    rows = _kline_feature_rows(dataset_id, frame, ma_periods)
    now = utc_now()
    with get_engine().begin() as connection:
        dialect = connection.dialect.name
        for start in range(0, len(rows), max(1, batch_size)):
            batch = rows[start:start + batch_size]
            if dialect == "mysql":
                statement = mysql_insert(kline_bar_features).values(batch)
                statement = statement.on_duplicate_key_update(**{
                    key: getattr(statement.inserted, key)
                    for key in (
                        "ma_json", "ema_fast", "ema_slow", "macd_dif", "macd_dea",
                        "macd_hist", "trend_bullish", "trend_bearish", "updated_at",
                    )
                })
            elif dialect == "sqlite":
                statement = sqlite_insert(kline_bar_features).values(batch)
                statement = statement.on_conflict_do_update(
                    index_elements=["dataset_id", "bar_time"],
                    set_={
                        key: getattr(statement.excluded, key)
                        for key in (
                            "ma_json", "ema_fast", "ema_slow", "macd_dif", "macd_dea",
                            "macd_hist", "trend_bullish", "trend_bearish", "updated_at",
                        )
                    },
                )
            else:
                connection.execute(delete(kline_bar_features).where(and_(
                    kline_bar_features.c.dataset_id == dataset_id,
                    kline_bar_features.c.bar_time.in_([row["bar_time"] for row in batch]),
                )))
                statement = insert(kline_bar_features).values(batch)
            connection.execute(statement)
        feature_count = int(connection.execute(
            select(func.count()).select_from(kline_bar_features).where(
                kline_bar_features.c.dataset_id == dataset_id
            )
        ).scalar_one())
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            feature_version=feature_version,
            feature_config_hash=feature_config_hash,
            feature_row_count=feature_count,
            features_updated_at=now,
            updated_at=now,
        ))
    return len(rows)


def update_kline_trend_features(dataset_id: str, scores: list[dict[str, Any]]) -> int:
    if not scores:
        return 0
    now = utc_now()
    with get_engine().begin() as connection:
        for item in scores:
            raw_time = item["time"]
            bar_time = pd.Timestamp(raw_time).to_pydatetime()
            if bar_time.tzinfo is not None:
                bar_time = bar_time.replace(tzinfo=None)
            connection.execute(update(kline_bar_features).where(and_(
                kline_bar_features.c.dataset_id == dataset_id,
                kline_bar_features.c.bar_time == bar_time,
            )).values(
                trend_bullish=int(item["bullish"]),
                trend_bearish=int(item["bearish"]),
                updated_at=now,
            ))
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            features_updated_at=now,
            updated_at=now,
        ))
    return len(scores)


def list_kline_bars(dataset_id: str, page: int, page_size: int) -> dict[str, Any]:
    dataset = get_kline_dataset(dataset_id)
    features_valid = bool(
        dataset["feature_version"]
        and int(dataset["feature_row_count"] or 0) == int(dataset["row_count"] or 0)
    )
    with get_engine().connect() as connection:
        total = int(connection.execute(
            select(func.count()).select_from(kline_bars).where(kline_bars.c.dataset_id == dataset_id)
        ).scalar_one())
        columns = [kline_bars]
        if features_valid:
            columns.extend([
                kline_bar_features.c.ma_json,
                kline_bar_features.c.ema_fast,
                kline_bar_features.c.ema_slow,
                kline_bar_features.c.macd_dif,
                kline_bar_features.c.macd_dea,
                kline_bar_features.c.macd_hist,
                kline_bar_features.c.trend_bullish,
                kline_bar_features.c.trend_bearish,
            ])
        statement = (
            select(*columns)
            .where(kline_bars.c.dataset_id == dataset_id)
            .order_by(kline_bars.c.bar_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if features_valid:
            statement = statement.outerjoin(kline_bar_features, and_(
                kline_bar_features.c.dataset_id == kline_bars.c.dataset_id,
                kline_bar_features.c.bar_time == kline_bars.c.bar_time,
            ))
        rows = connection.execute(statement).mappings().all()
        items = []
        for row in rows:
            item = dict(row)
            if features_valid:
                item["ma"] = json.loads(item.pop("ma_json") or "{}")
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}


def _normalize_kline_rows(dataset_id: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    required = ["datetime", "open", "high", "low", "close", "volume"]
    normalized = frame[required].dropna().sort_values("datetime").drop_duplicates("datetime", keep="last")
    rows: list[dict[str, Any]] = []
    now = utc_now()
    storage_scale = Decimal("0.00000001")
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
            "open": Decimal(str(item["open"])).quantize(storage_scale),
            "high": Decimal(str(item["high"])).quantize(storage_scale),
            "low": Decimal(str(item["low"])).quantize(storage_scale),
            "close": Decimal(str(item["close"])).quantize(storage_scale),
            "volume": Decimal(str(item["volume"])).quantize(storage_scale),
            "updated_at": now,
        })
    return rows


def upsert_kline_frame_with_range(
    dataset_id: str,
    frame: pd.DataFrame,
    batch_size: int = 500,
) -> KlineUpsertResult:
    rows = _normalize_kline_rows(dataset_id, frame)
    if not rows:
        return KlineUpsertResult(0, 0, None, None)

    value_keys = ("open", "high", "low", "close", "volume")
    with get_engine().begin() as connection:
        existing: dict[datetime, tuple[Decimal, ...]] = {}
        for start in range(0, len(rows), max(1, batch_size)):
            bar_times = [row["bar_time"] for row in rows[start:start + batch_size]]
            stored_rows = connection.execute(select(
                kline_bars.c.bar_time,
                kline_bars.c.open,
                kline_bars.c.high,
                kline_bars.c.low,
                kline_bars.c.close,
                kline_bars.c.volume,
            ).where(and_(
                kline_bars.c.dataset_id == dataset_id,
                kline_bars.c.bar_time.in_(bar_times),
            ))).mappings()
            existing.update({
                item["bar_time"]: tuple(item[key] for key in value_keys)
                for item in stored_rows
            })

        changed_rows = [
            row for row in rows
            if existing.get(row["bar_time"]) != tuple(row[key] for key in value_keys)
        ]
        if not changed_rows:
            return KlineUpsertResult(len(rows), 0, None, None)

        dialect = connection.dialect.name
        for start in range(0, len(changed_rows), max(1, batch_size)):
            batch = changed_rows[start:start + batch_size]
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
        connection.execute(update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(
            feature_version=None,
            feature_config_hash=None,
            feature_row_count=0,
            features_updated_at=None,
            updated_at=utc_now(),
        ))
    changed_times = [row["bar_time"] for row in changed_rows]
    return KlineUpsertResult(
        processed_count=len(rows),
        changed_count=len(changed_rows),
        earliest_changed_at=min(changed_times),
        latest_changed_at=max(changed_times),
    )


def upsert_kline_frame(dataset_id: str, frame: pd.DataFrame, batch_size: int = 500) -> int:
    return upsert_kline_frame_with_range(dataset_id, frame, batch_size).processed_count


def trim_kline_dataset(dataset_id: str, target_count: int, *, data_changed: bool = True) -> int:
    deleted_count = 0
    with get_engine().begin() as connection:
        cutoff = connection.execute(
            select(kline_bars.c.bar_time)
            .where(kline_bars.c.dataset_id == dataset_id)
            .order_by(kline_bars.c.bar_time.desc())
            .offset(max(target_count - 1, 0))
            .limit(1)
        ).scalar_one_or_none()
        if cutoff is not None:
            result = connection.execute(delete(kline_bars).where(and_(
                kline_bars.c.dataset_id == dataset_id,
                kline_bars.c.bar_time < cutoff,
            )))
            deleted_count = max(0, int(result.rowcount or 0))
            connection.execute(delete(kline_bar_features).where(and_(
                kline_bar_features.c.dataset_id == dataset_id,
                kline_bar_features.c.bar_time < cutoff,
            )))
    refresh_kline_dataset_stats(
        dataset_id,
        increment_revision=data_changed or deleted_count > 0,
    )
    return deleted_count


def refresh_kline_dataset_stats(dataset_id: str, *, increment_revision: bool = True) -> None:
    with get_engine().begin() as connection:
        stats = connection.execute(select(
            func.count().label("row_count"),
            func.min(kline_bars.c.bar_time).label("start_time"),
            func.max(kline_bars.c.bar_time).label("end_time"),
        ).where(kline_bars.c.dataset_id == dataset_id)).mappings().one()
        feature_count = int(connection.execute(
            select(func.count()).select_from(kline_bar_features).where(
                kline_bar_features.c.dataset_id == dataset_id
            )
        ).scalar_one())
        values: dict[str, Any] = {
            "row_count": int(stats["row_count"]),
            "start_time": stats["start_time"],
            "end_time": stats["end_time"],
            "feature_row_count": feature_count,
            "updated_at": utc_now(),
        }
        if increment_revision:
            values["revision"] = kline_datasets.c.revision + 1
        connection.execute(
            update(kline_datasets).where(kline_datasets.c.id == dataset_id).values(**values)
        )
