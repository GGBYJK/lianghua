"""Store partial exits for scaled backtest positions."""

from alembic import op
import sqlalchemy as sa


revision = "20260721_02"
down_revision = "20260721_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    additions = {
        "partial_exit_time": sa.Column("partial_exit_time", sa.DateTime(), nullable=True),
        "partial_exit_price": sa.Column("partial_exit_price", sa.Numeric(24, 8), nullable=True),
        "partial_exit_quantity": sa.Column("partial_exit_quantity", sa.Integer(), nullable=False, server_default="0"),
        "partial_net_pnl": sa.Column("partial_net_pnl", sa.Numeric(24, 8), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("backtest_orders", column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    for name in ("partial_net_pnl", "partial_exit_quantity", "partial_exit_price", "partial_exit_time"):
        if name in columns:
            op.drop_column("backtest_orders", name)
