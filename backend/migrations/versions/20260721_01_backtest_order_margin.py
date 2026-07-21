"""Store the occupied margin for each backtest order."""

from alembic import op
import sqlalchemy as sa


revision = "20260721_01"
down_revision = "20260720_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    if "margin" not in columns:
        op.add_column(
            "backtest_orders",
            sa.Column("margin", sa.Numeric(24, 8), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    if "margin" in columns:
        op.drop_column("backtest_orders", "margin")
