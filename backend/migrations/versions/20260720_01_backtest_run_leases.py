"""Add worker ownership and heartbeat fields to backtest runs."""

from alembic import op
import sqlalchemy as sa


revision = "20260720_01"
down_revision = "20260719_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_runs")}
    if "worker_id" not in columns:
        op.add_column("backtest_runs", sa.Column("worker_id", sa.String(length=96), nullable=True))
    if "heartbeat_at" not in columns:
        op.add_column("backtest_runs", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    if "attempt_count" not in columns:
        op.add_column(
            "backtest_runs",
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("backtest_runs")}
    if "idx_backtest_runs_status_heartbeat" not in indexes:
        op.create_index(
            "idx_backtest_runs_status_heartbeat",
            "backtest_runs",
            ["status", "heartbeat_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("backtest_runs")}
    if "idx_backtest_runs_status_heartbeat" in indexes:
        op.drop_index("idx_backtest_runs_status_heartbeat", table_name="backtest_runs")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_runs")}
    for column in ("attempt_count", "heartbeat_at", "worker_id"):
        if column in columns:
            op.drop_column("backtest_runs", column)
