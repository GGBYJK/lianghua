from __future__ import annotations

import os
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error as MySQLError

from .market_client import is_listed_futures_contract


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


def _utc_now_without_tz() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
                trading_sessions VARCHAR(40) NOT NULL DEFAULT 'day,night',
                min_head_to_neck_height DOUBLE NOT NULL DEFAULT 0,
                monitor_started_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_watch_pool_symbol_timeframe (symbol, timeframe)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute("SHOW COLUMNS FROM watch_pool_items LIKE 'monitor_started_at'")
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE watch_pool_items
                ADD COLUMN monitor_started_at TIMESTAMP NULL DEFAULT NULL AFTER monitor_minutes
                """
            )
        cursor.execute("SHOW COLUMNS FROM watch_pool_items LIKE 'trading_sessions'")
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE watch_pool_items
                ADD COLUMN trading_sessions VARCHAR(40) NOT NULL DEFAULT 'day,night' AFTER monitor_minutes
                """
            )
        cursor.execute("SHOW COLUMNS FROM watch_pool_items LIKE 'min_head_to_neck_height'")
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE watch_pool_items
                ADD COLUMN min_head_to_neck_height DOUBLE NOT NULL DEFAULT 0 AFTER trading_sessions
                """
            )
        cursor.execute(
            """
            UPDATE watch_pool_items
            SET monitor_started_at = updated_at
            WHERE enabled = 1 AND monitor_started_at IS NULL
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
                hidden_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_head_shoulders_alert_unique_key (unique_key),
                INDEX idx_head_shoulders_alerts_symbol_timeframe (symbol, timeframe),
                INDEX idx_head_shoulders_alerts_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute("SHOW COLUMNS FROM head_shoulders_alerts LIKE 'hidden_at'")
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE head_shoulders_alerts
                ADD COLUMN hidden_at TIMESTAMP NULL DEFAULT NULL AFTER chart_payload
                """
            )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_feedbacks (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                alert_id BIGINT UNSIGNED NOT NULL,
                symbol VARCHAR(40) NOT NULL,
                timeframe VARCHAR(16) NOT NULL,
                pattern VARCHAR(40) NOT NULL,
                alert_type VARCHAR(40) NOT NULL,
                score INT NOT NULL DEFAULT 0,
                message TEXT NOT NULL,
                unique_key VARCHAR(255) NOT NULL,
                signal_payload LONGTEXT NOT NULL,
                chart_payload LONGTEXT NOT NULL,
                feedback_note TEXT NOT NULL,
                alert_created_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_alert_feedbacks_alert_id (alert_id),
                INDEX idx_alert_feedbacks_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contract_center_items (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(40) NOT NULL,
                exchange VARCHAR(16) NOT NULL,
                name VARCHAR(80) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_contract_center_symbol (symbol),
                INDEX idx_contract_center_exchange (exchange),
                INDEX idx_contract_center_created_at (created_at)
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
        "trading_sessions": row.get("trading_sessions") or "day,night",
        "min_head_to_neck_height": float(row.get("min_head_to_neck_height") or 0),
        "monitor_started_at": _isoformat_utc(row.get("monitor_started_at")),
        "created_at": _isoformat_utc(row.get("created_at")),
        "updated_at": _isoformat_utc(row.get("updated_at")),
    }


def list_watch_pool_items() -> list[dict[str, Any]]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, symbol, timeframe, enabled, monitor_minutes, trading_sessions, min_head_to_neck_height, monitor_started_at, created_at, updated_at
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
            SELECT id, name, symbol, timeframe, enabled, monitor_minutes, trading_sessions, min_head_to_neck_height, monitor_started_at, created_at, updated_at
            FROM watch_pool_items
            WHERE enabled = 1
            ORDER BY id ASC
            """
        )
        return [_row_to_item(row) for row in cursor.fetchall()]


def enable_all_watch_pool_items() -> list[dict[str, Any]]:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE watch_pool_items
            SET enabled = 1, monitor_started_at = %s
            WHERE enabled = 0
            """,
            (_utc_now_without_tz(),),
        )
    return list_watch_pool_items()


def disable_all_watch_pool_items() -> list[dict[str, Any]]:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE watch_pool_items
            SET enabled = 0
            WHERE enabled = 1
            """
        )
    return list_watch_pool_items()


