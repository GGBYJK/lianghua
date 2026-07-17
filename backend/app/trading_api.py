from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import jwt
from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .market_client import MarketApiError, contract_to_variety, fetch_kline_from_market, fetch_tqsdk_contract_details
from .product_cost_import import parse_product_cost_excel
from .security import (
    REFRESH_TOKEN_DAYS,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_refresh_token,
)
from .trading_schemas import (
    AccountAdjustmentRequest,
    ClosePositionRequest,
    ContractSpecRequest,
    ExitRuleUpdateRequest,
    LoginRequest,
    ManualOpenOrderRequest,
    PasswordResetRequest,
    SignalOpenOrderRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from .trading_service import (
    create_open_order,
    create_signal_order,
    decimal_value,
    get_trade_signal,
    list_trade_signals,
    close_position_lot,
    replace_exit_rules,
)
from .trading_store import (
    TradingStoreError,
    adjust_account,
    authenticate_user,
    consume_refresh_session,
    create_refresh_session,
    create_user,
    get_account_summary,
    get_market_snapshot,
    get_product_cost_template,
    get_user,
    list_audit_logs,
    list_contract_specs,
    list_ledger,
    list_market_snapshots,
    list_orders,
    list_positions,
    list_users,
    public_user,
    provider_symbol,
    reset_user_password,
    revoke_refresh_session,
    update_user,
    upsert_contract_spec,
    upsert_market_snapshot,
    upsert_product_cost_templates,
)
from .watch_pool_store import WatchPoolStoreError, list_contract_center_items


router = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)
REFRESH_COOKIE_NAME = "paper_refresh"


