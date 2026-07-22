"""Add MACD recurrence state for incremental K-line features."""

from alembic import op
import sqlalchemy as sa


revision = "20260722_04"
down_revision = "20260722_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("kline_bar_features")}
    for name in ("ema_fast", "ema_slow"):
        if name not in columns:
            op.add_column(
                "kline_bar_features",
                sa.Column(name, sa.Numeric(24, 12), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("kline_bar_features")}
    for name in ("ema_slow", "ema_fast"):
        if name in columns:
            op.drop_column("kline_bar_features", name)
