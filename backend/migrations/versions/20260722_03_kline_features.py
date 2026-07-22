"""Add versioned per-bar K-line feature cache."""

from alembic import op
import sqlalchemy as sa

from app.trading_db import kline_bar_features


revision = "20260722_03"
down_revision = "20260722_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("kline_datasets")}
    additions = (
        ("feature_version", sa.String(length=64), True, None),
        ("feature_config_hash", sa.String(length=64), True, None),
        ("feature_row_count", sa.Integer(), False, "0"),
        ("features_updated_at", sa.DateTime(), True, None),
    )
    for name, column_type, nullable, server_default in additions:
        if name not in columns:
            op.add_column(
                "kline_datasets",
                sa.Column(name, column_type, nullable=nullable, server_default=server_default),
            )
    kline_bar_features.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    kline_bar_features.drop(bind=bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("kline_datasets")}
    for name in ("features_updated_at", "feature_row_count", "feature_config_hash", "feature_version"):
        if name in columns:
            op.drop_column("kline_datasets", name)
