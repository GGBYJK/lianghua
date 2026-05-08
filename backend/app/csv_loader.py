from __future__ import annotations

from io import BytesIO

import pandas as pd


REQUIRED_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]

COLUMN_ALIASES = {
    "datetime": {"datetime", "date", "time", "trade_time", "timestamp"},
    "open": {"open", "o"},
    "high": {"high", "h"},
    "low": {"low", "l"},
    "close": {"close", "c", "last_price", "last"},
    "volume": {"volume", "vol", "v"},
}


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def normalize_kline_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    normalized = {_normalize_name(col): col for col in df.columns}
    rename_map: dict[str, str] = {}
    missing: list[str] = []

    for canonical, aliases in COLUMN_ALIASES.items():
        source = next((normalized[alias] for alias in aliases if alias in normalized), None)
        if source is None:
            missing.append(canonical)
        else:
            rename_map[source] = canonical

    if missing:
        raise ValueError(f"CSV 缺少必要字段：{', '.join(missing)}")

    result = df.rename(columns=rename_map)[REQUIRED_COLUMNS].copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")

    bad_rows = result[result[REQUIRED_COLUMNS].isna().any(axis=1)]
    if not bad_rows.empty:
        raise ValueError(f"CSV 存在无效数据，行索引：{bad_rows.index[:10].tolist()}")

    return result.sort_values("datetime").reset_index(drop=True)


def read_csv_bytes(content: bytes) -> pd.DataFrame:
    try:
        df = pd.read_csv(BytesIO(content))
    except Exception as exc:
        raise ValueError(f"CSV 解析失败：{exc}") from exc
    if df.empty:
        raise ValueError("CSV 文件为空")
    return normalize_kline_dataframe(df)
