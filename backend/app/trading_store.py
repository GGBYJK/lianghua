from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, desc, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .security import hash_password, verify_password
from .trading_db import (
    account_ledger,
    audit_logs,
    contract_specs,
    fills,
    get_engine,
    market_snapshots,
    orders,
    paper_accounts,
    permissions,
    position_exit_rules,
    position_lots,
    refresh_sessions,
    role_permissions,
    roles,
    user_roles,
    users,
    utc_now,
)


class TradingStoreError(RuntimeError):
    pass


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def provider_symbol(symbol: str) -> str:
    value = symbol.strip()
    if "." not in value:
        return value
    exchange, contract = value.split(".", 1)
    exchange = exchange.upper()
    normalized_contract = contract.upper() if exchange in {"CZCE", "CFFEX"} else contract.lower()
    return f"{exchange}.{normalized_contract}"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _load_user(connection: Any, *, user_id: int | None = None, username: str | None = None) -> dict[str, Any] | None:
    condition = users.c.id == user_id if user_id is not None else users.c.username == username
    row = connection.execute(
        select(users, roles.c.code.label("role"), roles.c.name.label("role_name"))
        .join(user_roles, user_roles.c.user_id == users.c.id)
        .join(roles, roles.c.id == user_roles.c.role_id)
        .where(condition)
    ).mappings().first()
    if row is None:
        return None
    permission_codes = list(connection.execute(
        select(permissions.c.code)
        .join(role_permissions, role_permissions.c.permission_id == permissions.c.id)
        .join(roles, roles.c.id == role_permissions.c.role_id)
        .where(roles.c.code == row["role"])
        .order_by(permissions.c.code)
    ).scalars())
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "password_hash": row["password_hash"],
        "status": row["status"],
        "role": row["role"],
        "role_name": row["role_name"],
        "permissions": permission_codes,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in user.items() if key != "password_hash"}


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        user = _load_user(connection, username=username)
    if user is None or user["status"] != "ACTIVE" or not verify_password(user["password_hash"], password):
        return None
    return user


def get_user(user_id: int) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        return _load_user(connection, user_id=user_id)


def create_refresh_session(user_id: int, token_hash: str, expires_at: datetime) -> str:
    session_id = str(uuid4())
    with get_engine().begin() as connection:
        connection.execute(insert(refresh_sessions).values(
            id=session_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        ))
    return session_id


def consume_refresh_session(token_hash: str) -> dict[str, Any] | None:
    now = utc_now()
    with get_engine().begin() as connection:
        row = connection.execute(
            select(refresh_sessions)
            .where(and_(
                refresh_sessions.c.token_hash == token_hash,
                refresh_sessions.c.revoked_at.is_(None),
                refresh_sessions.c.expires_at > now,
            ))
            .with_for_update()
        ).mappings().first()
        if row is None:
            return None
        connection.execute(update(refresh_sessions).where(refresh_sessions.c.id == row["id"]).values(revoked_at=now))
        return _load_user(connection, user_id=int(row["user_id"]))


def revoke_refresh_session(token_hash: str) -> None:
    with get_engine().begin() as connection:
        connection.execute(update(refresh_sessions).where(
            and_(refresh_sessions.c.token_hash == token_hash, refresh_sessions.c.revoked_at.is_(None))
        ).values(revoked_at=utc_now()))


def write_audit(connection: Any, actor_user_id: int | None, action: str, target_type: str, target_id: Any, payload: dict[str, Any] | None = None) -> None:
    connection.execute(insert(audit_logs).values(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        payload=json.dumps(payload or {}, ensure_ascii=False, default=str),
    ))


def list_users() -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        ids = list(connection.execute(select(users.c.id).order_by(users.c.created_at.desc())).scalars())
        return [public_user(user) for user_id in ids if (user := _load_user(connection, user_id=int(user_id))) is not None]


def create_user(actor_user_id: int, username: str, display_name: str, password: str, role_code: str, initial_balance: Decimal) -> dict[str, Any]:
    with get_engine().begin() as connection:
        role_id = connection.execute(select(roles.c.id).where(roles.c.code == role_code)).scalar_one_or_none()
        if role_id is None:
            raise TradingStoreError("角色不存在")
        try:
            result = connection.execute(insert(users).values(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                status="ACTIVE",
            ))
        except IntegrityError as exc:
            raise TradingStoreError("用户名已存在") from exc
        user_id = int(result.inserted_primary_key[0])
        connection.execute(insert(user_roles).values(user_id=user_id, role_id=role_id))
        account_result = connection.execute(insert(paper_accounts).values(
            user_id=user_id,
            initial_balance=initial_balance,
            cash_balance=initial_balance,
        ))
        account_id = int(account_result.inserted_primary_key[0])
        connection.execute(insert(account_ledger).values(
            account_id=account_id,
            entry_type="INITIAL_BALANCE",
            amount=initial_balance,
            balance_after=initial_balance,
            description="模拟账户初始资金",
        ))
        write_audit(connection, actor_user_id, "USER_CREATE", "USER", user_id, {"role": role_code, "initial_balance": str(initial_balance)})
        user = _load_user(connection, user_id=user_id)
        if user is None:
            raise TradingStoreError("用户创建失败")
        return public_user(user)


