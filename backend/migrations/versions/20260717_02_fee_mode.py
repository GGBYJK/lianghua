"""Replace combined fee fields with a single selectable fee configuration."""

from alembic import op
import sqlalchemy as sa


revision = "20260717_02"
down_revision = "20260717_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {column["name"] for column in sa.inspect(bind).get_columns("contract_specs")}
    added_fee_mode = "fee_mode" not in existing_columns

    if added_fee_mode:
        op.add_column(
            "contract_specs",
            sa.Column("fee_mode", sa.String(length=24), nullable=False, server_default="TURNOVER_RATE"),
        )
    if "fee_value" not in existing_columns:
        op.add_column(
            "contract_specs",
            sa.Column("fee_value", sa.Numeric(24, 8), nullable=False, server_default="0"),
        )

    # Preserve the existing opening fee as the unified fee for upgraded rows.
    if added_fee_mode and {"fee_open_rate", "fee_open_fixed"}.issubset(existing_columns):
        op.execute("""
            UPDATE contract_specs
            SET fee_mode = CASE WHEN fee_open_rate > 0 THEN 'TURNOVER_RATE' ELSE 'PER_LOT' END,
                fee_value = CASE WHEN fee_open_rate > 0 THEN fee_open_rate ELSE fee_open_fixed END
        """)


def downgrade() -> None:
    # Legacy fee columns are retained during upgrade so historic configurations remain recoverable.
    pass
