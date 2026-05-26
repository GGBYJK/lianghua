from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.monitor import build_wechat_workbot_content, is_in_trading_session, should_emit_signal_for_item
from app.watch_pool_store import _isoformat_utc


TZ = ZoneInfo("Asia/Shanghai")


def test_monitor_runs_during_day_and_night_trading_sessions() -> None:
    assert is_in_trading_session(datetime(2026, 5, 19, 9, 0, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 11, 30, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 13, 30, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 15, 0, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 21, 0, tzinfo=TZ))
    assert is_in_trading_session(datetime(2026, 5, 19, 23, 0, tzinfo=TZ))


def test_monitor_respects_selected_trading_session() -> None:
    assert is_in_trading_session(datetime(2026, 5, 19, 10, 0, tzinfo=TZ), "day")
    assert not is_in_trading_session(datetime(2026, 5, 19, 22, 0, tzinfo=TZ), "day")
    assert is_in_trading_session(datetime(2026, 5, 19, 22, 0, tzinfo=TZ), "night")
    assert not is_in_trading_session(datetime(2026, 5, 19, 10, 0, tzinfo=TZ), "night")


def test_monitor_skips_outside_trading_sessions() -> None:
    assert not is_in_trading_session(datetime(2026, 5, 19, 8, 59, tzinfo=TZ))
    assert not is_in_trading_session(datetime(2026, 5, 19, 11, 31, tzinfo=TZ))
    assert not is_in_trading_session(datetime(2026, 5, 19, 15, 1, tzinfo=TZ))
    assert not is_in_trading_session(datetime(2026, 5, 19, 23, 1, tzinfo=TZ))


def test_store_timestamps_are_returned_with_timezone() -> None:
    assert _isoformat_utc(datetime(2026, 5, 20, 1, 13, 6)) == "2026-05-20T01:13:06+00:00"


def test_app_lifespan_starts_and_stops_watch_pool_monitor(monkeypatch) -> None:
    from app import main

    events: list[str] = []

    async def fake_monitor(stop_event):
        events.append("started")
        await stop_event.wait()
        events.append("stopped")

    monkeypatch.setattr(main, "init_watch_pool_store", lambda: events.append("init"))
    monkeypatch.setattr(main, "monitor_watch_pool_loop", fake_monitor)
    monkeypatch.setattr(main, "shutdown_market_clients", lambda: events.append("shutdown"))

    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
        deadline = time.monotonic() + 1
        while "started" not in events and time.monotonic() < deadline:
            time.sleep(0.01)

    assert events == ["init", "started", "stopped", "shutdown"]


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
            "trading_sessions": "day,night",
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
        "trading_sessions": "day,night",
    })

    update_calls = [params for sql, params in calls if "UPDATE watch_pool_items" in sql]
    assert update_calls == [("热卷2610 5分钟", 3, "day,night", 0.0, 4)]


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


def test_enabled_watch_pool_list_filters_disabled_items(monkeypatch) -> None:
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

    assert watch_pool_store.list_enabled_watch_pool_items() == []
    assert any("WHERE enabled = 1" in sql for sql in executed_sql)


def test_enable_all_watch_pool_items_enables_closed_items(monkeypatch) -> None:
    from app import watch_pool_store

    calls: list[tuple[str, tuple[object, ...] | None]] = []

    class Cursor:
        def execute(self, sql: str, params=None) -> None:
            calls.append((sql, params))

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
    monkeypatch.setattr(watch_pool_store, "list_watch_pool_items", lambda: [])

    assert watch_pool_store.enable_all_watch_pool_items() == []
    assert any("SET enabled = 1" in sql and "WHERE enabled = 0" in sql for sql, _ in calls)


def test_disable_all_watch_pool_items_disables_enabled_items(monkeypatch) -> None:
    from app import watch_pool_store

    calls: list[tuple[str, tuple[object, ...] | None]] = []

    class Cursor:
        def execute(self, sql: str, params=None) -> None:
            calls.append((sql, params))

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
    monkeypatch.setattr(watch_pool_store, "list_watch_pool_items", lambda: [])

    assert watch_pool_store.disable_all_watch_pool_items() == []
    assert any("SET enabled = 0" in sql and "WHERE enabled = 1" in sql for sql, _ in calls)


def make_signal(**overrides):
    signal = {
        "left_shoulder": {"time": "2026-05-20T08:55:00"},
        "left_neck": {"time": "2026-05-20T08:56:00"},
        "head": {"time": "2026-05-20T08:57:00"},
        "right_neck": {"time": "2026-05-20T08:58:00"},
        "right_shoulder": {"time": "2026-05-20T08:59:00"},
        "break_time": None,
        "retest_time": None,
    }
    signal.update(overrides)
    return signal


def test_signal_before_monitor_start_is_skipped() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}

    assert not should_emit_signal_for_item(make_signal(), item)


def test_signal_with_right_shoulder_after_monitor_start_is_emitted() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}
    signal = make_signal(right_shoulder={"time": "2026-05-20T09:01:00"})

    assert should_emit_signal_for_item(signal, item)


def test_signal_with_break_after_monitor_start_is_emitted() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}
    signal = make_signal(break_time="2026-05-20T09:02:00")

    assert should_emit_signal_for_item(signal, item)


def test_wechat_workbot_content_includes_core_signal_fields() -> None:
    signal = {
        "symbol": "c0",
        "timeframe": "5m",
        "pattern": "head_shoulders_top",
        "alert_type": "right_shoulder_confirmed",
        "score": 88,
        "right_shoulder": {"time": "2026-05-20T09:01:00", "price": 3329},
    }
    assert build_wechat_workbot_content(signal, {"name": "c0"}) == "新形态：c0，5m，头肩顶，20260520 09:01"
