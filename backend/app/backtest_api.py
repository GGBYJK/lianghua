from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .backtest_schemas import (
    BacktestCreateRequest,
    BacktestSymbolGroupCreateRequest,
    BacktestSymbolGroupUpdateRequest,
)
from .backtest_service import build_backtest_export
from .backtest_store import (
    BacktestStoreError,
    create_backtest_run,
    create_backtest_symbol_group,
    delete_backtest_run,
    delete_backtest_symbol_group,
    get_backtest_run,
    get_backtest_series,
    backtest_capital_usage,
    backtest_equity_curve,
    list_backtest_orders,
    list_backtest_runs,
    list_backtest_symbol_groups,
    request_backtest_cancel,
    update_backtest_symbol_group,
)
from .trading_api import require_permission


router = APIRouter(prefix="/api/backtests", tags=["backtests"])


def _user_id(user: dict[str, object]) -> int:
    return int(user["id"])


def _not_found(exc: BacktestStoreError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.post("")
async def create_run(
    payload: BacktestCreateRequest,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    return create_backtest_run(_user_id(user), payload.model_dump())


@router.get("")
def runs(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> list[dict[str, object]]:
    return list_backtest_runs(_user_id(user), limit)


@router.get("/symbol-groups")
def symbol_groups(
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> list[dict[str, object]]:
    return list_backtest_symbol_groups(_user_id(user))


@router.post("/symbol-groups")
def create_symbol_group(
    payload: BacktestSymbolGroupCreateRequest,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    try:
        return create_backtest_symbol_group(_user_id(user), payload.model_dump())
    except BacktestStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/symbol-groups/{group_id}")
def update_symbol_group(
    group_id: str,
    payload: BacktestSymbolGroupUpdateRequest,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    try:
        return update_backtest_symbol_group(_user_id(user), group_id, payload.model_dump())
    except BacktestStoreError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 409, detail=str(exc)) from exc


@router.delete("/symbol-groups/{group_id}")
def remove_symbol_group(
    group_id: str,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, bool]:
    try:
        delete_backtest_symbol_group(_user_id(user), group_id)
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc
    return {"ok": True}


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
    page_size: int = Query(default=50, ge=1, le=5000),
    symbol: str | None = None,
    timeframe: str | None = None,
    rule_key: str | None = None,
    alert_type: str | None = None,
    summary_entry_condition: str | None = None,
    exit_reason: str | None = None,
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    try:
        return list_backtest_orders(
            _user_id(user), run_id, page=page, page_size=page_size,
            symbol=symbol, timeframe=timeframe, rule_key=rule_key, alert_type=alert_type,
            summary_entry_condition=summary_entry_condition, exit_reason=exit_reason,
        )
    except BacktestStoreError as exc:
        raise _not_found(exc) from exc


@router.get("/{run_id}/equity-curve")
def run_equity_curve(
    run_id: str,
    rule_key: str = Query(min_length=1, max_length=80),
    summary_entry_condition: str | None = Query(default=None, max_length=40),
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    return {
        "rule_key": rule_key,
        "summary_entry_condition": summary_entry_condition,
        "items": backtest_equity_curve(_user_id(user), run_id, rule_key, summary_entry_condition),
    }


@router.get("/{run_id}/capital-usage")
def run_capital_usage(
    run_id: str,
    rule_key: str = Query(min_length=1, max_length=80),
    summary_entry_condition: str | None = Query(default=None, max_length=40),
    user: dict[str, object] = Depends(require_permission("market.read")),
) -> dict[str, object]:
    return {
        "rule_key": rule_key,
        "summary_entry_condition": summary_entry_condition,
        "items": backtest_capital_usage(_user_id(user), run_id, rule_key, summary_entry_condition),
    }


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
