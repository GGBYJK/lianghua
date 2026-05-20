from __future__ import annotations

import os
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Iterator

from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error as MySQLError


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)
load_dotenv(os.path.join(ROOT_DIR, "backend", ".env"), override=False)


@dataclass(frozen=True)
class MySQLSettings:
    host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    port: int = int(os.getenv("MYSQL_PORT", "3306"))
    user: str = os.getenv("MYSQL_USER", "root")
    password: str = os.getenv("MYSQL_PASSWORD", "123123")
    database: str = os.getenv("MYSQL_DATABASE", "lh_demo")


class WatchPoolStoreError(RuntimeError):
    pass


def _isoformat_utc(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def get_mysql_settings() -> MySQLSettings:
    return MySQLSettings()


def _connect(database: str | None = None):
    settings = get_mysql_settings()
    try:
        return mysql.connector.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            database=database,
            autocommit=False,
        )
    except MySQLError as exc:
        raise WatchPoolStoreError(f"MySQL 连接失败：{exc}") from exc


@contextmanager
def mysql_connection() -> Iterator[Any]:
    settings = get_mysql_settings()
    conn = _connect(settings.database)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_watch_pool_store() -> None:
    settings = get_mysql_settings()
    conn = _connect(None)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{settings.database}` "
            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.execute(f"USE `{settings.database}`")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_pool_items (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(80) NOT NULL,
                symbol VARCHAR(40) NOT NULL,
                timeframe VARCHAR(16) NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                monitor_minutes INT NOT NULL DEFAULT 30,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_watch_pool_symbol_timeframe (symbol, timeframe)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS head_shoulders_alerts (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                watch_pool_id BIGINT UNSIGNED NOT NULL,
                symbol VARCHAR(40) NOT NULL,
                timeframe VARCHAR(16) NOT NULL,
                pattern VARCHAR(40) NOT NULL,
                alert_type VARCHAR(40) NOT NULL,
                score INT NOT NULL DEFAULT 0,
                message TEXT NOT NULL,
                unique_key VARCHAR(255) NOT NULL,
                signal_payload LONGTEXT NOT NULL,
                chart_payload LONGTEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_head_shoulders_alert_unique_key (unique_key),
                INDEX idx_head_shoulders_alerts_symbol_timeframe (symbol, timeframe),
                INDEX idx_head_shoulders_alerts_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        conn.commit()
    except MySQLError as exc:
        conn.rollback()
        raise WatchPoolStoreError(f"MySQL 初始化失败：{exc}") from exc
    finally:
        conn.close()


def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "enabled": bool(row["enabled"]),
        "monitor_minutes": int(row["monitor_minutes"]),
        "created_at": _isoformat_utc(row.get("created_at")),
        "updated_at": _isoformat_utc(row.get("updated_at")),
    }


def list_watch_pool_items() -> list[dict[str, Any]]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, symbol, timeframe, enabled, monitor_minutes, created_at, updated_at
            FROM watch_pool_items
            ORDER BY created_at DESC, id DESC
            """
        )
        return [_row_to_item(row) for row in cursor.fetchall()]


def list_enabled_watch_pool_items() -> list[dict[str, Any]]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, symbol, timeframe, enabled, monitor_minutes, created_at, updated_at
            FROM watch_pool_items
            WHERE enabled = 1
            ORDER BY id ASC
            """
        )
        return [_row_to_item(row) for row in cursor.fetchall()]


def create_watch_pool_item(item: dict[str, Any]) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO watch_pool_items (name, symbol, timeframe, enabled, monitor_minutes)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                item["name"],
                item["symbol"],
                item["timeframe"],
                int(item["enabled"]),
                item["monitor_minutes"],
            ),
        )
        item_id = cursor.lastrowid
    return get_watch_pool_item(str(item_id))


def ensure_watch_pool_item(item: dict[str, Any]) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id
            FROM watch_pool_items
            WHERE symbol = %s AND timeframe = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (item["symbol"], item["timeframe"]),
        )
        row = cursor.fetchone()
        if row is None:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO watch_pool_items (name, symbol, timeframe, enabled, monitor_minutes)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    item["name"],
                    item["symbol"],
                    item["timeframe"],
                    int(item["enabled"]),
                    item["monitor_minutes"],
                ),
            )
            item_id = cursor.lastrowid
        else:
            item_id = row["id"]
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE watch_pool_items
                SET name = %s, monitor_minutes = %s
                WHERE id = %s
                """,
                (
                    item["name"],
                    item["monitor_minutes"],
                    item_id,
                ),
            )
    return get_watch_pool_item(str(item_id))


def get_watch_pool_item(item_id: str) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, symbol, timeframe, enabled, monitor_minutes, created_at, updated_at
            FROM watch_pool_items
            WHERE id = %s
            """,
            (item_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WatchPoolStoreError("检测池品种不存在")
        return _row_to_item(row)


def update_watch_pool_item(item_id: str, item: dict[str, Any]) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE watch_pool_items
            SET name = %s, symbol = %s, timeframe = %s, enabled = %s, monitor_minutes = %s
            WHERE id = %s
            """,
            (
                item["name"],
                item["symbol"],
                item["timeframe"],
                int(item["enabled"]),
                item["monitor_minutes"],
                item_id,
            ),
        )
        if cursor.rowcount == 0:
            raise WatchPoolStoreError("检测池品种不存在")
    return get_watch_pool_item(item_id)


def delete_watch_pool_item(item_id: str) -> None:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM watch_pool_items WHERE id = %s", (item_id,))
        if cursor.rowcount == 0:
            raise WatchPoolStoreError("检测池品种不存在")


def _row_to_alert(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "watch_pool_id": str(row["watch_pool_id"]),
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "pattern": row["pattern"],
        "alert_type": row["alert_type"],
        "score": int(row["score"]),
        "message": row["message"],
        "unique_key": row["unique_key"],
        "signal_payload": json.loads(row["signal_payload"]),
        "chart_payload": json.loads(row["chart_payload"]),
        "created_at": _isoformat_utc(row.get("created_at")),
    }


def _row_to_alert_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "watch_pool_id": str(row["watch_pool_id"]),
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "pattern": row["pattern"],
        "alert_type": row["alert_type"],
        "score": int(row["score"]),
        "message": row["message"],
        "unique_key": row["unique_key"],
        "signal_payload": json.loads(row["signal_payload"]),
        "created_at": _isoformat_utc(row.get("created_at")),
    }


def insert_head_shoulders_alert_if_new(alert: dict[str, Any]) -> bool:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT IGNORE INTO head_shoulders_alerts (
                watch_pool_id, symbol, timeframe, pattern, alert_type, score, message,
                unique_key, signal_payload, chart_payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                alert["watch_pool_id"],
                alert["symbol"],
                alert["timeframe"],
                alert["pattern"],
                alert["alert_type"],
                alert["score"],
                alert["message"],
                alert["unique_key"],
                json.dumps(alert["signal_payload"], ensure_ascii=False),
                json.dumps(alert["chart_payload"], ensure_ascii=False),
            ),
        )
        return cursor.rowcount == 1


def list_head_shoulders_alerts(
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    values: list[Any] = []
    if symbol:
        filters.append("symbol = %s")
        values.append(symbol)
    if timeframe:
        filters.append("timeframe = %s")
        values.append(timeframe)
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    values.append(max(1, min(int(limit), 500)))

    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT id, watch_pool_id, symbol, timeframe, pattern, alert_type, score, message,
                   unique_key, signal_payload, created_at
            FROM head_shoulders_alerts
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            tuple(values),
        )
        return [_row_to_alert_summary(row) for row in cursor.fetchall()]


def get_head_shoulders_alert(alert_id: str) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, watch_pool_id, symbol, timeframe, pattern, alert_type, score, message,
                   unique_key, signal_payload, chart_payload, created_at
            FROM head_shoulders_alerts
            WHERE id = %s
            """,
            (alert_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WatchPoolStoreError("监控消息不存在")
        return _row_to_alert(row)
