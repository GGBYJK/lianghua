from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import load_head_shoulder_config
from .csv_loader import read_csv_bytes
from .market_client import MarketApiError, fetch_kline_from_market, fetch_market_symbols, get_market_settings
from .monitor import ensure_default_watch_pool_items, monitor_watch_pool_loop, scan_watch_pool_once
from .schemas import DefaultConfigResponse, HeadShouldersAlertResponse, HeadShouldersAlertSummaryResponse, HealthResponse, ScanResponse, WatchPoolItemCreate, WatchPoolItemResponse, WatchPoolItemUpdate
from .strategy import add_macd_columns, add_ma_columns, find_pivots, prepare_chart_payload, scan_head_shoulders
from .watch_pool_store import (
    WatchPoolStoreError,
    create_watch_pool_item,
    delete_watch_pool_item,
    get_head_shoulders_alert,
    init_watch_pool_store,
    list_head_shoulders_alerts,
    list_watch_pool_items,
    update_watch_pool_item,
)


@dataclass
class SimulationSession:
    df: pd.DataFrame
    symbol: str
    timeframe: str
    config_overrides: dict[str, Any] | None
    cursor: int = 0


ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_DATA_PATH = ROOT_DIR / "sample_data" / "head_shoulders_sample.csv"


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    monitor_task: asyncio.Task[None] | None = None
    try:
        init_watch_pool_store()
        ensure_default_watch_pool_items()
        monitor_task = asyncio.create_task(monitor_watch_pool_loop(stop_event))
    except WatchPoolStoreError:
        # 数据库不可用时保留行情扫描能力；检测池接口会返回明确错误。
        pass
    try:
        yield
    finally:
        stop_event.set()
        if monitor_task is not None:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="头肩顶识别服务", version="0.2.0", lifespan=lifespan)
simulation_sessions: dict[str, SimulationSession] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def parse_overrides(config_overrides: str | None) -> dict[str, Any] | None:
    if not config_overrides:
        return None
    overrides = json.loads(config_overrides)
    if not isinstance(overrides, dict):
        raise ValueError("config_overrides 必须是 JSON 对象")
    return overrides


def build_scan_response(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    overrides: dict[str, Any] | None,
) -> ScanResponse:
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    config = load_head_shoulder_config(symbol=symbol, timeframe=timeframe, overrides=overrides)
    signals = scan_head_shoulders(df, symbol=symbol, timeframe=timeframe, config=config)
    enriched_df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_pivots(enriched_df, left=config.pivot_left, right=config.pivot_right)
    chart = prepare_chart_payload(enriched_df, pivots, signals, config)

    return ScanResponse(
        symbol=symbol,
        timeframe=timeframe,
        rows=len(df),
        start_time=df["datetime"].iloc[0].isoformat() if len(df) else None,
        end_time=df["datetime"].iloc[-1].isoformat() if len(df) else None,
        config=config.to_dict(),
        signals=[signal.to_dict() for signal in signals],
        chart=chart,
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.2.0")


@app.get("/api/config/default", response_model=DefaultConfigResponse)
def default_config(symbol: str = "rb2405", timeframe: str = "5m") -> DefaultConfigResponse:
    config = load_head_shoulder_config(symbol=symbol, timeframe=timeframe)
    return DefaultConfigResponse(symbol=symbol, timeframe=timeframe, config=config.to_dict())


@app.get("/api/market/settings")
def market_settings() -> dict[str, Any]:
    return get_market_settings()


@app.get("/api/market/symbols")
async def market_symbols(symbol_type: str = "FUTURES", symbols: str | None = None) -> dict[str, Any]:
    try:
        items = await fetch_market_symbols(symbol_type=symbol_type, symbols=symbols)
        return {"symbol_type": symbol_type.upper(), "symbols": items}
    except MarketApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"产品列表查询失败：{exc}") from exc


@app.get("/api/watch-pool", response_model=list[WatchPoolItemResponse])
def get_watch_pool() -> list[WatchPoolItemResponse]:
    try:
        return [WatchPoolItemResponse(**item) for item in list_watch_pool_items()]
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/watch-pool", response_model=WatchPoolItemResponse)
def create_watch_pool(payload: WatchPoolItemCreate) -> WatchPoolItemResponse:
    try:
        item = create_watch_pool_item(payload.model_dump())
        return WatchPoolItemResponse(**item)
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.put("/api/watch-pool/{item_id}", response_model=WatchPoolItemResponse)
def update_watch_pool(item_id: str, payload: WatchPoolItemUpdate) -> WatchPoolItemResponse:
    try:
        item = update_watch_pool_item(item_id, payload.model_dump())
        return WatchPoolItemResponse(**item)
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 503, detail=str(exc)) from exc


@app.delete("/api/watch-pool/{item_id}")
def delete_watch_pool(item_id: str) -> dict[str, Any]:
    try:
        delete_watch_pool_item(item_id)
        return {"id": item_id, "deleted": True}
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 503, detail=str(exc)) from exc