def update_user(actor_user_id: int, user_id: int, *, display_name: str | None = None, status: str | None = None, role_code: str | None = None) -> dict[str, Any]:
    with get_engine().begin() as connection:
        if connection.execute(select(users.c.id).where(users.c.id == user_id)).first() is None:
            raise TradingStoreError("用户不存在")
        values: dict[str, Any] = {}
        if display_name is not None:
            values["display_name"] = display_name
        if status is not None:
            values["status"] = status
        if values:
            connection.execute(update(users).where(users.c.id == user_id).values(**values))
        if role_code is not None:
            role_id = connection.execute(select(roles.c.id).where(roles.c.code == role_code)).scalar_one_or_none()
            if role_id is None:
                raise TradingStoreError("角色不存在")
            connection.execute(delete(user_roles).where(user_roles.c.user_id == user_id))
            connection.execute(insert(user_roles).values(user_id=user_id, role_id=role_id))
        write_audit(connection, actor_user_id, "USER_UPDATE", "USER", user_id, {"display_name": display_name, "status": status, "role": role_code})
        user = _load_user(connection, user_id=user_id)
        if user is None:
            raise TradingStoreError("用户不存在")
        return public_user(user)


def reset_user_password(actor_user_id: int, user_id: int, password: str) -> None:
    with get_engine().begin() as connection:
        result = connection.execute(update(users).where(users.c.id == user_id).values(password_hash=hash_password(password)))
        if result.rowcount == 0:
            raise TradingStoreError("用户不存在")
        connection.execute(update(refresh_sessions).where(refresh_sessions.c.user_id == user_id).values(revoked_at=utc_now()))
        write_audit(connection, actor_user_id, "PASSWORD_RESET", "USER", user_id)


def adjust_account(actor_user_id: int, user_id: int, amount: Decimal, description: str) -> dict[str, Any]:
    with get_engine().begin() as connection:
        account = connection.execute(select(paper_accounts).where(paper_accounts.c.user_id == user_id).with_for_update()).mappings().first()
        if account is None:
            raise TradingStoreError("模拟账户不存在")
        new_balance = _decimal(account["cash_balance"]) + amount
        if new_balance < 0:
            raise TradingStoreError("调整后资金不能为负数")
        connection.execute(update(paper_accounts).where(paper_accounts.c.id == account["id"]).values(
            cash_balance=new_balance,
            initial_balance=_decimal(account["initial_balance"]) + amount,
            version=int(account["version"]) + 1,
        ))
        connection.execute(insert(account_ledger).values(
            account_id=account["id"],
            entry_type="ADMIN_ADJUSTMENT",
            amount=amount,
            balance_after=new_balance,
            reference_type="USER",
            reference_id=str(user_id),
            description=description,
        ))
        write_audit(connection, actor_user_id, "ACCOUNT_ADJUST", "USER", user_id, {"amount": str(amount), "description": description})
    return get_account_summary(user_id)


def upsert_contract_spec(actor_user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    symbol = payload["symbol"].strip().lower()
    values = {**payload, "symbol": symbol}
    with get_engine().begin() as connection:
        existing = connection.execute(select(contract_specs.c.symbol).where(contract_specs.c.symbol == symbol)).first()
        if existing is None:
            connection.execute(insert(contract_specs).values(**values))
            action = "CONTRACT_CREATE"
        else:
            connection.execute(update(contract_specs).where(contract_specs.c.symbol == symbol).values(**values))
            action = "CONTRACT_UPDATE"
        write_audit(connection, actor_user_id, action, "CONTRACT", symbol, values)
    spec = get_contract_spec(symbol)
    if spec is None:
        raise TradingStoreError("合约参数保存失败")
    return spec


def get_contract_spec(symbol: str) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        row = connection.execute(select(contract_specs).where(contract_specs.c.symbol == symbol.strip().lower())).mappings().first()
        return dict(row) if row is not None else None


def list_contract_specs() -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(select(contract_specs).order_by(contract_specs.c.symbol)).mappings()]


def upsert_market_snapshot(symbol: str, price: Decimal, source: str, market_time: datetime | None = None) -> None:
    symbol = symbol.strip().lower()
    now = utc_now()
    with get_engine().begin() as connection:
        existing = connection.execute(select(market_snapshots.c.symbol).where(market_snapshots.c.symbol == symbol)).first()
        values = {"last_price": price, "source": source, "market_time": market_time, "updated_at": now}
        if existing is None:
            connection.execute(insert(market_snapshots).values(symbol=symbol, **values))
        else:
            connection.execute(update(market_snapshots).where(market_snapshots.c.symbol == symbol).values(**values))


def get_market_snapshot(symbol: str) -> dict[str, Any] | None:
    with get_engine().connect() as connection:
        row = connection.execute(select(market_snapshots).where(market_snapshots.c.symbol == symbol.strip().lower())).mappings().first()
        return dict(row) if row is not None else None


