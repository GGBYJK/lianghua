#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

exec gunicorn -c gunicorn.conf.py app.main:app
