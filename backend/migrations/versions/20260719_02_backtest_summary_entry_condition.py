"""Group backtest summaries by entry condition as well as take-profit rule."""

from alembic import op
import sqlalchemy as sa


revision = "20260719_02"
down_revision = "20260719_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("backtest_rule_summaries")}
    if "entry_condition" not in columns:
        op.add_column(
            "backtest_rule_summaries",
            sa.Column("entry_condition", sa.String(length=40), nullable=False, server_default="mixed"),
        )
    constraints = {item["name"] for item in inspector.get_unique_constraints("backtest_rule_summaries")}
    if "uq_backtest_rule_summary" in constraints:
        op.drop_constraint("uq_backtest_rule_summary", "backtest_rule_summaries", type_="unique")
    if "uq_backtest_rule_summary_entry_condition" not in constraints:
        op.create_unique_constraint(
            "uq_backtest_rule_summary_entry_condition",
            "backtest_rule_summaries",
            ["run_id", "rule_key", "entry_condition"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = {item["name"] for item in inspector.get_unique_constraints("backtest_rule_summaries")}
    if "uq_backtest_rule_summary_entry_condition" in constraints:
        op.drop_constraint("uq_backtest_rule_summary_entry_condition", "backtest_rule_summaries", type_="unique")
    if "uq_backtest_rule_summary" not in constraints:
        op.create_unique_constraint("uq_backtest_rule_summary", "backtest_rule_summaries", ["run_id", "rule_key"])
    columns = {column["name"] for column in sa.inspect(bind).get_columns("backtest_rule_summaries")}
    if "entry_condition" in columns:
        op.drop_column("backtest_rule_summaries", "entry_condition")
