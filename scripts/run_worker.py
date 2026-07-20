from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("WORKER_MODE", "backtest")

from app.market_client import MarketApiError  # noqa: E402
from app.worker import run  # noqa: E402


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, MarketApiError):
        pass
