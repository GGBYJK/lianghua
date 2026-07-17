"""Add importable product margin and commission templates."""

from alembic import op
import sqlalchemy as sa

from app.trading_db import product_cost_templates


revision = "20260717_04"
down_revision = "20260717_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("contract_specs")}
    if "fee_close_today_mode" not in columns:
        op.add_column("contract_specs", sa.Column("fee_close_today_mode", sa.String(length=24), nullable=True))
    if "fee_close_today_value" not in columns:
        op.add_column("contract_specs", sa.Column("fee_close_today_value", sa.Numeric(24, 8), nullable=True))
    product_cost_templates.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass
