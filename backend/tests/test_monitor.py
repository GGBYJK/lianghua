from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.alert_keys import build_signal_unique_key
from app.monitor import build_watch_pool_config_overrides, build_wechat_workbot_content, is_in_trading_session, should_emit_signal_for_item
from app.watch_pool_store import (
    _alert_beats_existing_head_score,
    _alert_structure_exists,
    _deduplicate_alert_summaries,
    _refresh_existing_alert_score_if_missing,
    _signal_has_score_details,
    _isoformat_utc,
)


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


def test_alert_summary_list_collapses_repeated_structure_updates() -> None:
    base_signal = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "left_shoulder": {"time": "2026-06-02T21:15:00"},
        "left_neck": {"time": "2026-06-02T21:24:00"},
        "head": {"time": "2026-06-02T21:33:00"},
        "right_neck": {"time": "2026-06-02T21:45:00"},
        "right_shoulder": {"time": "2026-06-02T21:52:35"},
        "score": 78,
        "trend_label": "多头趋势",
    }
    newer_alert = {
        "id": "2",
        "unique_key": "newer",
        "signal_payload": {**base_signal, "right_shoulder": {"time": "2026-06-02T22:04:53"}},
    }
    older_alert = {
        "id": "1",
        "unique_key": "older",
        "signal_payload": base_signal,
    }
    breakout_alert = {
        "id": "3",
        "unique_key": "breakout",
        "signal_payload": {**base_signal, "alert_type": "neckline_break", "break_time": "2026-06-02T22:10:00"},
    }

    assert _deduplicate_alert_summaries([newer_alert, older_alert, breakout_alert]) == [newer_alert, breakout_alert]


def test_signal_unique_key_uses_head_position_not_score() -> None:
    signal = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "head": {"time": "2026-06-02T21:33:00"},
        "score": 78,
    }

    assert build_signal_unique_key(signal) == build_signal_unique_key({**signal, "score": 88})


def test_alert_structure_exists_matches_legacy_unique_key_rows() -> None:
    base_signal = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "left_shoulder": {"time": "2026-06-02T21:15:00"},
        "left_neck": {"time": "2026-06-02T21:24:00"},
        "head": {"time": "2026-06-02T21:33:00"},
        "right_neck": {"time": "2026-06-02T21:45:00"},
        "right_shoulder": {"time": "2026-06-02T21:52:35"},
        "score": 78,
        "trend_label": "多头趋势",
    }
    inserted_signal = {
        **base_signal,
        "right_shoulder": {"time": "2026-06-02T22:04:53"},
    }
    inserted_alert = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "signal_payload": inserted_signal,
    }

    class Cursor:
        def __init__(self) -> None:
            self.params = None

        def execute(self, sql, params) -> None:
            self.params = params

        def fetchall(self):
            return [
                {
                    "unique_key": (
                        "CZCE.SA609|3m|inverse_head_shoulders|right_shoulder_confirmed|"
                        "2026-06-02T21:15:00|2026-06-02T21:33:00|"
                        "2026-06-02T21:52:35|2026-06-02T21:52:35"
                    ),
                    "signal_payload": json.dumps(base_signal),
                }
            ]

    cursor = Cursor()

    assert _alert_structure_exists(cursor, inserted_alert)
    assert cursor.params == ("CZCE.SA609", "3m", "inverse_head_shoulders")


def test_same_head_same_timeframe_alert_requires_higher_pattern_score() -> None:
    existing_signal = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "head": {"time": "2026-06-02T21:33:00", "price": 4644},
        "pattern_score": 82,
    }
    new_alert = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "signal_payload": {
            **existing_signal,
            "pattern_score": 82,
        },
    }

    class Cursor:
        def execute(self, sql, params) -> None:
            self.params = params

        def fetchall(self):
            return [{"signal_payload": json.dumps(existing_signal), "unique_key": "existing"}]

    cursor = Cursor()

    assert not _alert_beats_existing_head_score(cursor, new_alert)
    assert cursor.params == ("CZCE.SA609", "3m", "inverse_head_shoulders")

    new_alert["signal_payload"]["pattern_score"] = 83
    assert _alert_beats_existing_head_score(cursor, new_alert)


