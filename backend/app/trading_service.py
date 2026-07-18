from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from .market_client import contract_to_variety
from .strategy import signal_direction
from .trading_db import (
    account_ledger,
    contract_specs,
    fills,
    get_engine,
    market_snapshots,
    orders,
    paper_accounts,
    position_exit_rules,
    position_lots,
    utc_now,
)
from .trading_store import (
    TradingStoreError,
    account_id_for_user,
    get_account_summary,
    get_market_snapshot,
    list_market_snapshots,
    write_audit,
    with_utc_timestamps,
)
from .watch_pool_store import get_head_shoulders_alert, list_head_shoulders_alerts


DEFAULT_SLIPPAGE_TICKS = Decimal(os.getenv("DEFAULT_SLIPPAGE_TICKS", "1"))
QUOTE_STALE_SECONDS = int(os.getenv("QUOTE_STALE_SECONDS", "30"))
MIN_DEFAULT_TAKE_PROFIT_RR = Decimal(os.getenv("MIN_DEFAULT_TAKE_PROFIT_RR", "1.5"))
SIGNAL_TRADEABLE_HOURS = int(os.getenv("SIGNAL_TRADEABLE_HOURS", "24"))
TRADING_TIMEZONE = ZoneInfo(os.getenv("MARKET_TIMEZONE", "Asia/Shanghai"))


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round_price(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        return value
    ticks = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return ticks * tick


def _signal_metrics(signal: dict[str, Any]) -> dict[str, Any]:
    metrics = signal.get("pattern_metrics")
    return metrics if isinstance(metrics, dict) else {}


def _signal_direction(pattern: str, alert_type: str) -> str:
    try:
        return signal_direction(pattern, alert_type)
    except ValueError as exc:
        raise TradingStoreError(str(exc)) from exc


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _snapshot_updated_at(snapshot: dict[str, Any] | None) -> datetime | None:
    if not snapshot:
        return None
    value = snapshot.get("updated_at")
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        return _parse_created_at(value)
    return None


def _signal_sort_key(signal: dict[str, Any]) -> tuple[datetime, int]:
    created_at = _parse_created_at(signal.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    try:
        signal_id = int(signal.get("id") or 0)
    except (TypeError, ValueError):
        signal_id = 0
    return created_at, signal_id


def build_trade_signal(alert: dict[str, Any], quote_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    signal = alert.get("signal_payload") or {}
    metrics = _signal_metrics(signal)
    direction = _signal_direction(
        str(alert["pattern"]),
        str(alert.get("alert_type") or signal.get("alert_type") or "right_shoulder_confirmed"),
    )
    entry = metrics.get("trigger_price")
    if entry is None:
        entry = signal.get("retest_price") or signal.get("break_price") or signal.get("neckline_price")
    stop = metrics.get("stop")
    target = metrics.get("target")
    rr = Decimal(str(metrics.get("rr") or 0))
    created_at = _parse_created_at(alert.get("created_at"))
    active_after = datetime.now(timezone.utc) - timedelta(hours=SIGNAL_TRADEABLE_HOURS)
    recent = created_at is not None and created_at >= active_after
    valid_prices = entry is not None and stop is not None
    quote_updated_at = _snapshot_updated_at(quote_snapshot)
    quote_fresh = bool(
        quote_updated_at
        and quote_updated_at >= datetime.now(timezone.utc) - timedelta(seconds=QUOTE_STALE_SECONDS)
    )
    tradeable = bool(recent and valid_prices and quote_fresh)
    if not recent:
        tradeable_reason = "信号已超过可交易时限"
    elif not valid_prices:
        tradeable_reason = "信号缺少有效止损价"
    elif quote_updated_at is None:
        tradeable_reason = "暂无实时行情，暂时禁止成交"
    elif not quote_fresh:
        tradeable_reason = "行情已过期，暂时禁止成交"
    else:
        tradeable_reason = None
    return {
        **alert,
        "direction": direction,
        "suggested_entry_price": entry,
        "suggested_stop_price": stop,
        "suggested_take_profit_price": target if rr >= MIN_DEFAULT_TAKE_PROFIT_RR else None,
        "suggested_target_price": target,
        "risk_reward_ratio": rr,
        "last_price": quote_snapshot.get("last_price") if quote_snapshot else None,
        "quote_updated_at": quote_updated_at.isoformat() if quote_updated_at else None,
        "quote_fresh": quote_fresh,
        "tradeable": tradeable,
        "tradeable_reason": tradeable_reason,
        "expires_at": (created_at + timedelta(hours=SIGNAL_TRADEABLE_HOURS)).isoformat() if created_at else None,
    }


def list_trade_signals(limit: int = 200, symbol: str | None = None) -> list[dict[str, Any]]:
    alerts = list_head_shoulders_alerts(symbol=symbol, limit=limit)
    snapshots = {
        snapshot["symbol"].lower(): snapshot
        for snapshot in list_market_snapshots([alert["symbol"] for alert in alerts])
    }
    signals = [build_trade_signal(alert, snapshots.get(alert["symbol"].lower())) for alert in alerts]
    return sorted(signals, key=_signal_sort_key, reverse=True)


def get_trade_signal(signal_id: str) -> dict[str, Any]:
    alert = get_head_shoulders_alert(signal_id)
    return build_trade_signal(alert, get_market_snapshot(alert["symbol"]))


def _locked_quote(connection: Any, symbol: str) -> dict[str, Any]:
    row = connection.execute(
        select(market_snapshots).where(market_snapshots.c.symbol == symbol).with_for_update()
    ).mappings().first()
    if row is None:
        raise TradingStoreError("尚未取得该合约实时行情")
    updated_at = row["updated_at"]
    if updated_at is None or updated_at < utc_now() - timedelta(seconds=QUOTE_STALE_SECONDS):
        raise TradingStoreError("行情已过期，暂时禁止成交")
    return dict(row)


def _locked_contract(connection: Any, symbol: str) -> dict[str, Any]:
    product = (contract_to_variety(symbol) or symbol).lower()
    row = connection.execute(
        select(contract_specs).where(and_(contract_specs.c.symbol == product, contract_specs.c.enabled.is_(True)))
    ).mappings().first()
    if row is None:
        raise TradingStoreError("缺少有效的合约乘数、保证金率或手续费配置")
    return dict(row)


def _fill_price(quote_price: Decimal, side: str, tick: Decimal, slippage_ticks: Decimal) -> tuple[Decimal, Decimal]:
    slippage = tick * slippage_ticks
    price = quote_price + slippage if side == "BUY" else quote_price - slippage
    return _round_price(price, tick), slippage


def _fee(price: Decimal, quantity: int, spec: dict[str, Any], effect: str) -> Decimal:
    fee_mode = spec["fee_mode"]
    fee_value = decimal_value(spec["fee_value"])
    if effect == "CLOSE_TODAY" and spec.get("fee_close_today_mode") and spec.get("fee_close_today_value") is not None:
        fee_mode = spec["fee_close_today_mode"]
        fee_value = decimal_value(spec["fee_close_today_value"])
    if fee_mode == "TURNOVER_RATE":
        return price * quantity * decimal_value(spec["multiplier"]) * fee_value
    return fee_value * quantity


def _opened_today(opened_at: datetime) -> bool:
    value = opened_at.replace(tzinfo=timezone.utc) if opened_at.tzinfo is None else opened_at
    return value.astimezone(TRADING_TIMEZONE).date() == datetime.now(TRADING_TIMEZONE).date()


def _validate_exit_prices(position_side: str, fill_price: Decimal, stop_price: Decimal | None, take_profit_price: Decimal | None) -> None:
    if position_side == "LONG":
        if stop_price is not None and stop_price >= fill_price:
            raise TradingStoreError("多仓止损价必须低于成交价")
        if take_profit_price is not None and take_profit_price <= fill_price:
            raise TradingStoreError("多仓止盈价必须高于成交价")
    else:
        if stop_price is not None and stop_price <= fill_price:
            raise TradingStoreError("空仓止损价必须高于成交价")
        if take_profit_price is not None and take_profit_price >= fill_price:
            raise TradingStoreError("空仓止盈价必须低于成交价")


def _serialize_order(connection: Any, order_id: str) -> dict[str, Any]:
    row = connection.execute(select(orders).where(orders.c.id == order_id)).mappings().one()
    lot_id = connection.execute(select(position_lots.c.id).where(position_lots.c.open_order_id == order_id)).scalar_one_or_none()
    return with_utc_timestamps({**dict(row), "position_lot_id": lot_id}, "created_at", "filled_at")


def create_open_order(
    user_id: int,
    *,
    symbol: str,
    position_side: str,
    quantity: int,
    stop_price: Decimal | None,
    take_profit_price: Decimal | None,
    idempotency_key: str,
    signal_id: int | None = None,
    source: str = "MANUAL",
) -> dict[str, Any]:
    symbol = symbol.strip().lower()
    if quantity <= 0:
        raise TradingStoreError("手数必须大于零")
    if position_side not in {"LONG", "SHORT"}:
        raise TradingStoreError("持仓方向不正确")
    side = "BUY" if position_side == "LONG" else "SELL"
    order_id = str(uuid4())
    fill_id = str(uuid4())
    lot_id = str(uuid4())
    now = utc_now()

    try:
        with get_engine().begin() as connection:
            duplicate = connection.execute(select(orders).where(and_(
                orders.c.user_id == user_id,
                orders.c.idempotency_key == idempotency_key,
            ))).mappings().first()
            if duplicate is not None:
                return _serialize_order(connection, duplicate["id"])

            account = account_id_for_user(connection, user_id, for_update=True)
            if account is None or account["status"] != "ACTIVE":
                raise TradingStoreError("模拟账户不存在或已停用")
            quote = _locked_quote(connection, symbol)
            spec = _locked_contract(connection, symbol)
            tick = decimal_value(spec["price_tick"])
            fill_price, slippage = _fill_price(decimal_value(quote["last_price"]), side, tick, DEFAULT_SLIPPAGE_TICKS)
            stop = _round_price(stop_price, tick) if stop_price is not None else None
            target = _round_price(take_profit_price, tick) if take_profit_price is not None else None
            _validate_exit_prices(position_side, fill_price, stop, target)

            multiplier = decimal_value(spec["multiplier"])
            margin = fill_price * quantity * multiplier * decimal_value(spec["margin_rate"])
            fee = _fee(fill_price, quantity, spec, "OPEN")
            available = decimal_value(account["cash_balance"]) - decimal_value(account["used_margin"])
            if available < margin + fee:
                raise TradingStoreError("可用资金不足")

            connection.execute(insert(orders).values(
                id=order_id,
                account_id=account["id"],
                user_id=user_id,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                position_effect="OPEN",
                position_side=position_side,
                quantity=quantity,
                status="FILLED",
                source=source,
                requested_price=quote["last_price"],
                filled_price=fill_price,
                idempotency_key=idempotency_key,
                filled_at=now,
            ))
            connection.execute(insert(fills).values(
                id=fill_id,
                order_id=order_id,
                price=fill_price,
                quantity=quantity,
                fee=fee,
                slippage=slippage,
            ))
            connection.execute(insert(position_lots).values(
                id=lot_id,
                account_id=account["id"],
                open_order_id=order_id,
                signal_id=signal_id,
                symbol=symbol,
                side=position_side,
                open_price=fill_price,
                original_quantity=quantity,
                remaining_quantity=quantity,
                margin=margin,
            ))

            oco_group_id = str(uuid4())
            if stop is not None:
                connection.execute(insert(position_exit_rules).values(
                    id=str(uuid4()), lot_id=lot_id, rule_type="STOP_LOSS", trigger_price=stop, oco_group_id=oco_group_id,
                ))
            if target is not None:
                connection.execute(insert(position_exit_rules).values(
                    id=str(uuid4()), lot_id=lot_id, rule_type="TAKE_PROFIT", trigger_price=target, oco_group_id=oco_group_id,
                ))

            new_cash = decimal_value(account["cash_balance"]) - fee
            new_margin = decimal_value(account["used_margin"]) + margin
            connection.execute(update(paper_accounts).where(paper_accounts.c.id == account["id"]).values(
                cash_balance=new_cash,
                used_margin=new_margin,
                total_fees=decimal_value(account["total_fees"]) + fee,
                version=int(account["version"]) + 1,
            ))
            connection.execute(insert(account_ledger).values(
                account_id=account["id"], entry_type="OPEN_FEE", amount=-fee, balance_after=new_cash,
                reference_type="ORDER", reference_id=order_id, description=f"{symbol} 开仓手续费",
            ))
            write_audit(connection, user_id, "ORDER_FILLED", "ORDER", order_id, {"source": source, "side": position_side, "quantity": quantity})
            return _serialize_order(connection, order_id)
    except IntegrityError as exc:
        raise TradingStoreError("重复订单或交易数据冲突") from exc


def create_signal_order(
    user_id: int,
    signal_id: str,
    quantity: int,
    stop_price: Decimal | None,
    take_profit_price: Decimal | None,
    idempotency_key: str,
    disable_take_profit: bool = False,
) -> dict[str, Any]:
    signal = get_trade_signal(signal_id)
    if not signal["tradeable"]:
        raise TradingStoreError(signal["tradeable_reason"] or "信号当前不可交易")
    stop = stop_price if stop_price is not None else decimal_value(signal["suggested_stop_price"])
    target = None if disable_take_profit else take_profit_price
    if not disable_take_profit and target is None and signal["suggested_take_profit_price"] is not None:
        target = decimal_value(signal["suggested_take_profit_price"])
    if stop is None:
        raise TradingStoreError("信号交易必须设置止损价")
    return create_open_order(
        user_id,
        symbol=signal["symbol"],
        position_side=signal["direction"],
        quantity=quantity,
        stop_price=stop,
        take_profit_price=target,
        idempotency_key=idempotency_key,
        signal_id=int(signal_id),
        source="SIGNAL",
    )


def _close_lot_in_transaction(
    connection: Any,
    *,
    user_id: int,
    lot: dict[str, Any],
    quantity: int,
    source: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if quantity <= 0 or quantity > int(lot["remaining_quantity"]):
        raise TradingStoreError("平仓手数超过可用持仓")
    symbol = lot["symbol"]
    side = "SELL" if lot["side"] == "LONG" else "BUY"
    quote = _locked_quote(connection, symbol)
    spec = _locked_contract(connection, symbol)
    tick = decimal_value(spec["price_tick"])
    fill_price, slippage = _fill_price(decimal_value(quote["last_price"]), side, tick, DEFAULT_SLIPPAGE_TICKS)
    fee = _fee(fill_price, quantity, spec, "CLOSE_TODAY" if _opened_today(lot["opened_at"]) else "CLOSE")
    multiplier = decimal_value(spec["multiplier"])
    open_price = decimal_value(lot["open_price"])
    pnl = (fill_price - open_price) * quantity * multiplier
    if lot["side"] == "SHORT":
        pnl = -pnl

    remaining_before = int(lot["remaining_quantity"])
    margin_before = decimal_value(lot["margin"])
    margin_release = margin_before * Decimal(quantity) / Decimal(remaining_before)
    remaining_after = remaining_before - quantity
    order_id = str(uuid4())
    now = utc_now()
    account = account_id_for_user(connection, user_id, for_update=True)
    if account is None:
        raise TradingStoreError("模拟账户不存在")

    connection.execute(insert(orders).values(
        id=order_id,
        account_id=account["id"],
        user_id=user_id,
        signal_id=lot.get("signal_id"),
        symbol=symbol,
        side=side,
        position_effect="CLOSE",
        position_side=lot["side"],
        quantity=quantity,
        status="FILLED",
        source=source,
        requested_price=quote["last_price"],
        filled_price=fill_price,
        idempotency_key=idempotency_key,
        filled_at=now,
    ))
    connection.execute(insert(fills).values(
        id=str(uuid4()), order_id=order_id, price=fill_price, quantity=quantity, fee=fee, slippage=slippage,
    ))
    connection.execute(update(position_lots).where(position_lots.c.id == lot["id"]).values(
        remaining_quantity=remaining_after,
        margin=margin_before - margin_release,
        realized_pnl=decimal_value(lot["realized_pnl"]) + pnl,
        status="CLOSED" if remaining_after == 0 else "OPEN",
        closed_at=now if remaining_after == 0 else None,
        version=int(lot["version"]) + 1,
    ))
    if remaining_after == 0:
        connection.execute(update(position_exit_rules).where(
            and_(position_exit_rules.c.lot_id == lot["id"], position_exit_rules.c.status == "ACTIVE")
        ).values(status="CANCELLED"))

    new_cash = decimal_value(account["cash_balance"]) + pnl - fee
    connection.execute(update(paper_accounts).where(paper_accounts.c.id == account["id"]).values(
        cash_balance=new_cash,
        used_margin=max(Decimal("0"), decimal_value(account["used_margin"]) - margin_release),
        realized_pnl=decimal_value(account["realized_pnl"]) + pnl,
        total_fees=decimal_value(account["total_fees"]) + fee,
        version=int(account["version"]) + 1,
    ))
    connection.execute(insert(account_ledger).values(
        account_id=account["id"], entry_type="REALIZED_PNL", amount=pnl, balance_after=decimal_value(account["cash_balance"]) + pnl,
        reference_type="ORDER", reference_id=order_id, description=f"{symbol} 平仓盈亏",
    ))
    connection.execute(insert(account_ledger).values(
        account_id=account["id"], entry_type="CLOSE_FEE", amount=-fee, balance_after=new_cash,
        reference_type="ORDER", reference_id=order_id, description=f"{symbol} 平仓手续费",
    ))
    write_audit(connection, user_id, "ORDER_FILLED", "ORDER", order_id, {"source": source, "quantity": quantity, "pnl": str(pnl)})
    return _serialize_order(connection, order_id)


def close_position_lot(user_id: int, lot_id: str, quantity: int, idempotency_key: str, source: str = "MANUAL") -> dict[str, Any]:
    with get_engine().begin() as connection:
        duplicate = connection.execute(select(orders).where(and_(
            orders.c.user_id == user_id, orders.c.idempotency_key == idempotency_key,
        ))).mappings().first()
        if duplicate is not None:
            return _serialize_order(connection, duplicate["id"])
        account = account_id_for_user(connection, user_id)
        if account is None:
            raise TradingStoreError("模拟账户不存在")
        lot = connection.execute(select(position_lots).where(and_(
            position_lots.c.id == lot_id,
            position_lots.c.account_id == account["id"],
            position_lots.c.status == "OPEN",
        )).with_for_update()).mappings().first()
        if lot is None:
            raise TradingStoreError("持仓不存在或已平仓")
        return _close_lot_in_transaction(
            connection, user_id=user_id, lot=dict(lot), quantity=quantity, source=source, idempotency_key=idempotency_key,
        )


def replace_exit_rules(user_id: int, lot_id: str, stop_price: Decimal | None, take_profit_price: Decimal | None) -> None:
    with get_engine().begin() as connection:
        account = account_id_for_user(connection, user_id)
        if account is None:
            raise TradingStoreError("模拟账户不存在")
        lot = connection.execute(select(position_lots).where(and_(
            position_lots.c.id == lot_id,
            position_lots.c.account_id == account["id"],
            position_lots.c.status == "OPEN",
        )).with_for_update()).mappings().first()
        if lot is None:
            raise TradingStoreError("持仓不存在或已平仓")
        quote = _locked_quote(connection, lot["symbol"])
        spec = _locked_contract(connection, lot["symbol"])
        tick = decimal_value(spec["price_tick"])
        stop = _round_price(stop_price, tick) if stop_price is not None else None
        target = _round_price(take_profit_price, tick) if take_profit_price is not None else None
        _validate_exit_prices(lot["side"], decimal_value(quote["last_price"]), stop, target)
        connection.execute(update(position_exit_rules).where(
            and_(position_exit_rules.c.lot_id == lot_id, position_exit_rules.c.status == "ACTIVE")
        ).values(status="CANCELLED"))
        group_id = str(uuid4())
        if stop is not None:
            _activate_exit_rule(connection, lot_id, "STOP_LOSS", stop, group_id)
        if target is not None:
            _activate_exit_rule(connection, lot_id, "TAKE_PROFIT", target, group_id)
        write_audit(connection, user_id, "EXIT_RULE_UPDATE", "POSITION_LOT", lot_id, {"stop": str(stop), "take_profit": str(target)})


def _activate_exit_rule(connection: Any, lot_id: str, rule_type: str, trigger_price: Decimal, group_id: str) -> None:
    existing_id = connection.execute(select(position_exit_rules.c.id).where(and_(
        position_exit_rules.c.lot_id == lot_id,
        position_exit_rules.c.rule_type == rule_type,
    ))).scalar_one_or_none()
    values = {
        "trigger_price": trigger_price,
        "status": "ACTIVE",
        "oco_group_id": group_id,
        "triggered_order_id": None,
    }
    if existing_id is None:
        connection.execute(insert(position_exit_rules).values(
            id=str(uuid4()), lot_id=lot_id, rule_type=rule_type, **values,
        ))
    else:
        connection.execute(update(position_exit_rules).where(position_exit_rules.c.id == existing_id).values(**values))


def _rule_triggered(rule_type: str, side: str, last_price: Decimal, trigger_price: Decimal) -> bool:
    if rule_type == "STOP_LOSS":
        return last_price <= trigger_price if side == "LONG" else last_price >= trigger_price
    return last_price >= trigger_price if side == "LONG" else last_price <= trigger_price


def execute_exit_rule(rule_id: str) -> dict[str, Any] | None:
    with get_engine().begin() as connection:
        row = connection.execute(
            select(
                position_exit_rules.c.id.label("rule_id"),
                position_exit_rules.c.rule_type,
                position_exit_rules.c.trigger_price,
                position_exit_rules.c.status.label("rule_status"),
                position_exit_rules.c.oco_group_id,
                position_lots.c.id.label("lot_id"),
                position_lots.c.account_id,
                position_lots.c.signal_id,
                position_lots.c.symbol,
                position_lots.c.side,
                position_lots.c.open_price,
                position_lots.c.remaining_quantity,
                position_lots.c.margin,
                position_lots.c.realized_pnl,
                position_lots.c.version,
                position_lots.c.status.label("lot_status"),
                paper_accounts.c.user_id,
            )
            .join(position_lots, position_lots.c.id == position_exit_rules.c.lot_id)
            .join(paper_accounts, paper_accounts.c.id == position_lots.c.account_id)
            .where(position_exit_rules.c.id == rule_id)
            .with_for_update()
        ).mappings().first()
        if row is None or row["rule_status"] != "ACTIVE" or row["lot_status"] != "OPEN":
            return None
        quote = _locked_quote(connection, row["symbol"])
        if not _rule_triggered(row["rule_type"], row["side"], decimal_value(quote["last_price"]), decimal_value(row["trigger_price"])):
            return None
        source = "AUTO_STOP" if row["rule_type"] == "STOP_LOSS" else "AUTO_TAKE_PROFIT"
        order = _close_lot_in_transaction(
            connection,
            user_id=int(row["user_id"]),
            lot={
                "id": row["lot_id"], "account_id": row["account_id"], "signal_id": row["signal_id"],
                "symbol": row["symbol"], "side": row["side"], "open_price": row["open_price"],
                "remaining_quantity": row["remaining_quantity"], "margin": row["margin"],
                "realized_pnl": row["realized_pnl"], "version": row["version"],
            },
            quantity=int(row["remaining_quantity"]),
            source=source,
            idempotency_key=f"exit:{rule_id}",
        )
        connection.execute(update(position_exit_rules).where(position_exit_rules.c.oco_group_id == row["oco_group_id"]).values(status="CANCELLED"))
        connection.execute(update(position_exit_rules).where(position_exit_rules.c.id == rule_id).values(
            status="TRIGGERED", triggered_order_id=order["id"],
        ))
        return order


def process_exit_rules_once() -> int:
    with get_engine().connect() as connection:
        rule_ids = list(connection.execute(
            select(position_exit_rules.c.id)
            .join(position_lots, position_lots.c.id == position_exit_rules.c.lot_id)
            .join(market_snapshots, market_snapshots.c.symbol == position_lots.c.symbol)
            .where(and_(position_exit_rules.c.status == "ACTIVE", position_lots.c.status == "OPEN"))
        ).scalars())
    triggered = 0
    for rule_id in rule_ids:
        if execute_exit_rule(rule_id) is not None:
            triggered += 1
    return triggered
