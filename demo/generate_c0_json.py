"""Generate a JSON file for corn futures data.

The existing backend market client uses the Aliyun symbol `c0` for corn's main
continuous futures contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "c0_futures_data.json"
TQ_SYMBOLS = {
    "c0": "KQ.m@DCE.c",
}

sys.path.insert(0, str(BACKEND_DIR))

from app.market_client import fetch_kline_from_market  # noqa: E402


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _format_datetime(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000_000:
            return pd.to_datetime(int(number), unit="ns").isoformat()
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000).isoformat()
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number).isoformat()
    return pd.to_datetime(value).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate corn futures c0 JSON data.")
    parser.add_argument(
        "--source",
        choices=["market", "tqsdk"],
        default="market",
        help="Data source: market uses the existing backend provider; tqsdk uses TqSdk.",
    )
    parser.add_argument("--symbol", default="c0", help="Market symbol, default: c0")
    parser.add_argument("--period", default="1m", help="K-line period, default: 1m")
    parser.add_argument("--limit", type=int, default=120, help="Number of K-line rows.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON file.")
    parser.add_argument("--timeout", type=float, default=60, help="TqSdk wait timeout in seconds.")
    return parser.parse_args()


def dataframe_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "datetime": _format_datetime(row.datetime),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in df.itertuples(index=False)
    ]


def fetch_tqsdk_kline(symbol: str, period: str, limit: int, timeout: float) -> pd.DataFrame:
    from tqsdk import TqApi, TqAuth

    _load_dotenv(PROJECT_ROOT / ".env")
    account = os.getenv("TQ_ACCOUNT") or os.getenv("TQ_USER") or os.getenv("TQ_USERNAME")
    password = os.getenv("TQ_PASSWORD")
    if not account or not password:
        raise RuntimeError("Missing TqSdk credentials: set TQ_ACCOUNT and TQ_PASSWORD.")

    duration_map = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "60m": 3600,
        "1h": 3600,
        "1d": 86400,
    }
    if period not in duration_map:
        raise ValueError(f"TqSdk source does not support period: {period}")

    tq_symbol = TQ_SYMBOLS.get(symbol, symbol)
    api = TqApi(auth=TqAuth(account, password))
    try:
        klines = api.get_kline_serial(tq_symbol, duration_map[period], data_length=limit)
        deadline = time.time() + timeout
        while time.time() < deadline:
            api.wait_update(deadline=deadline)
            if len(klines) >= min(limit, 1) and not pd.isna(klines.close.iloc[-1]):
                break
        df = klines.tail(limit).copy()
        df = df.dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
        if df.empty:
            raise RuntimeError(f"TqSdk returned no K-line data for {tq_symbol}.")
        return df[["datetime", "open", "high", "low", "close", "volume"]]
    finally:
        api.close()


async def main() -> int:
    args = parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output

    if args.source == "tqsdk":
        df = fetch_tqsdk_kline(args.symbol, args.period, args.limit, args.timeout)
        provider = "tqsdk"
    else:
        df = await fetch_kline_from_market(symbol=args.symbol, period=args.period, limit=args.limit)
        provider = os.getenv("MARKET_DATA_PROVIDER", "aliyun")
    records = dataframe_to_records(df)

    payload = {
        "symbol": args.symbol,
        "name": "玉米主力连续",
        "name_en": "Corn main continuous",
        "market": "DCE",
        "period": args.period,
        "limit": args.limit,
        "provider": provider,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(records),
        "data": records,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=_json_default), encoding="utf-8")
    print(f"Wrote {len(records)} rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
