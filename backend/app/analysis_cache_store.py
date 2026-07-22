from __future__ import annotations

import json
import zlib
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .trading_db import get_engine, market_analysis_cache, utc_now


def load_analysis_cache(cache_key: str) -> dict[str, Any] | None:
    with get_engine().begin() as connection:
        row = connection.execute(
            select(market_analysis_cache.c.payload_blob).where(
                market_analysis_cache.c.cache_key == cache_key
            )
        ).first()
        if row is None:
            return None
        connection.execute(
            update(market_analysis_cache)
            .where(market_analysis_cache.c.cache_key == cache_key)
            .values(
                hit_count=market_analysis_cache.c.hit_count + 1,
                last_accessed_at=utc_now(),
            )
        )
    try:
        return json.loads(zlib.decompress(row[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        with get_engine().begin() as connection:
            connection.execute(
                delete(market_analysis_cache).where(
                    market_analysis_cache.c.cache_key == cache_key
                )
            )
        return None


def save_analysis_cache(
    *,
    cache_key: str,
    symbol: str,
    timeframe: str,
    requested_count: int,
    provider: str,
    config_hash: str,
    algorithm_version: str,
    main_signature: str,
    hourly_signature: str,
    daily_signature: str,
    calculation_ms: int,
    payload: dict[str, Any],
) -> None:
    now = utc_now()
    values = {
        "cache_key": cache_key,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_count": requested_count,
        "provider": provider,
        "config_hash": config_hash,
        "algorithm_version": algorithm_version,
        "main_signature": main_signature,
        "hourly_signature": hourly_signature,
        "daily_signature": daily_signature,
        "payload_blob": zlib.compress(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            level=6,
        ),
        "calculation_ms": calculation_ms,
        "hit_count": 0,
        "last_accessed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    with get_engine().begin() as connection:
        # A new data revision supersedes old entries for the same logical request.
        connection.execute(delete(market_analysis_cache).where(
            market_analysis_cache.c.provider == provider,
            market_analysis_cache.c.symbol == symbol,
            market_analysis_cache.c.timeframe == timeframe,
            market_analysis_cache.c.requested_count == requested_count,
            market_analysis_cache.c.config_hash == config_hash,
            market_analysis_cache.c.algorithm_version == algorithm_version,
            market_analysis_cache.c.cache_key != cache_key,
        ))
        update_values = {key: value for key, value in values.items() if key not in {"cache_key", "created_at"}}
        if connection.dialect.name == "mysql":
            statement = mysql_insert(market_analysis_cache).values(**values)
            statement = statement.on_duplicate_key_update(**update_values)
        elif connection.dialect.name == "sqlite":
            statement = sqlite_insert(market_analysis_cache).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[market_analysis_cache.c.cache_key],
                set_=update_values,
            )
        else:
            connection.execute(delete(market_analysis_cache).where(
                market_analysis_cache.c.cache_key == cache_key
            ))
            statement = insert(market_analysis_cache).values(**values)
        connection.execute(statement)


def analysis_cache_stats() -> dict[str, int]:
    with get_engine().connect() as connection:
        row = connection.execute(select(
            func.count().label("entries"),
            func.coalesce(func.sum(func.length(market_analysis_cache.c.payload_blob)), 0).label("bytes"),
            func.coalesce(func.sum(market_analysis_cache.c.hit_count), 0).label("hits"),
            func.coalesce(func.sum(market_analysis_cache.c.calculation_ms), 0).label("calculation_ms"),
        )).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def clear_analysis_cache() -> int:
    with get_engine().begin() as connection:
        result = connection.execute(delete(market_analysis_cache))
    return int(result.rowcount or 0)
