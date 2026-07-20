"""Use a version-isolated pending status for newly queued backtests."""

from alembic import op
import sqlalchemy as sa


revision = "20260720_02"
down_revision = "20260720_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "backtest_runs",
        "status",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="PENDING",
    )


def downgrade() -> None:
    op.alter_column(
        "backtest_runs",
        "status",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="QUEUED",
    )
