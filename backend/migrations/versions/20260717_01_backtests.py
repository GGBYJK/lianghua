"""Create strategy backtest tables."""

from alembic import op

from app.trading_db import (
    backtest_errors,
    backtest_orders,
    backtest_rule_summaries,
    backtest_runs,
    backtest_series,
)


revision = "20260717_01"
down_revision = "20260716_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in (backtest_runs, backtest_series, backtest_rule_summaries, backtest_orders, backtest_errors):
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (backtest_errors, backtest_orders, backtest_rule_summaries, backtest_series, backtest_runs):
        table.drop(bind=bind, checkfirst=True)
