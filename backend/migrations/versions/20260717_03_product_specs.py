"""Normalize contract settings from delivery-month to product level."""

import re

from alembic import op
import sqlalchemy as sa


revision = "20260717_03"
down_revision = "20260717_02"
branch_labels = None
depends_on = None


def _product_symbol(symbol: str) -> str | None:
    if "." not in symbol:
        return None
    exchange, code = symbol.split(".", 1)
    match = re.match(r"([A-Za-z]+)", code)
    if match is None or match.group(1) == code:
        return None
    return f"{exchange.upper()}.{match.group(1)}".lower()


def upgrade() -> None:
    bind = op.get_bind()
    rows = list(bind.execute(sa.text("SELECT symbol, updated_at FROM contract_specs ORDER BY updated_at DESC")).mappings())
    product_rows = {str(row["symbol"]).lower() for row in rows if _product_symbol(str(row["symbol"])) is None}
    grouped: dict[str, list[str]] = {}
    for row in rows:
        source = str(row["symbol"])
        product = _product_symbol(source)
        if product is not None:
            grouped.setdefault(product, []).append(source)

    for product, symbols in grouped.items():
        if product in product_rows:
            bind.execute(sa.text("DELETE FROM contract_specs WHERE symbol IN :symbols").bindparams(
                sa.bindparam("symbols", expanding=True)
            ), {"symbols": symbols})
            continue
        selected = symbols[0]
        bind.execute(sa.text("UPDATE contract_specs SET symbol = :product WHERE symbol = :selected"), {
            "product": product,
            "selected": selected,
        })
        duplicates = symbols[1:]
        if duplicates:
            bind.execute(sa.text("DELETE FROM contract_specs WHERE symbol IN :symbols").bindparams(
                sa.bindparam("symbols", expanding=True)
            ), {"symbols": duplicates})


def downgrade() -> None:
    # A product setting cannot be safely expanded back to individual delivery months.
    pass