def _error(exc: Exception, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(exc))


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        payload = decode_access_token(credentials.credentials)
        user = get_user(int(payload["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效") from None
    if user is None or user["status"] != "ACTIVE":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    return user


def require_permission(permission: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def dependency(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if permission not in user["permissions"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有执行该操作的权限")
        return user
    return dependency


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=REFRESH_TOKEN_DAYS * 86400,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/api/auth",
    )


def _issue_session(response: Response, user: dict[str, Any]) -> dict[str, Any]:
    access_token, expires_in = create_access_token(user)
    refresh_token, token_hash, expires_at = create_refresh_token()
    create_refresh_session(int(user["id"]), token_hash, expires_at)
    _set_refresh_cookie(response, refresh_token)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": public_user(user),
    }


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    user = authenticate_user(payload.username.strip(), payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return _issue_session(response, user)


@router.post("/auth/refresh")
def refresh(response: Response, paper_refresh: str | None = Cookie(default=None)) -> dict[str, Any]:
    if not paper_refresh:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新凭证不存在")
    user = consume_refresh_session(hash_refresh_token(paper_refresh))
    if user is None or user["status"] != "ACTIVE":
        response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新凭证已失效")
    return _issue_session(response, user)


@router.post("/auth/logout")
def logout(response: Response, paper_refresh: str | None = Cookie(default=None)) -> dict[str, bool]:
    if paper_refresh:
        revoke_refresh_session(hash_refresh_token(paper_refresh))
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return public_user(user)


async def refresh_quote(symbol: str) -> dict[str, Any]:
    normalized = symbol.strip().lower()
    try:
        frame = await fetch_kline_from_market(provider_symbol(normalized), "1m", limit=2)
    except (MarketApiError, ValueError) as exc:
        existing = get_market_snapshot(normalized)
        if existing is not None:
            return existing
        raise TradingStoreError(f"获取 {normalized} 行情失败：{exc}") from exc
    if frame.empty:
        raise TradingStoreError(f"{normalized} 行情为空")
    latest = frame.iloc[-1]
    market_time: datetime | None = None
    raw_time = latest.get("time")
    if raw_time is not None:
        parsed = raw_time.to_pydatetime() if hasattr(raw_time, "to_pydatetime") else raw_time
        if isinstance(parsed, datetime):
            market_time = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    upsert_market_snapshot(normalized, Decimal(str(latest["close"])), "market", market_time)
    snapshot = get_market_snapshot(normalized)
    if snapshot is None:
        raise TradingStoreError("行情快照保存失败")
    return snapshot


@router.get("/trading/quotes")
async def quotes(
    symbols: str = Query(min_length=1),
    refresh_market: bool = True,
    _: dict[str, Any] = Depends(require_permission("market.read")),
) -> list[dict[str, Any]]:
    symbol_list = list(dict.fromkeys(item.strip().lower() for item in symbols.split(",") if item.strip()))[:50]
    if refresh_market:
        results = await asyncio.gather(*(refresh_quote(symbol) for symbol in symbol_list), return_exceptions=True)
        if all(isinstance(result, Exception) for result in results):
            first = next(result for result in results if isinstance(result, Exception))
            raise _error(first, 503)
    return list_market_snapshots(symbol_list)


@router.get("/trading/signals")
def signals(
    limit: int = Query(default=200, ge=1, le=500),
    symbol: str | None = None,
    _: dict[str, Any] = Depends(require_permission("signals.read")),
) -> list[dict[str, Any]]:
    try:
        return list_trade_signals(limit=limit, symbol=symbol)
    except WatchPoolStoreError as exc:
        raise _error(exc, 503) from exc


@router.get("/trading/signals/{signal_id}")
def signal_detail(signal_id: str, _: dict[str, Any] = Depends(require_permission("signals.read"))) -> dict[str, Any]:
    try:
        return get_trade_signal(signal_id)
    except (WatchPoolStoreError, TradingStoreError) as exc:
        raise _error(exc, 404) from exc


@router.get("/trading/account")
def account(user: dict[str, Any] = Depends(require_permission("account.read"))) -> dict[str, Any]:
    try:
        return get_account_summary(int(user["id"]))
    except TradingStoreError as exc:
        raise _error(exc, 404) from exc


@router.get("/trading/positions")
def positions(user: dict[str, Any] = Depends(require_permission("account.read"))) -> list[dict[str, Any]]:
    try:
        return list_positions(int(user["id"]))
    except TradingStoreError as exc:
        raise _error(exc, 404) from exc


@router.get("/trading/orders")
def order_history(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict[str, Any] = Depends(require_permission("account.read")),
) -> list[dict[str, Any]]:
    return list_orders(int(user["id"]), limit)


@router.get("/trading/ledger")
def ledger(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict[str, Any] = Depends(require_permission("account.read")),
) -> list[dict[str, Any]]:
    try:
        return list_ledger(int(user["id"]), limit)
    except TradingStoreError as exc:
        raise _error(exc, 404) from exc


@router.post("/trading/orders/open")
async def manual_open(
    payload: ManualOpenOrderRequest,
    user: dict[str, Any] = Depends(require_permission("trade.execute")),
) -> dict[str, Any]:
    try:
        await refresh_quote(payload.symbol)
        return create_open_order(
            int(user["id"]),
            symbol=payload.symbol,
            position_side=payload.position_side,
            quantity=payload.quantity,
            stop_price=payload.stop_price,
            take_profit_price=payload.take_profit_price,
            idempotency_key=payload.idempotency_key,
        )
    except (TradingStoreError, MarketApiError) as exc:
        raise _error(exc) from exc


@router.post("/trading/signals/{signal_id}/open")
async def signal_open(
    signal_id: str,
    payload: SignalOpenOrderRequest,
    user: dict[str, Any] = Depends(require_permission("trade.execute")),
) -> dict[str, Any]:
    try:
        signal = get_trade_signal(signal_id)
        await refresh_quote(signal["symbol"])
        return create_signal_order(
            int(user["id"]), signal_id, payload.quantity, payload.stop_price,
            payload.take_profit_price, payload.idempotency_key, payload.disable_take_profit,
        )
    except (TradingStoreError, WatchPoolStoreError, MarketApiError) as exc:
        raise _error(exc) from exc


@router.post("/trading/positions/{lot_id}/close")
async def close_position(
    lot_id: str,
    payload: ClosePositionRequest,
    user: dict[str, Any] = Depends(require_permission("trade.execute")),
) -> dict[str, Any]:
    try:
        owned = next((item for item in list_positions(int(user["id"])) if item["id"] == lot_id), None)
        if owned is None:
            raise TradingStoreError("持仓不存在")
        await refresh_quote(owned["symbol"])
        return close_position_lot(int(user["id"]), lot_id, payload.quantity, payload.idempotency_key)
    except (TradingStoreError, MarketApiError) as exc:
        raise _error(exc) from exc


@router.put("/trading/positions/{lot_id}/exit-rules")
def update_exit_rules(
    lot_id: str,
    payload: ExitRuleUpdateRequest,
    user: dict[str, Any] = Depends(require_permission("trade.execute")),
) -> dict[str, bool]:
    try:
        replace_exit_rules(int(user["id"]), lot_id, payload.stop_price, payload.take_profit_price)
        return {"ok": True}
    except TradingStoreError as exc:
        raise _error(exc) from exc


@router.get("/trading/contracts")
def contract_spec_list(_: dict[str, Any] = Depends(require_permission("market.read"))) -> list[dict[str, Any]]:
    return list_contract_specs()


@router.get("/trading/products")
def product_catalog(_: dict[str, Any] = Depends(require_permission("market.read"))) -> list[dict[str, str]]:
    products: dict[str, dict[str, str]] = {}
    for item in list_contract_center_items():
        product = contract_to_variety(item["symbol"])
        if product is None:
            continue
        normalized = product.lower()
        products.setdefault(normalized, {
            "symbol": normalized,
            "exchange": item["exchange"],
            "name": product.split(".", 1)[1],
            "representative_symbol": item["symbol"],
        })
    return [products[symbol] for symbol in sorted(products)]


@router.get("/trading/products/details")
def product_details(
    symbol: str = Query(min_length=1, max_length=40),
    _: dict[str, Any] = Depends(require_permission("market.read")),
) -> dict[str, Any]:
    try:
        details = fetch_tqsdk_contract_details(symbol)
        template = get_product_cost_template(details["symbol"])
        if template is None:
            return details
        return {
            **details,
            "name": template["name"] or details["name"],
            "margin_rate": template["margin_rate"],
            "fee_mode": template["fee_mode"],
            "fee_value": template["fee_value"],
            "fee_close_today_mode": template["fee_close_today_mode"],
            "fee_close_today_value": template["fee_close_today_value"],
            "fee_description": template["fee_description"],
        }
    except MarketApiError as exc:
        raise _error(exc) from exc


@router.post("/admin/product-costs/import")
async def import_product_costs(
    file: UploadFile = File(...),
    _: dict[str, Any] = Depends(require_permission("contracts.manage")),
) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的 Excel 文件")
    items, issues = parse_product_cost_excel(await file.read())
    imported = upsert_product_cost_templates(items)
    return {
        "imported": imported,
        "errors": [{"row": issue.row, "reason": issue.reason} for issue in issues],
    }


@router.put("/admin/contracts/{symbol}")
def save_contract(
    symbol: str,
    payload: ContractSpecRequest,
    user: dict[str, Any] = Depends(require_permission("contracts.manage")),
) -> dict[str, Any]:
    try:
        values = payload.model_dump()
        values["symbol"] = symbol
        return upsert_contract_spec(int(user["id"]), values)
    except TradingStoreError as exc:
        raise _error(exc) from exc


@router.get("/admin/users")
def users_list(_: dict[str, Any] = Depends(require_permission("users.manage"))) -> list[dict[str, Any]]:
    return list_users()


@router.post("/admin/users")
def add_user(payload: UserCreateRequest, user: dict[str, Any] = Depends(require_permission("users.manage"))) -> dict[str, Any]:
    try:
        return create_user(
            int(user["id"]), payload.username, payload.display_name, payload.password, payload.role, payload.initial_balance,
        )
    except TradingStoreError as exc:
        raise _error(exc) from exc


@router.patch("/admin/users/{user_id}")
def edit_user(
    user_id: int,
    payload: UserUpdateRequest,
    user: dict[str, Any] = Depends(require_permission("users.manage")),
) -> dict[str, Any]:
    try:
        return update_user(
            int(user["id"]), user_id, display_name=payload.display_name, status=payload.status, role_code=payload.role,
        )
    except TradingStoreError as exc:
        raise _error(exc, 404) from exc


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    user: dict[str, Any] = Depends(require_permission("users.manage")),
) -> dict[str, bool]:
    try:
        reset_user_password(int(user["id"]), user_id, payload.password)
        return {"ok": True}
    except TradingStoreError as exc:
        raise _error(exc, 404) from exc


@router.post("/admin/users/{user_id}/account-adjustment")
def admin_adjust_account(
    user_id: int,
    payload: AccountAdjustmentRequest,
    user: dict[str, Any] = Depends(require_permission("accounts.manage")),
) -> dict[str, Any]:
    try:
        return adjust_account(int(user["id"]), user_id, payload.amount, payload.description)
    except TradingStoreError as exc:
        raise _error(exc) from exc


@router.get("/admin/audit-logs")
def audit_log_list(
    limit: int = Query(default=200, ge=1, le=1000),
    _: dict[str, Any] = Depends(require_permission("audit.read")),
) -> list[dict[str, Any]]:
    return list_audit_logs(limit)
