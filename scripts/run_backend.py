from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

import mysql.connector
import uvicorn


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

# The pure Python connector fails normally when MySQL is unavailable, allowing
# the application to keep its documented database-degraded startup behavior.
mysql.connector.connect = functools.partial(mysql.connector.connect, use_pure=True)

uvicorn.run("app.main:app", host="0.0.0.0", port=8010)
