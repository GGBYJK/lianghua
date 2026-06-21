from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from app.watch_pool_import import EXPECTED_HEADERS, parse_watch_pool_excel


def make_workbook(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(EXPECTED_HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_watch_pool_excel_validates_rows() -> None:
    content = make_workbook([
        ["SHFE.rb2605", "1m", 0, 0, "关闭", "", "", "day,night", "开启"],
        ["SHFE.hc2610", "3m", 8, 4, "开启", "3500-3520", "3300-3320", "day", "开启"],
        ["SHFE.nope2605", "1m", 0, 0, "关闭", "", "", "day", "开启"],
        ["SHFE.hc2610", "1d", 0, 0, "关闭", "", "", "day", "开启"],
        ["SHFE.hc2610", "5m", -1, -1, "maybe", "bad", "bad", "bad", "maybe"],
    ])

    items, errors = parse_watch_pool_excel(content, {"SHFE.rb2605": "螺纹钢", "SHFE.hc2610": "热卷"})

    assert [(item["symbol"], item["timeframe"]) for item in items] == [("SHFE.rb2605", "1m"), ("SHFE.hc2610", "3m")]
    assert any(error.field == "监控品种" for error in errors)
    assert any(error.field == "监控周期" for error in errors)
    assert any(error.field == "头部到左颈，头部到右颈最小高度" for error in errors)
    assert any(error.field == "左颈到左肩，右颈到右肩最小价差" for error in errors)
    assert any(error.field == "启用关键区间趋势评分" for error in errors)
    assert any(error.field == "阻挡区间" for error in errors)
    assert any(error.field == "支撑区间" for error in errors)
    assert any(error.field == "交易时间段" for error in errors)
    assert any(error.field == "监控开关" for error in errors)
    assert items[1]["monitor_minutes"] == 3
    assert items[1]["min_shoulder_to_neck_height"] == 4.0
    assert items[1]["enable_key_zone_trend_score"] is True
    assert items[1]["resistance_zone_min"] == 3500.0
    assert items[1]["resistance_zone_max"] == 3520.0
    assert items[1]["support_zone_min"] == 3300.0
    assert items[1]["support_zone_max"] == 3320.0


def test_import_watch_pool_skips_existing_and_creates_new(monkeypatch) -> None:
    from app import main

    content = make_workbook([
        ["SHFE.rb2605", "1m", 0, 0, "关闭", "", "", "day,night", "开启"],
        ["SHFE.hc2610", "3m", 8, 4, "开启", "3500-3520", "3300-3320", "day", "开启"],
    ])
    created: list[dict[str, object]] = []

    monkeypatch.setattr(
        main,
        "list_contract_center_items",
        lambda: [
            {"symbol": "SHFE.rb2605", "name": "螺纹钢"},
            {"symbol": "SHFE.hc2610", "name": "热卷"},
        ],
    )
    monkeypatch.setattr(main, "list_watch_pool_keys", lambda: {("SHFE.rb2605", "1m")})

    def fake_create(item: dict[str, object]) -> dict[str, object]:
        created.append(item)
        return {
            "id": "9",
            **item,
            "monitor_started_at": None,
            "created_at": "2026-05-25T00:00:00+00:00",
            "updated_at": "2026-05-25T00:00:00+00:00",
        }

    monkeypatch.setattr(main, "create_watch_pool_item", fake_create)

    client = TestClient(app)
    response = client.post(
        "/api/watch-pool/import",
        files={"file": ("demo.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 1
    assert body["skipped"] == 1
    assert body["failed"] == 0
    assert body["duplicates"][0]["symbol"] == "SHFE.rb2605"
    assert created[0]["symbol"] == "SHFE.hc2610"
    assert created[0]["timeframe"] == "3m"
    assert created[0]["monitor_minutes"] == 3
    assert created[0]["min_shoulder_to_neck_height"] == 4.0
    assert created[0]["enable_key_zone_trend_score"] is True
    assert created[0]["resistance_zone_min"] == 3500.0
    assert created[0]["resistance_zone_max"] == 3520.0
    assert created[0]["support_zone_min"] == 3300.0
    assert created[0]["support_zone_max"] == 3320.0


def test_import_watch_pool_rejects_non_xlsx() -> None:
    client = TestClient(app)
    response = client.post("/api/watch-pool/import", files={"file": ("demo.csv", b"a,b", "text/csv")})
    assert response.status_code == 400
