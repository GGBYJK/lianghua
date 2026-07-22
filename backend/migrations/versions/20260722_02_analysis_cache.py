"""Add versioned market analysis cache."""

from alembic import op
import sqlalchemy as sa

from app.trading_db import market_analysis_cache


revision = "20260722_02"
down_revision = "20260722_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("kline_datasets")}
    if "revision" not in columns:
        op.add_column(
            "kline_datasets",
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        )
    market_analysis_cache.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    market_analysis_cache.drop(bind=bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("kline_datasets")}
    if "revision" in columns:
        op.drop_column("kline_datasets", "revision")