def create_watch_pool_item(item: dict[str, Any]) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO watch_pool_items (name, symbol, timeframe, enabled, monitor_minutes, trading_sessions, min_head_to_neck_height, monitor_started_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item["name"],
                item["symbol"],
                item["timeframe"],
                int(item["enabled"]),
                item["monitor_minutes"],
                item.get("trading_sessions", "day,night"),
                float(item.get("min_head_to_neck_height", 0)),
                _utc_now_without_tz() if item["enabled"] else None,
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
                INSERT INTO watch_pool_items (name, symbol, timeframe, enabled, monitor_minutes, trading_sessions, min_head_to_neck_height, monitor_started_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item["name"],
                    item["symbol"],
                    item["timeframe"],
                    int(item["enabled"]),
                    item["monitor_minutes"],
                    item.get("trading_sessions", "day,night"),
                    float(item.get("min_head_to_neck_height", 0)),
                    _utc_now_without_tz() if item["enabled"] else None,
                ),
            )
            item_id = cursor.lastrowid
        else:
            item_id = row["id"]
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE watch_pool_items
                SET name = %s, monitor_minutes = %s, trading_sessions = %s, min_head_to_neck_height = %s
                WHERE id = %s
                """,
                (
                    item["name"],
                    item["monitor_minutes"],
                    item.get("trading_sessions", "day,night"),
                    float(item.get("min_head_to_neck_height", 0)),
                    item_id,
                ),
            )
    return get_watch_pool_item(str(item_id))


def get_watch_pool_item(item_id: str) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, symbol, timeframe, enabled, monitor_minutes, trading_sessions, min_head_to_neck_height, monitor_started_at, created_at, updated_at
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
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT enabled, monitor_started_at
            FROM watch_pool_items
            WHERE id = %s
            """,
            (item_id,),
        )
        current = cursor.fetchone()
        if current is None:
            raise WatchPoolStoreError("检测池品种不存在")

        next_enabled = bool(item["enabled"])
        monitor_started_at = current.get("monitor_started_at")
        if next_enabled and not bool(current["enabled"]):
            monitor_started_at = _utc_now_without_tz()

        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE watch_pool_items
            SET name = %s, symbol = %s, timeframe = %s, enabled = %s, monitor_minutes = %s, trading_sessions = %s, min_head_to_neck_height = %s, monitor_started_at = %s
            WHERE id = %s
            """,
            (
                item["name"],
                item["symbol"],
                item["timeframe"],
                int(next_enabled),
                item["monitor_minutes"],
                item.get("trading_sessions", "day,night"),
                float(item.get("min_head_to_neck_height", 0)),
                monitor_started_at,
                item_id,
            ),
        )
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
    filters: list[str] = ["hidden_at IS NULL"]
    values: list[Any] = []
    if symbol:
        filters.append("symbol = %s")
        values.append(symbol)
    if timeframe:
        filters.append("timeframe = %s")
        values.append(timeframe)
    where_clause = f"WHERE {' AND '.join(filters)}"
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


def hide_head_shoulders_alert(alert_id: str) -> None:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE head_shoulders_alerts
            SET hidden_at = %s
            WHERE id = %s
            """,
            (_utc_now_without_tz(), alert_id),
        )
        if cursor.rowcount == 0:
            raise WatchPoolStoreError("监控消息不存在")


def _row_to_feedback(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "alert_id": str(row["alert_id"]),
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "pattern": row["pattern"],
        "alert_type": row["alert_type"],
        "score": int(row["score"]),
        "message": row["message"],
        "unique_key": row["unique_key"],
        "signal_payload": json.loads(row["signal_payload"]),
        "chart_payload": json.loads(row["chart_payload"]),
        "feedback_note": row["feedback_note"],
        "alert_created_at": _isoformat_utc(row.get("alert_created_at")),
        "created_at": _isoformat_utc(row.get("created_at")),
        "updated_at": _isoformat_utc(row.get("updated_at")),
    }


def create_alert_feedback(alert_id: str, note: str) -> dict[str, Any]:
    alert = get_head_shoulders_alert(alert_id)
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO alert_feedbacks (
                alert_id, symbol, timeframe, pattern, alert_type, score, message, unique_key,
                signal_payload, chart_payload, feedback_note, alert_created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                alert["id"],
                alert["symbol"],
                alert["timeframe"],
                alert["pattern"],
                alert["alert_type"],
                alert["score"],
                alert["message"],
                alert["unique_key"],
                json.dumps(alert["signal_payload"], ensure_ascii=False),
                json.dumps(alert["chart_payload"], ensure_ascii=False),
                note,
                alert["created_at"],
            ),
        )
        feedback_id = cursor.lastrowid
    return get_alert_feedback(str(feedback_id))


def list_alert_feedbacks(limit: int = 100) -> list[dict[str, Any]]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, alert_id, symbol, timeframe, pattern, alert_type, score, message, unique_key,
                   signal_payload, chart_payload, feedback_note, alert_created_at, created_at, updated_at
            FROM alert_feedbacks
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 500)),),
        )
        return [_row_to_feedback(row) for row in cursor.fetchall()]


