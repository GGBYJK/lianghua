from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.monitor import is_in_trading_session


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
