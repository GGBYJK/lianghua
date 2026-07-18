"""Add saved symbol groups for strategy backtests."""

from alembic import op

from app.trading_db import backtest_symbol_groups


revision = "20260718_01"
down_revision = "20260717_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    backtest_symbol_groups.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    backtest_symbol_groups.drop(bind=op.get_bind(), checkfirst=True)
