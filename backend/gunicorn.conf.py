from __future__ import annotations

import multiprocessing
import os


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


bind = os.getenv("GUNICORN_BIND", f"0.0.0.0:{os.getenv('PORT', '8010')}")
workers = _int_env("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1)
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "uvicorn.workers.UvicornWorker")

request_timeout = _int_env("REQUEST_TIMEOUT_SECONDS", 30)
timeout = _int_env("GUNICORN_TIMEOUT", request_timeout)
graceful_timeout = _int_env("GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = _int_env("GUNICORN_KEEPALIVE", 5)

max_requests = _int_env("GUNICORN_MAX_REQUESTS", 1000)
max_requests_jitter = _int_env("GUNICORN_MAX_REQUESTS_JITTER", 200)

preload_app = _bool_env("GUNICORN_PRELOAD_APP", True)

accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
loglevel = os.getenv("LOG_LEVEL", "info").lower()

raw_env = [
    f"REQUEST_TIMEOUT_SECONDS={request_timeout}",
    f"REQUEST_TIMEOUT_KILL_WORKER={os.getenv('REQUEST_TIMEOUT_KILL_WORKER', '1')}",
]
