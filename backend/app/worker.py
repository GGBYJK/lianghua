from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import insert, select, update

from .market_client import MarketApiError, fetch_kline_from_market, shutdown_market_clients
from .backtest_service import process_next_backtest_run
from .monitor import monitor_watch_pool_loop
from .trading_db import get_engine, init_trading_database, utc_now, worker_leases
from .trading_service import process_exit_rules_once
from .trading_store import open_lots_for_symbols, provider_symbol, upsert_market_snapshot
from .watch_pool_store import init_watch_pool_store, list_enabled_watch_pool_items


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.worker")
POLL_SECONDS = float(os.getenv("TRADING_WORKER_POLL_SECONDS", "2"))
LEASE_SECONDS = int(os.getenv("TRADING_WORKER_LEASE_SECONDS", "15"))
OWNER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def renew_lease() -> bool:
    now = utc_now()
    expires_at = now + timedelta(seconds=LEASE_SECONDS)
    with get_engine().begin() as connection:
        row = connection.execute(
            select(worker_leases).where(worker_leases.c.name == "paper-trading").with_for_update()
        ).mappings().first()
        if row is None:
            connection.execute(insert(worker_leases).values(
                name="paper-trading", owner_id=OWNER_ID, expires_at=expires_at, updated_at=now,
            ))
            return True
        if row["owner_id"] != OWNER_ID and row["expires_at"] > now:
            return False
        connection.execute(update(worker_leases).where(worker_leases.c.name == "paper-trading").values(
            owner_id=OWNER_ID, expires_at=expires_at, updated_at=now,
        ))
        return True


def watched_symbols() -> list[str]:
    symbols = set(open_lots_for_symbols())
    symbols.update(item["symbol"].strip().lower() for item in list_enabled_watch_pool_items())
    return sorted(symbol for symbol in symbols if symbol)


async def refresh_symbol(symbol: str) -> None:
    frame = await fetch_kline_from_market(provider_symbol(symbol), "1m", limit=2)
    if frame.empty:
        return
    latest = frame.iloc[-1]
    raw_time = latest.get("time")
    market_time = raw_time.to_pydatetime() if hasattr(raw_time, "to_pydatetime") else raw_time
    if getattr(market_time, "tzinfo", None) is not None:
        market_time = market_time.astimezone().replace(tzinfo=None)
    upsert_market_snapshot(symbol, Decimal(str(latest["close"])), "worker", market_time)


async def trading_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        if not renew_lease():
            logger.warning("another paper trading worker owns the lease")
        else:
            symbols = watched_symbols()
            results = await asyncio.gather(*(refresh_symbol(symbol) for symbol in symbols), return_exceptions=True)
            for symbol, result in zip(symbols, results):
                if isinstance(result, Exception):
                    logger.warning("quote refresh failed: symbol=%s error=%s", symbol, result)
            try:
                triggered = process_exit_rules_once()
                if triggered:
                    logger.info("automatic exit orders filled: count=%s", triggered)
            except Exception:
                logger.exception("automatic exit evaluation failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def backtest_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await process_next_backtest_run()
        except Exception:
            logger.exception("backtest worker iteration failed")
            processed = False
        if processed:
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


async def run() -> None:
    init_watch_pool_store()
    init_trading_database()
    stop_event = asyncio.Event()
    mode = os.getenv("WORKER_MODE", "all").strip().lower()
    tasks: list[asyncio.Task[None]] = []
    if mode in {"all", "market"}:
        tasks.append(asyncio.create_task(monitor_watch_pool_loop(stop_event), name="watch-pool-monitor"))
        tasks.append(asyncio.create_task(trading_loop(stop_event), name="paper-trading-worker"))
    if mode in {"all", "backtest"}:
        tasks.append(asyncio.create_task(backtest_loop(stop_event), name="strategy-backtest-worker"))
    if not tasks:
        raise ValueError(f"unsupported WORKER_MODE: {mode}")
    logger.info("worker started: owner=%s mode=%s", OWNER_ID, mode)
    try:
        await asyncio.gather(*tasks)
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        shutdown_market_clients()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, MarketApiError):
        logger.info("worker stopped")
