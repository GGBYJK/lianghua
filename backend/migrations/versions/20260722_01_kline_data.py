"""Create maintained K-line datasets and synchronization jobs."""

from alembic import op

from app.trading_db import kline_bars, kline_datasets, kline_sync_jobs


revision = "20260722_01"
down_revision = "20260721_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in (kline_datasets, kline_bars, kline_sync_jobs):
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (kline_sync_jobs, kline_bars, kline_datasets):
        table.drop(bind=bind, checkfirst=True)