def list_market_snapshots(symbols: list[str]) -> list[dict[str, Any]]:
    normalized = [symbol.strip().lower() for symbol in symbols if symbol.strip()]
    if not normalized:
        return []
    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(
            select(market_snapshots).where(market_snapshots.c.symbol.in_(normalized))
        ).mappings()]


def _position_rows(connection: Any, account_id: int) -> list[dict[str, Any]]:
    statement = (
        select(position_lots, market_snapshots.c.last_price, market_snapshots.c.updated_at.label("quote_updated_at"), contract_specs.c.multiplier)
        .join(contract_specs, contract_specs.c.symbol == position_lots.c.symbol)
        .outerjoin(market_snapshots, market_snapshots.c.symbol == position_lots.c.symbol)
        .where(and_(position_lots.c.account_id == account_id, position_lots.c.status == "OPEN"))
        .order_by(position_lots.c.opened_at)
    )
    result: list[dict[str, Any]] = []
    for row in connection.execute(statement).mappings():
        item = dict(row)
        latest = _decimal(item.get("last_price") or item["open_price"])
        multiplier = _decimal(item["multiplier"])
        quantity = int(item["remaining_quantity"])
        open_price = _decimal(item["open_price"])
        pnl = (latest - open_price) * quantity * multiplier
        if item["side"] == "SHORT":
            pnl = -pnl
        rules = connection.execute(select(position_exit_rules).where(
            and_(position_exit_rules.c.lot_id == item["id"], position_exit_rules.c.status == "ACTIVE")
        )).mappings().all()
        item["unrealized_pnl"] = pnl
        item["last_price"] = latest
        item["stop_price"] = next((_decimal(rule["trigger_price"]) for rule in rules if rule["rule_type"] == "STOP_LOSS"), None)
        item["take_profit_price"] = next((_decimal(rule["trigger_price"]) for rule in rules if rule["rule_type"] == "TAKE_PROFIT"), None)
        result.append(item)
    return result


def get_account_summary(user_id: int) -> dict[str, Any]:
    with get_engine().connect() as connection:
        account = connection.execute(select(paper_accounts).where(paper_accounts.c.user_id == user_id)).mappings().first()
        if account is None:
            raise TradingStoreError("模拟账户不存在")
        positions = _position_rows(connection, int(account["id"]))
        unrealized = sum((_decimal(item["unrealized_pnl"]) for item in positions), Decimal("0"))
        cash = _decimal(account["cash_balance"])
        used_margin = _decimal(account["used_margin"])
        equity = cash + unrealized
        return {
            "id": str(account["id"]),
            "user_id": str(account["user_id"]),
            "currency": account["currency"],
            "initial_balance": _decimal(account["initial_balance"]),
            "cash_balance": cash,
            "used_margin": used_margin,
            "available_funds": equity - used_margin,
            "realized_pnl": _decimal(account["realized_pnl"]),
            "unrealized_pnl": unrealized,
            "total_fees": _decimal(account["total_fees"]),
            "equity": equity,
            "status": account["status"],
            "updated_at": _iso(account["updated_at"]),
        }


def list_positions(user_id: int) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        account_id = connection.execute(select(paper_accounts.c.id).where(paper_accounts.c.user_id == user_id)).scalar_one_or_none()
        if account_id is None:
            raise TradingStoreError("模拟账户不存在")
        return _position_rows(connection, int(account_id))


def list_orders(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(orders).where(orders.c.user_id == user_id).order_by(desc(orders.c.created_at)).limit(limit)
        ).mappings()
        return [dict(row) for row in rows]


def list_ledger(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        account_id = connection.execute(select(paper_accounts.c.id).where(paper_accounts.c.user_id == user_id)).scalar_one_or_none()
        if account_id is None:
            raise TradingStoreError("模拟账户不存在")
        rows = connection.execute(
            select(account_ledger).where(account_ledger.c.account_id == account_id).order_by(desc(account_ledger.c.created_at)).limit(limit)
        ).mappings()
        return [dict(row) for row in rows]


def list_audit_logs(limit: int = 200) -> list[dict[str, Any]]:
    with get_engine().connect() as connection:
        statement = (
            select(audit_logs, users.c.username.label("actor_username"))
            .outerjoin(users, users.c.id == audit_logs.c.actor_user_id)
            .order_by(desc(audit_logs.c.created_at))
            .limit(limit)
        )
        return [dict(row) for row in connection.execute(statement).mappings()]


def account_id_for_user(connection: Any, user_id: int, *, for_update: bool = False) -> dict[str, Any] | None:
    statement = select(paper_accounts).where(paper_accounts.c.user_id == user_id)
    if for_update:
        statement = statement.with_for_update()
    row = connection.execute(statement).mappings().first()
    return dict(row) if row is not None else None


def open_lots_for_symbols() -> list[str]:
    with get_engine().connect() as connection:
        return list(connection.execute(select(position_lots.c.symbol).where(position_lots.c.status == "OPEN").distinct()).scalars())