def test_same_head_pullback_alert_is_only_inserted_once() -> None:
    existing_signal = {
        "symbol": "DCE.a2609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "inverse_head_shoulders_pullback",
        "head": {"time": "2026-06-22T09:51:00", "price": 4673},
        "pattern_score": 90,
    }
    new_alert = {
        "symbol": "DCE.a2609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "inverse_head_shoulders_pullback",
        "signal_payload": {
            **existing_signal,
            "pattern_score": 95,
        },
    }

    class Cursor:
        def execute(self, sql, params) -> None:
            self.params = params

        def fetchall(self):
            return [{"signal_payload": json.dumps(existing_signal), "unique_key": "existing"}]

    cursor = Cursor()

    assert not _alert_beats_existing_head_score(cursor, new_alert)
    assert cursor.params == ("DCE.a2609", "3m", "inverse_head_shoulders")


def test_same_head_different_timeframe_does_not_share_pattern_score_gate() -> None:
    existing_signal = {
        "symbol": "CZCE.SA609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "head": {"time": "2026-06-02T21:33:00", "price": 4644},
        "pattern_score": 82,
    }
    new_alert = {
        "symbol": "CZCE.SA609",
        "timeframe": "5m",
        "pattern": "inverse_head_shoulders",
        "signal_payload": {
            **existing_signal,
            "timeframe": "5m",
            "pattern_score": 82,
        },
    }

    class Cursor:
        def execute(self, sql, params) -> None:
            self.params = params

        def fetchall(self):
            return [{"signal_payload": json.dumps(existing_signal), "unique_key": "existing"}]

    cursor = Cursor()

    assert _alert_beats_existing_head_score(cursor, new_alert)
    assert cursor.params == ("CZCE.SA609", "5m", "inverse_head_shoulders")


def test_same_head_score_gate_is_separate_per_pattern() -> None:
    existing_signal = {
        "symbol": "SHFE.sp2609",
        "timeframe": "3m",
        "pattern": "head_shoulders_top",
        "head": {"time": "2026-06-02T21:33:00", "price": 4908},
        "pattern_score": 90,
    }
    new_alert = {
        "symbol": "SHFE.sp2609",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "signal_payload": {
            "symbol": "SHFE.sp2609",
            "timeframe": "3m",
            "pattern": "inverse_head_shoulders",
            "head": {"time": "2026-06-02T21:33:00", "price": 4908},
            "pattern_score": 70,
        },
    }

    class Cursor:
        def execute(self, sql, params) -> None:
            self.params = params

        def fetchall(self):
            return [{"signal_payload": json.dumps(existing_signal), "unique_key": "existing"}]

    cursor = Cursor()

    assert _alert_beats_existing_head_score(cursor, new_alert)
    assert cursor.params == ("SHFE.sp2609", "3m", "inverse_head_shoulders")


def test_score_detail_detection_handles_new_and_legacy_payloads() -> None:
    assert _signal_has_score_details({"reasons": ["小时线评分：32/50"]})
    assert _signal_has_score_details({"reasons": ["Daily timeframe score: 28/50"]})
    assert not _signal_has_score_details({"reasons": ["头部高于左右肩", "右肩已确认"]})


def test_existing_unscored_alert_is_refreshed_without_duplicate_insert() -> None:
    executed: list[tuple[str, tuple[object, ...]]] = []
    existing = {
        "id": 9,
        "signal_payload": json.dumps({"reasons": ["头部高于左右肩"]}),
    }
    alert = {
        "score": 82,
        "message": "scored",
        "signal_payload": {"reasons": ["头部高于左右肩", "小时线评分：42/50"]},
        "chart_payload": {"candles": []},
    }

    class Cursor:
        def execute(self, sql, params) -> None:
            executed.append((sql, params))

    _refresh_existing_alert_score_if_missing(Cursor(), existing, alert)

    assert len(executed) == 1
    assert "UPDATE head_shoulders_alerts" in executed[0][0]
    assert executed[0][1][0] == 82
    assert executed[0][1][-1] == 9


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
    assert update_calls == [("热卷2610 5分钟", 3, "day,night", 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 4)]


