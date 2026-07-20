from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app import backtest_store
from app.backtest_store import (
    _retry_database_write,
    claim_next_backtest_run,
    recover_stale_backtest_runs,
    request_backtest_cancel,
    touch_backtest_heartbeat,
)
from app.trading_db import backtest_runs, backtest_series, metadata, utc_now


@pytest.fixture()
def store_engine(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    monkeypatch.setattr(backtest_store, "get_engine", lambda: engine)
    yield engine
    engine.dispose()


def add_run(engine, run_id: str, *, status: str, age_seconds: int = 0, **values) -> None:
    timestamp = utc_now() - timedelta(seconds=age_seconds)
    payload = {
        "id": run_id,
        "user_id": 7,
        "name": run_id,
        "status": status,
        "request_json": '{"symbols":["DCE.a2609"],"timeframes":["5m"]}',
        "total_combinations": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        **values,
    }
    with engine.begin() as connection:
        connection.execute(insert(backtest_runs).values(**payload))


def run_row(engine, run_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        return dict(connection.execute(
            select(backtest_runs).where(backtest_runs.c.id == run_id)
        ).mappings().one())


def test_claim_assigns_worker_and_heartbeat(store_engine) -> None:
    add_run(store_engine, "legacy", status="QUEUED", age_seconds=10)
    add_run(store_engine, "queued", status="PENDING")

    claimed = claim_next_backtest_run("worker-1")

    assert claimed is not None
    assert claimed["id"] == "queued"
    assert claimed["worker_id"] == "worker-1"
    assert claimed["attempt_count"] == 1
    row = run_row(store_engine, "queued")
    assert row["status"] == "RUNNING"
    assert row["heartbeat_at"] is not None
    assert touch_backtest_heartbeat("queued", "worker-1") is True
    assert touch_backtest_heartbeat("queued", "worker-2") is False
    assert run_row(store_engine, "legacy")["status"] == "QUEUED"


def test_recovery_cancels_requeues_and_caps_retries(store_engine) -> None:
    add_run(
        store_engine,
        "cancelled",
        status="RUNNING",
        age_seconds=300,
        heartbeat_at=utc_now() - timedelta(seconds=300),
        cancel_requested=True,
        worker_id="dead-1",
        attempt_count=1,
    )
    add_run(
        store_engine,
        "retry",
        status="RUNNING",
        age_seconds=300,
        heartbeat_at=utc_now() - timedelta(seconds=300),
        worker_id="dead-2",
        attempt_count=1,
        progress=50,
        completed_combinations=1,
    )
    add_run(
        store_engine,
        "failed",
        status="RUNNING",
        age_seconds=300,
        heartbeat_at=utc_now() - timedelta(seconds=300),
        worker_id="dead-3",
        attempt_count=3,
    )
    add_run(
        store_engine,
        "fresh",
        status="RUNNING",
        heartbeat_at=utc_now(),
        worker_id="live",
        attempt_count=1,
    )
    with store_engine.begin() as connection:
        connection.execute(insert(backtest_series).values(
            id="partial",
            run_id="retry",
            symbol="DCE.a2609",
            timeframe="5m",
            row_count=1,
            payload_blob=b"partial",
        ))

    result = recover_stale_backtest_runs(stale_seconds=60, max_attempts=3)

    assert result == {"requeued": 1, "cancelled": 1, "failed": 1}
    assert run_row(store_engine, "cancelled")["status"] == "CANCELLED"
    retry = run_row(store_engine, "retry")
    assert retry["status"] == "PENDING"
    assert retry["progress"] == 0
    assert retry["worker_id"] is None
    assert run_row(store_engine, "failed")["status"] == "FAILED"
    assert run_row(store_engine, "fresh")["status"] == "RUNNING"
    with store_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(backtest_series)).scalar_one() == 0


def test_cancel_finishes_stale_run_but_marks_live_run_pending(store_engine) -> None:
    add_run(
        store_engine,
        "stale",
        status="RUNNING",
        age_seconds=300,
        heartbeat_at=utc_now() - timedelta(seconds=300),
        worker_id="dead",
    )
    add_run(
        store_engine,
        "live",
        status="RUNNING",
        heartbeat_at=utc_now(),
        worker_id="worker",
    )

    request_backtest_cancel(7, "stale", stale_seconds=60)
    request_backtest_cancel(7, "live", stale_seconds=60)

    stale = run_row(store_engine, "stale")
    live = run_row(store_engine, "live")
    assert stale["status"] == "CANCELLED"
    assert stale["progress"] == 100
    assert live["status"] == "RUNNING"
    assert live["cancel_requested"] is True


def test_database_write_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class Engine:
        disposed = 0

        def dispose(self) -> None:
            self.disposed += 1

    engine = Engine()
    monkeypatch.setattr(backtest_store, "get_engine", lambda: engine)
    monkeypatch.setattr(backtest_store, "BACKTEST_DB_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(backtest_store, "BACKTEST_DB_RETRY_DELAY", 0)

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError("write", {}, Exception("temporary"))
        return "ok"

    assert _retry_database_write("test", operation) == "ok"
    assert attempts == 3
    assert engine.disposed == 2
