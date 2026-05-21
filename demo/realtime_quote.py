"""Fetch realtime quote data with TqSdk.

Usage:
    $env:TQ_ACCOUNT = "your_shinny_account"
    $env:TQ_PASSWORD = "your_password"
    python demo/realtime_quote.py --symbol SHFE.ni2606 --count 10
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from math import isnan
from typing import Any

from tqsdk import TqApi, TqAuth


DEFAULT_SYMBOL = "KQ.m@SHFE.ni"


def _load_dotenv(path: str) -> None:
    """Load simple KEY=VALUE entries without adding a dependency."""
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _is_valid_price(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isnan(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print realtime quote data from TqSdk.")
    parser.add_argument(
        "--symbol",
        default=os.getenv("TQ_SYMBOL", DEFAULT_SYMBOL),
        help=f"Contract symbol, default: env TQ_SYMBOL or {DEFAULT_SYMBOL}",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.getenv("TQ_QUOTE_COUNT", "10")),
        help="Number of quote updates to print before exiting.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("TQ_WAIT_TIMEOUT", "30")),
        help="Seconds to wait for realtime data before failing.",
    )
    return parser.parse_args()


def main() -> int:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _load_dotenv(os.path.join(project_root, ".env"))

    args = parse_args()
    account = os.getenv("TQ_ACCOUNT") or os.getenv("TQ_USER") or os.getenv("TQ_USERNAME")
    password = os.getenv("TQ_PASSWORD")
    if not account or not password:
        print(
            "Missing TqSdk credentials. Set TQ_ACCOUNT and TQ_PASSWORD in the shell "
            "or in the project .env file.",
            file=sys.stderr,
        )
        return 2

    print(f"Connecting to TqSdk, symbol={args.symbol}, count={args.count}...", flush=True)
    api = TqApi(auth=TqAuth(account, password))
    quote = api.get_quote(args.symbol)

    printed = 0
    try:
        while printed < args.count:
            if not api.wait_update(deadline=time.time() + args.timeout):
                print(
                    f"No quote update received within {args.timeout:g} seconds for {args.symbol}.",
                    file=sys.stderr,
                )
                return 1
            if not _is_valid_price(quote.last_price):
                print(
                    f"Received update but last_price is not ready yet: {quote.last_price}",
                    flush=True,
                )
                continue

            printed += 1
            local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"{printed:02d} local_time={local_time} "
                f"quote_time={quote.datetime} symbol={args.symbol} "
                f"last={quote.last_price} bid1={quote.bid_price1} ask1={quote.ask_price1} "
                f"volume={quote.volume} open_interest={quote.open_interest}"
            )
    finally:
        api.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
