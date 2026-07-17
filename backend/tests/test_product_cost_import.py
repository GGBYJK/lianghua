from decimal import Decimal

from app.product_cost_import import parse_fee_description


def test_parse_turnover_fee_with_close_today_rate() -> None:
    result = parse_fee_description("万分之0.75，平今仓万分之1.5 (按成交额)")

    assert result == {
        "fee_mode": "TURNOVER_RATE",
        "fee_value": Decimal("0.000075"),
        "fee_close_today_mode": "TURNOVER_RATE",
        "fee_close_today_value": Decimal("0.00015"),
        "fee_description": "万分之0.75，平今仓万分之1.5 (按成交额)",
    }


def test_parse_fixed_fee_with_close_today_amount() -> None:
    result = parse_fee_description("7.5元/手, 平今15元/手")

    assert result["fee_mode"] == "PER_LOT"
    assert result["fee_value"] == Decimal("7.5")
    assert result["fee_close_today_mode"] == "PER_LOT"
    assert result["fee_close_today_value"] == Decimal("15")
