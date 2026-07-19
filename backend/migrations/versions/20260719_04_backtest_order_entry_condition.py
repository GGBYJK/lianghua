"""Track the independent entry-condition stream for each backtest order."""

from alembic import op
import sqlalchemy as sa


revision = "20260719_04"
down_revision = "20260719_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    if "entry_condition" not in columns:
        op.add_column(
            "backtest_orders",
            sa.Column("entry_condition", sa.String(length=40), nullable=False, server_default="mixed"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("backtest_orders")}
    if "idx_backtest_orders_run_entry_condition" not in indexes:
        op.create_index(
            "idx_backtest_orders_run_entry_condition",
            "backtest_orders",
            ["run_id", "entry_condition"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("backtest_orders")}
    if "idx_backtest_orders_run_entry_condition" in indexes:
        op.drop_index("idx_backtest_orders_run_entry_condition", table_name="backtest_orders")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_orders")}
    if "entry_condition" in columns:
        op.drop_column("backtest_orders", "entry_condition")