@app.get("/api/alerts", response_model=list[HeadShouldersAlertSummaryResponse])
def get_alerts(symbol: str | None = None, timeframe: str | None = None, limit: int = 100) -> list[HeadShouldersAlertSummaryResponse]:
    try:
        return [
            HeadShouldersAlertSummaryResponse(**item)
            for item in list_head_shoulders_alerts(symbol=symbol, timeframe=timeframe, limit=limit)
        ]
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/alerts/{alert_id}", response_model=HeadShouldersAlertResponse)
def get_alert(alert_id: str) -> HeadShouldersAlertResponse:
    try:
        return HeadShouldersAlertResponse(**get_head_shoulders_alert(alert_id))
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 503, detail=str(exc)) from exc


@app.post("/api/alerts/scan-once")
async def scan_alerts_once(limit: int = 420) -> dict[str, Any]:
    try:
        inserted = await scan_watch_pool_once(limit=limit)
        return {"inserted": inserted}
    except WatchPoolStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/market/scan", response_model=ScanResponse)
async def scan_market(
    symbol: str = "rb2405",
    timeframe: str = "5m",
    limit: int = 120,
    config_overrides: str | None = None,
) -> ScanResponse:
    try:
        overrides = parse_overrides(config_overrides)
        df = await fetch_kline_from_market(symbol=symbol, period=timeframe, limit=limit)
        return build_scan_response(df, symbol=symbol, timeframe=timeframe, overrides=overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MarketApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"行情扫描失败：{exc}") from exc

@app.get("/api/sample/scan", response_model=ScanResponse)
def scan_sample(symbol: str = "TEST", timeframe: str = "5m") -> ScanResponse:
    try:
        df = pd.read_csv(SAMPLE_DATA_PATH)
        return build_scan_response(df, symbol=symbol, timeframe=timeframe, overrides=None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"测试数据扫描失败：{exc}") from exc


@app.post("/api/scan", response_model=ScanResponse)
async def scan_csv(
    file: UploadFile = File(...),
    symbol: str = Form("rb2405"),
    timeframe: str = Form("5m"),
    config_overrides: str | None = Form(None),
) -> ScanResponse:
    try:
        overrides = parse_overrides(config_overrides)
        df = read_csv_bytes(await file.read())
        return build_scan_response(df, symbol=symbol, timeframe=timeframe, overrides=overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"扫描失败：{exc}") from exc


@app.post("/api/simulations/start")
async def start_simulation(
    file: UploadFile = File(...),
    symbol: str = Form("rb2405"),
    timeframe: str = Form("5m"),
    config_overrides: str | None = Form(None),
) -> dict[str, Any]:
    try:
        overrides = parse_overrides(config_overrides)
        df = read_csv_bytes(await file.read())
        if len(df) < 10:
            raise ValueError("模拟实盘至少需要 10 根K线")
        session_id = uuid4().hex
        simulation_sessions[session_id] = SimulationSession(
            df=df,
            symbol=symbol,
            timeframe=timeframe,
            config_overrides=overrides,
            cursor=0,
        )
        return {
            "session_id": session_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "total_rows": len(df),
            "start_time": df["datetime"].iloc[0].isoformat(),
            "end_time": df["datetime"].iloc[-1].isoformat(),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动模拟失败：{exc}") from exc


@app.post("/api/simulations/{session_id}/next")
def next_simulation_bar(session_id: str, bars: int = 1) -> dict[str, Any]:
    session = simulation_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="模拟会话不存在，请重新上传 CSV")

    step = max(1, min(int(bars), 50))
    session.cursor = min(len(session.df), session.cursor + step)
    current_df = session.df.iloc[: session.cursor].copy().reset_index(drop=True)
    response = build_scan_response(
        current_df,
        symbol=session.symbol,
        timeframe=session.timeframe,
        overrides=session.config_overrides,
    )

    latest_bar = None
    if session.cursor > 0:
        row = session.df.iloc[session.cursor - 1]
        latest_bar = {
            "index": session.cursor - 1,
            "time": row["datetime"].isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }

    return {
        "session_id": session_id,
        "cursor": session.cursor,
        "total_rows": len(session.df),
        "done": session.cursor >= len(session.df),
        "latest_bar": latest_bar,
        "scan": response.model_dump(),
    }


@app.post("/api/simulations/{session_id}/reset")
def reset_simulation(session_id: str) -> dict[str, Any]:
    session = simulation_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="模拟会话不存在，请重新上传 CSV")
    session.cursor = 0
    return {"session_id": session_id, "cursor": 0, "total_rows": len(session.df), "done": False}


@app.delete("/api/simulations/{session_id}")
def delete_simulation(session_id: str) -> dict[str, Any]:
    simulation_sessions.pop(session_id, None)
    return {"session_id": session_id, "deleted": True}
