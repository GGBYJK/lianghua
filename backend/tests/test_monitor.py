from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.monitor import is_in_trading_session
from app.watch_pool_store import _isoformat_utc


TZ = ZoneInfo("Asia/Shanghai")


def test_monitor_runs_during_day_and_night_trading_sessions() -> None:
    assert is_in_trading_session(datetime(2026, 5, 19, 9, 0, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 11, 30, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 13, 30, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 15, 0, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 21, 0, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 23, 0, tzinfo=TZ))


def test_monitor_skips_outside_trading_sessions() -> None:
    assert not is_in_trading_session(datetime(2026, 5, 19, 8, 59, tzinfo=TZ))
    assert not is_in_trading_session(datetime(2026, 5, 19, 11, 31, tzinfo=TZ))
    assert not is_in_trading_session(datetime(2026, 5, 19, 15, 1, tzinfo=TZ))
    assert not is_in_trading_session(datetime(2026, 5, 19, 23, 1, tzinfo=TZ))


def test_store_timestamps_are_returned_with_timezone() -> None:
    assert _isoformat_utc(datetime(2026, 5, 20, 1, 13, 6)) == "2026-05-20T01:13:06+00:00"


def test_ensure_watch_pool_item_does_not_reenable_existing_item(monkeypatch) -> None:
    from app import watch_pool_store

    calls: list[tuple[str, tuple[object, ...] | None]] = []

    class Cursor:
        lastrowid = 99

        def __init__(self, dictionary: bool = False) -> None:
            self.dictionary = dictionary

        def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
            calls.append((sql, params))

        def fetchone(self) -> dict[str, object] | None:
            return {"id": 4}

    class Conn:
        def cursor(self, dictionary: bool = False) -> Cursor:
            return Cursor(dictionary=dictionary)

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(watch_pool_store, "_connect", lambda database=None: Conn())
    monkeypatch.setattr(
        watch_pool_store,
        "get_watch_pool_item",
        lambda item_id: {
            "id": item_id,
            "name": "热卷2610 5分钟",
            "symbol": "hc2610",
            "timeframe": "5m",
            "enabled": False,
            "monitor_minutes": 3,
            "created_at": None,
            "updated_at": None,
        },
    )

    watch_pool_store.ensure_watch_pool_item({
        "name": "热卷2610 5分钟",
        "symbol": "hc2610",
        "timeframe": "5m",
        "enabled": True,
        "monitor_minutes": 3,
    })

    update_calls = [params for sql, params in calls if "UPDATE watch_pool_items" in sql]
    assert update_calls == [("热卷2610 5分钟", 3, 4)]


def test_watch_pool_list_orders_by_created_at(monkeypatch) -> None:
    from app import watch_pool_store

    executed_sql: list[str] = []

    class Cursor:
        def execute(self, sql: str, params=None) -> None:
            executed_sql.append(sql)

        def fetchall(self) -> list[dict[str, object]]:
            return []

    class Conn:
        def cursor(self, dictionary: bool = False) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(watch_pool_store, "_connect", lambda database=None: Conn())

    assert watch_pool_store.list_watch_pool_items() == []
    assert any("ORDER BY created_at DESC, id DESC" in sql for sql in executed_sql)
