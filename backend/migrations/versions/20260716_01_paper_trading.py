"""Create paper trading, authentication and RBAC tables."""

from alembic import op

from app.trading_db import metadata


revision = "20260716_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())

