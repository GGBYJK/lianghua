"""Allow full pattern and trigger keys in backtest summary entry conditions."""

from alembic import op
import sqlalchemy as sa


revision = "20260719_03"
down_revision = "20260719_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    column = next(
        item for item in sa.inspect(bind).get_columns("backtest_rule_summaries")
        if item["name"] == "entry_condition"
    )
    if getattr(column["type"], "length", 0) < 80:
        op.alter_column(
            "backtest_rule_summaries",
            "entry_condition",
            existing_type=sa.String(length=40),
            type_=sa.String(length=80),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.alter_column(
        "backtest_rule_summaries",
        "entry_condition",
        existing_type=sa.String(length=80),
        type_=sa.String(length=40),
        existing_nullable=False,
    )
