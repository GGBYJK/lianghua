"""Store the calculated quantity for each backtest order."""

from alembic import op
import sqlalchemy as sa


revision = "20260719_01"
down_revision = "20260718_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    if "quantity" not in columns:
        op.add_column(
            "backtest_orders",
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    if "quantity" in columns:
        op.drop_column("backtest_orders", "quantity")
