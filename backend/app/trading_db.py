from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine, URL


logger = logging.getLogger("app.trading_db")
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(ROOT_DIR, ".env"), override=True)
load_dotenv(os.path.join(ROOT_DIR, "backend", ".env"), override=False)

metadata = MetaData()
money_type = Numeric(24, 8)

roles = Table(
    "roles",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("code", String(32), nullable=False, unique=True),
    Column("name", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

permissions = Table(
    "permissions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("name", String(80), nullable=False),
)

role_permissions = Table(
    "role_permissions",
    metadata,
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", BigInteger, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)

users = Table(
    "users",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("display_name", String(80), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("status", String(16), nullable=False, server_default="ACTIVE"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("updated_at", DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
)

user_roles = Table(
    "user_roles",
    metadata,
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

refresh_sessions = Table(
    "refresh_sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime, nullable=False),
    Column("revoked_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("idx_refresh_sessions_user", "user_id"),
)

audit_logs = Table(
    "audit_logs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("actor_user_id", BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("action", String(80), nullable=False),
    Column("target_type", String(40), nullable=False),
    Column("target_id", String(64), nullable=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("idx_audit_logs_created", "created_at"),
)

paper_accounts = Table(
    "paper_accounts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("currency", String(8), nullable=False, server_default="CNY"),
    Column("initial_balance", money_type, nullable=False),
    Column("cash_balance", money_type, nullable=False),
    Column("used_margin", money_type, nullable=False, server_default="0"),
    Column("realized_pnl", money_type, nullable=False, server_default="0"),
    Column("total_fees", money_type, nullable=False, server_default="0"),
    Column("status", String(16), nullable=False, server_default="ACTIVE"),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("updated_at", DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
)

account_ledger = Table(
    "account_ledger",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("account_id", BigInteger, ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False),
    Column("entry_type", String(32), nullable=False),
    Column("amount", money_type, nullable=False),
    Column("balance_after", money_type, nullable=False),
    Column("reference_type", String(32), nullable=True),
    Column("reference_id", String(64), nullable=True),
    Column("description", String(255), nullable=False, server_default=""),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("idx_account_ledger_account_created", "account_id", "created_at"),
)

contract_specs = Table(
    "contract_specs",
    metadata,
    Column("symbol", String(40), primary_key=True),
    Column("exchange", String(16), nullable=False),
    Column("name", String(80), nullable=False, server_default=""),
    Column("multiplier", money_type, nullable=False),
    Column("price_tick", money_type, nullable=False),
    Column("margin_rate", money_type, nullable=False),
    Column("fee_open_rate", money_type, nullable=False, server_default="0"),
    Column("fee_close_rate", money_type, nullable=False, server_default="0"),
    Column("fee_open_fixed", money_type, nullable=False, server_default="0"),
    Column("fee_close_fixed", money_type, nullable=False, server_default="0"),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("updated_at", DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
)

market_snapshots = Table(
    "market_snapshots",
    metadata,
    Column("symbol", String(40), primary_key=True),
    Column("last_price", money_type, nullable=False),
    Column("market_time", DateTime, nullable=True),
    Column("source", String(32), nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

orders = Table(
    "paper_orders",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("account_id", BigInteger, ForeignKey("paper_accounts.id"), nullable=False),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("signal_id", BigInteger, nullable=True),
    Column("symbol", String(40), nullable=False),
    Column("side", String(8), nullable=False),
    Column("position_effect", String(8), nullable=False),
    Column("position_side", String(8), nullable=False),
    Column("order_type", String(16), nullable=False, server_default="MARKET"),
    Column("quantity", Integer, nullable=False),
    Column("status", String(24), nullable=False),
    Column("source", String(24), nullable=False),
    Column("requested_price", money_type, nullable=True),
    Column("filled_price", money_type, nullable=True),
    Column("rejection_reason", String(255), nullable=True),
    Column("idempotency_key", String(80), nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("filled_at", DateTime, nullable=True),
    UniqueConstraint("user_id", "idempotency_key", name="uq_paper_orders_user_idempotency"),
    Index("idx_paper_orders_account_created", "account_id", "created_at"),
)

fills = Table(
    "paper_fills",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("order_id", String(36), ForeignKey("paper_orders.id", ondelete="CASCADE"), nullable=False),
    Column("price", money_type, nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("fee", money_type, nullable=False),
    Column("slippage", money_type, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

position_lots = Table(
    "position_lots",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("account_id", BigInteger, ForeignKey("paper_accounts.id"), nullable=False),
    Column("open_order_id", String(36), ForeignKey("paper_orders.id"), nullable=False),
    Column("signal_id", BigInteger, nullable=True),
    Column("symbol", String(40), nullable=False),
    Column("side", String(8), nullable=False),
    Column("open_price", money_type, nullable=False),
    Column("original_quantity", Integer, nullable=False),
    Column("remaining_quantity", Integer, nullable=False),
    Column("margin", money_type, nullable=False),
    Column("realized_pnl", money_type, nullable=False, server_default="0"),
    Column("status", String(16), nullable=False, server_default="OPEN"),
    Column("opened_at", DateTime, nullable=False, server_default=func.now()),
    Column("closed_at", DateTime, nullable=True),
    Column("version", Integer, nullable=False, server_default="0"),
    Index("idx_position_lots_account_status", "account_id", "status"),
    Index("idx_position_lots_symbol_status", "symbol", "status"),
)

position_exit_rules = Table(
    "position_exit_rules",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("lot_id", String(36), ForeignKey("position_lots.id", ondelete="CASCADE"), nullable=False),
    Column("rule_type", String(24), nullable=False),
    Column("trigger_price", money_type, nullable=False),
    Column("status", String(16), nullable=False, server_default="ACTIVE"),
    Column("oco_group_id", String(36), nullable=False),
    Column("triggered_order_id", String(36), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("updated_at", DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
    UniqueConstraint("lot_id", "rule_type", name="uq_position_exit_rule_type"),
    Index("idx_position_exit_rules_status", "status"),
)

worker_leases = Table(
    "worker_leases",
    metadata,
    Column("name", String(40), primary_key=True),
    Column("owner_id", String(64), nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)


ROLE_PERMISSIONS = {
    "ADMIN": {
        "market.read", "signals.read", "account.read", "trade.execute",
        "users.manage", "accounts.manage", "contracts.manage", "audit.read",
    },
    "TRADER": {"market.read", "signals.read", "account.read", "trade.execute"},
    "VIEWER": {"market.read", "signals.read", "account.read"},
}

PERMISSION_NAMES = {
    "market.read": "查看行情",
    "signals.read": "查看信号池",
    "account.read": "查看模拟账户",
    "trade.execute": "执行模拟交易",
    "users.manage": "管理用户与角色",
    "accounts.manage": "管理模拟资金",
    "contracts.manage": "管理合约参数",
    "audit.read": "查看审计日志",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = URL.create(
        "mysql+mysqlconnector",
        username=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123123"),
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "lh_demo"),
    )
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800, future=True)


def _seed_roles_and_permissions(connection: Any) -> None:
    existing_permissions = {row.code: row.id for row in connection.execute(select(permissions.c.id, permissions.c.code))}
    for code, name in PERMISSION_NAMES.items():
        if code not in existing_permissions:
            result = connection.execute(insert(permissions).values(code=code, name=name))
            existing_permissions[code] = result.inserted_primary_key[0]

    existing_roles = {row.code: row.id for row in connection.execute(select(roles.c.id, roles.c.code))}
    role_names = {"ADMIN": "管理员", "TRADER": "交易员", "VIEWER": "只读用户"}
    for code, name in role_names.items():
        if code not in existing_roles:
            result = connection.execute(insert(roles).values(code=code, name=name))
            existing_roles[code] = result.inserted_primary_key[0]

    for role_code, permission_codes in ROLE_PERMISSIONS.items():
        role_id = existing_roles[role_code]
        assigned = set(connection.execute(
            select(role_permissions.c.permission_id).where(role_permissions.c.role_id == role_id)
        ).scalars())
        for permission_code in permission_codes:
            permission_id = existing_permissions[permission_code]
            if permission_id not in assigned:
                connection.execute(insert(role_permissions).values(role_id=role_id, permission_id=permission_id))


def _seed_bootstrap_admin(connection: Any) -> None:
    username = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin").strip()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "admin123456")
    if connection.execute(select(users.c.id).where(users.c.username == username)).first() is not None:
        return

    from .security import hash_password

    admin_role_id = connection.execute(select(roles.c.id).where(roles.c.code == "ADMIN")).scalar_one()
    result = connection.execute(insert(users).values(
        username=username,
        display_name=os.getenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "系统管理员"),
        password_hash=hash_password(password),
        status="ACTIVE",
    ))
    user_id = result.inserted_primary_key[0]
    connection.execute(insert(user_roles).values(user_id=user_id, role_id=admin_role_id))
    initial_balance = Decimal(os.getenv("DEFAULT_INITIAL_BALANCE", "1000000"))
    account_result = connection.execute(insert(paper_accounts).values(
        user_id=user_id,
        initial_balance=initial_balance,
        cash_balance=initial_balance,
    ))
    account_id = account_result.inserted_primary_key[0]
    connection.execute(insert(account_ledger).values(
        account_id=account_id,
        entry_type="INITIAL_BALANCE",
        amount=initial_balance,
        balance_after=initial_balance,
        description="模拟账户初始资金",
    ))
    logger.warning("bootstrap administrator created: username=%s; override BOOTSTRAP_ADMIN_PASSWORD in production", username)


def init_trading_database() -> None:
    engine = get_engine()
    metadata.create_all(engine)
    with engine.begin() as connection:
        locked = connection.execute(text("SELECT GET_LOCK('lh_demo_trading_seed', 10)")).scalar() == 1
        if not locked:
            raise RuntimeError("failed to acquire trading database seed lock")
        try:
            _seed_roles_and_permissions(connection)
            _seed_bootstrap_admin(connection)
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('lh_demo_trading_seed')"))