def test_watch_pool_config_overrides_include_optional_price_gap_thresholds() -> None:
    assert build_watch_pool_config_overrides({
        "min_head_to_neck_height": 8,
        "min_shoulder_to_neck_height": 4,
    }) == {
        "min_head_to_neck_height": 8.0,
        "min_shoulder_to_neck_height": 4.0,
    }
    assert build_watch_pool_config_overrides({
        "min_head_to_neck_height": 0,
        "min_shoulder_to_neck_height": 0,
    }) is None
    assert build_watch_pool_config_overrides({
        "enable_key_zone_trend_score": True,
        "resistance_zone_min": 3500,
        "resistance_zone_max": 3520,
        "support_zone_min": 3300,
        "support_zone_max": 3320,
    }) == {
        "enable_key_zone_trend_score": True,
        "resistance_zone_min": 3500.0,
        "resistance_zone_max": 3520.0,
        "support_zone_min": 3300.0,
        "support_zone_max": 3320.0,
    }


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

    assert not should_emit_signal_for_item(make_signal(), item, now=datetime(2026, 5, 20, 9, 0, tzinfo=TZ))


def test_signal_with_right_shoulder_after_monitor_start_is_emitted() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}
    signal = make_signal(right_shoulder={"time": "2026-05-20T09:01:00"})

    assert should_emit_signal_for_item(signal, item, now=datetime(2026, 5, 20, 9, 2, tzinfo=TZ))


def test_signal_with_break_after_monitor_start_is_emitted() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}
    signal = make_signal(break_time="2026-05-20T09:02:00")

    assert should_emit_signal_for_item(signal, item, now=datetime(2026, 5, 20, 9, 3, tzinfo=TZ))


def test_previous_day_signal_is_not_emitted_on_current_watch_day() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}
    now = datetime(2026, 5, 26, 14, 30, tzinfo=TZ)

    assert not should_emit_signal_for_item(
        make_signal(
            right_shoulder={"time": "2026-05-25T11:02:00"},
            break_time=None,
            retest_time=None,
        ),
        item,
        now=now,
    )
    assert not should_emit_signal_for_item(
        make_signal(
            right_shoulder={"time": "2026-05-25T22:50:00"},
            break_time=None,
            retest_time=None,
        ),
        item,
        now=now,
    )


def test_current_day_signal_is_emitted_on_current_watch_day() -> None:
    item = {"monitor_started_at": "2026-05-20T01:00:00+00:00"}
    signal = make_signal(right_shoulder={"time": "2026-05-26T14:30:00"})

    assert should_emit_signal_for_item(signal, item, now=datetime(2026, 5, 26, 14, 31, tzinfo=TZ))


def test_day_session_signal_is_not_emitted_during_night_session() -> None:
    item = {"monitor_started_at": "2026-05-27T00:00:00+00:00", "trading_sessions": "day,night"}
    signal = make_signal(right_shoulder={"time": "2026-05-27T09:18:00"})

    assert not should_emit_signal_for_item(signal, item, now=datetime(2026, 5, 27, 22, 31, tzinfo=TZ))


def test_night_session_signal_is_emitted_during_night_session() -> None:
    item = {"monitor_started_at": "2026-05-27T00:00:00+00:00", "trading_sessions": "day,night"}
    signal = make_signal(right_shoulder={"time": "2026-05-27T22:09:00"})

    assert should_emit_signal_for_item(signal, item, now=datetime(2026, 5, 27, 22, 31, tzinfo=TZ))


def test_wechat_workbot_content_includes_core_signal_fields() -> None:
    signal = {
        "symbol": "SHFE.hc2610",
        "timeframe": "3m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "score": 29,
        "right_shoulder": {"time": "2026-06-05T09:06:00", "price": 3329},
    }
    assert (
        build_wechat_workbot_content(signal, {"name": "SHFE.hc2610"})
        == "新形态：SHFE.hc2610，3m，反向头肩，20260605 09:06，29分，空头趋势下震荡"
    )