def get_alert_feedback(feedback_id: str) -> dict[str, Any]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, alert_id, symbol, timeframe, pattern, alert_type, score, message, unique_key,
                   signal_payload, chart_payload, feedback_note, alert_created_at, created_at, updated_at
            FROM alert_feedbacks
            WHERE id = %s
            """,
            (feedback_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WatchPoolStoreError("反馈不存在")
        return _row_to_feedback(row)


def delete_alert_feedback(feedback_id: str) -> None:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM alert_feedbacks WHERE id = %s", (feedback_id,))
        if cursor.rowcount == 0:
            raise WatchPoolStoreError("反馈不存在")


def _contract_name_from_symbol(symbol: str) -> str:
    code = symbol.split(".", 1)[1] if "." in symbol else symbol
    return code


def _row_to_contract(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "symbol": row["symbol"],
        "exchange": row["exchange"],
        "name": row["name"],
        "created_at": _isoformat_utc(row.get("created_at")),
        "updated_at": _isoformat_utc(row.get("updated_at")),
    }


def list_contract_center_items(exchange: str | None = None) -> list[dict[str, Any]]:
    filters: list[str] = []
    values: list[Any] = []
    if exchange:
        filters.append("exchange = %s")
        values.append(exchange.upper())
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT id, symbol, exchange, name, created_at, updated_at
            FROM contract_center_items
            {where_clause}
            ORDER BY exchange ASC, symbol ASC
            """,
            tuple(values),
        )
        return [_row_to_contract(row) for row in cursor.fetchall() if is_listed_futures_contract(row["symbol"])]


def list_contract_center_symbols() -> set[str]:
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT symbol FROM contract_center_items")
        return {str(row[0]) for row in cursor.fetchall() if is_listed_futures_contract(str(row[0]))}


def insert_contract_center_items(symbols: list[str]) -> int:
    rows = []
    for symbol in symbols:
        normalized = symbol.strip()
        if not normalized or "." not in normalized or not is_listed_futures_contract(normalized):
            continue
        exchange = normalized.split(".", 1)[0].upper()
        rows.append((normalized, exchange, _contract_name_from_symbol(normalized)))
    if not rows:
        return 0
    with mysql_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT IGNORE INTO contract_center_items (symbol, exchange, name)
            VALUES (%s, %s, %s)
            """,
            rows,
        )
        return cursor.rowcount


def delete_contract_center_items_not_in_latest(exchanges: list[str], latest_symbols: list[str]) -> int:
    exchange_set = {exchange.strip().upper() for exchange in exchanges if exchange.strip()}
    latest_set = {symbol.strip() for symbol in latest_symbols if symbol.strip()}
    if not exchange_set:
        return 0
    stale_symbols: list[str] = []
    with mysql_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(exchange_set))
        cursor.execute(
            f"""
            SELECT symbol
            FROM contract_center_items
            WHERE exchange IN ({placeholders})
            """,
            tuple(exchange_set),
        )
        stale_symbols = [
            str(row[0])
            for row in cursor.fetchall()
            if str(row[0]) not in latest_set or not is_listed_futures_contract(str(row[0]))
        ]
        if not stale_symbols:
            return 0
        delete_placeholders = ",".join(["%s"] * len(stale_symbols))
        cursor.execute(
            f"""
            DELETE FROM contract_center_items
            WHERE symbol IN ({delete_placeholders})
            """,
            tuple(stale_symbols),
        )
        return cursor.rowcount


def replace_contract_center_items(exchanges: list[str], symbols: list[str]) -> tuple[int, int]:
    exchange_set = {exchange.strip().upper() for exchange in exchanges if exchange.strip()}
    if not exchange_set:
        return 0, 0
    rows = []
    for symbol in symbols:
        normalized = symbol.strip()
        if not normalized or "." not in normalized or not is_listed_futures_contract(normalized):
            continue
        exchange = normalized.split(".", 1)[0].upper()
        if exchange not in exchange_set:
            continue
        rows.append((normalized, exchange, _contract_name_from_symbol(normalized)))
    with mysql_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(exchange_set))
        cursor.execute(
            f"""
            DELETE FROM contract_center_items
            WHERE exchange IN ({placeholders})
            """,
            tuple(exchange_set),
        )
        removed = cursor.rowcount
        inserted = 0
        if rows:
            cursor.executemany(
                """
                INSERT IGNORE INTO contract_center_items (symbol, exchange, name)
                VALUES (%s, %s, %s)
                """,
                rows,
            )
            inserted = cursor.rowcount
        return inserted, removed

