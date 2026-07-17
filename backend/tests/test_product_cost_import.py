from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.product_cost_import import parse_contract_specs_excel, parse_fee_description


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


def test_parse_full_contract_spec_excel() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["品种", "名称", "合约乘数", "最小变动", "保证金率", "手续费"])
    sheet.append(["SHFE.cu", "铜", 5, 10, 0.17, "万分之0.75，平今仓万分之1.5 (按成交额)"])
    sheet.append(["DCE.m", "豆粕", 10, 1, 0.08, "4.5元/手"])
    content = BytesIO()
    workbook.save(content)

    items, issues = parse_contract_specs_excel(content.getvalue())

    assert issues == []
    assert items == [
        {
            "symbol": "shfe.cu",
            "exchange": "SHFE",
            "name": "铜",
            "multiplier": Decimal("5"),
            "price_tick": Decimal("10"),
            "margin_rate": Decimal("0.17"),
            "fee_mode": "TURNOVER_RATE",
            "fee_value": Decimal("0.000075"),
            "fee_close_today_mode": "TURNOVER_RATE",
            "fee_close_today_value": Decimal("0.00015"),
            "enabled": True,
        },
        {
            "symbol": "dce.m",
            "exchange": "DCE",
            "name": "豆粕",
            "multiplier": Decimal("10"),
            "price_tick": Decimal("1"),
            "margin_rate": Decimal("0.08"),
            "fee_mode": "PER_LOT",
            "fee_value": Decimal("4.5"),
            "fee_close_today_mode": None,
            "fee_close_today_value": None,
            "enabled": True,
        },
    ]
