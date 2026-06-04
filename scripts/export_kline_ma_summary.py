from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.market_client import fetch_kline_from_market, shutdown_market_clients  # noqa: E402


EXPORTS_DIR = ROOT / "exports"
OUTPUT = EXPORTS_DIR / "five_varieties_kline_ma_summary.xlsx"

SYMBOLS = [
    {"品种": "玉米", "代码": "KQ.m@DCE.c"},
    {"品种": "豆粕", "代码": "KQ.m@DCE.m"},
    {"品种": "白糖", "代码": "KQ.m@CZCE.SR"},
    {"品种": "豆一", "代码": "KQ.m@DCE.a"},
    {"品种": "PVC", "代码": "KQ.m@DCE.v"},
]

TIMEFRAMES = [
    {"周期": "小时线", "period": "1h", "sheet": "小时线最近100"},
    {"周期": "日线", "period": "1d", "sheet": "日线最近100"},
]

MA_PERIODS = [5, 10, 20, 30, 60, 250]
FETCH_LIMIT = 360
EXPORT_LIMIT = 100


def enrich(symbol_row: dict[str, str], timeframe: dict[str, str], df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values("datetime").reset_index(drop=True)
    for period in MA_PERIODS:
        df[f"MA{period}"] = df["close"].rolling(period).mean()

    df = df.tail(EXPORT_LIMIT).copy().reset_index(drop=True)
    df.insert(0, "序号", range(1, len(df) + 1))
    df.insert(1, "品种", symbol_row["品种"])
    df.insert(2, "代码", symbol_row["代码"])
    df.insert(3, "周期", timeframe["周期"])
    df = df.rename(
        columns={
            "datetime": "时间",
            "open": "起始价(开盘价)",
            "close": "收盘价",
            "high": "最高价",
            "low": "最低价",
            "volume": "成交量",
        }
    )
    columns = [
        "序号",
        "品种",
        "代码",
        "周期",
        "时间",
        "起始价(开盘价)",
        "收盘价",
        "MA5",
        "MA10",
        "MA20",
        "MA30",
        "MA60",
        "MA250",
        "最高价",
        "最低价",
        "成交量",
    ]
    return df[columns]


async def fetch_one(
    symbol_row: dict[str, str],
    timeframe: dict[str, str],
) -> tuple[str, str, pd.DataFrame | None, str | None]:
    label = f"{symbol_row['品种']} {timeframe['周期']}"
    try:
        df = await fetch_kline_from_market(
            symbol=symbol_row["代码"],
            period=timeframe["period"],
            limit=FETCH_LIMIT,
        )
        if df.empty:
            return timeframe["sheet"], label, None, "接口返回空数据"
        return timeframe["sheet"], label, enrich(symbol_row, timeframe, df), None
    except Exception as exc:  # noqa: BLE001 - export should record provider failures.
        return timeframe["sheet"], label, None, str(exc)


async def export() -> None:
    EXPORTS_DIR.mkdir(exist_ok=True)
    tasks = [fetch_one(symbol, timeframe) for symbol in SYMBOLS for timeframe in TIMEFRAMES]
    results = await asyncio.gather(*tasks)

    by_sheet: dict[str, list[pd.DataFrame]] = {timeframe["sheet"]: [] for timeframe in TIMEFRAMES}
    errors: list[dict[str, str]] = []
    for sheet, label, df, error in results:
        if error:
            errors.append({"对象": label, "错误": error})
            continue
        if df is not None:
            by_sheet[sheet].append(df)

    hourly = (
        pd.concat(by_sheet["小时线最近100"], ignore_index=True)
        if by_sheet["小时线最近100"]
        else pd.DataFrame()
    )
    daily = (
        pd.concat(by_sheet["日线最近100"], ignore_index=True)
        if by_sheet["日线最近100"]
        else pd.DataFrame()
    )
    latest_parts = [
        frame.groupby(["品种", "代码", "周期"], as_index=False).tail(1)
        for frame in [hourly, daily]
        if not frame.empty
    ]
    latest = pd.concat(latest_parts, ignore_index=True) if latest_parts else pd.DataFrame()

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        hourly.to_excel(writer, sheet_name="小时线最近100", index=False)
        daily.to_excel(writer, sheet_name="日线最近100", index=False)
        latest.to_excel(writer, sheet_name="最新汇总", index=False)
        if errors:
            pd.DataFrame(errors).to_excel(writer, sheet_name="取数错误", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                values = [str(cell.value) for cell in column_cells if cell.value is not None]
                width = min(max([len(value) for value in values] + [10]) + 2, 24)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width

    print(f"OUTPUT={OUTPUT}")
    print(f"hourly_rows={len(hourly)} daily_rows={len(daily)} latest_rows={len(latest)} errors={len(errors)}")
    for item in errors:
        print(f"ERROR {item['对象']}: {item['错误']}")


def main() -> None:
    try:
        asyncio.run(export())
    finally:
        shutdown_market_clients()


if __name__ == "__main__":
    main()
