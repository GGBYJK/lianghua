from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .backtest_schemas import BacktestCreateRequest
from .backtest_service import build_backtest_export, process_next_backtest_run
from .backtest_store import (
    BacktestStoreError,
    create_backtest_run,
    delete_backtest_run,
    get_backtest_run,
    get_backtest_series,
    list_backtest_orders,
    list_backtest_runs,
    request_backtest_cancel,
)
from .trading_api import require_permission


router = APIRouter(prefix="/api/backtests", tags=["backtests"])


def _user_id(user: dict[str, object]) -> int:
    return int(user["id"])


def _not_found(exc: BacktestStoreError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


async def _drain_backtest_queue() -> None:
    while await process_next_backtest_run():
        pass


@router.post("")
async def create_run(
    payload: BacktestCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    run = create_backtest_run(_user_id(user), payload.model_dump())
    background_tasks.add_task(_drain_backtest_queue)
    return run


@router.get("")
def runs(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> list[dict[str, object]]:
    return list_backtest_runs(_user_id(user), limit)


@router.get("/{run_id}")
def run_detail(
    run_id: str,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    try:
        return get_backtest_run(_user_id(user), run_id)
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc


@router.get("/{run_id}/orders")
def run_orders(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    symbol: str | None = None,
    timeframe: str | None = None,
    rule_key: str | None = None,
    exit_reason: str | None = None,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    try:
        return list_backtest_orders(
            _user_id(user), run_id, page=page, page_size=page_size,
            symbol=symbol, timeframe=timeframe, rule_key=rule_key, exit_reason=exit_reason,
        )
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc


@router.get("/{run_id}/series")
def run_series(
    run_id: str,
    symbol: str,
    timeframe: str,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    try:
        return get_backtest_series(_user_id(user), run_id, symbol, timeframe)
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, bool]:
    try:
        request_backtest_cancel(_user_id(user), run_id)
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc
    return {"ok": True}


@router.delete("/{run_id}")
def remove_run(
    run_id: str,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, bool]:
    try:
        delete_backtest_run(_user_id(user), run_id)
    except BacktestStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/{run_id}/export")
def export_run(
    run_id: str,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> StreamingResponse:
    try:
        output, filename = build_backtest_export(_user_id(user), run_id)
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
