from __future__ import annotations

import json
from typing import Any


def _point_time(signal: dict[str, Any], name: str) -> Any:
    point = signal.get(name)
    if not isinstance(point, dict):
        return ""
    return point.get("time", "")


def build_signal_unique_key(signal: dict[str, Any]) -> str:
    parts = [
        signal["symbol"],
        signal["timeframe"],
        signal["pattern"],
        _point_time(signal, "head"),
    ]
    return "|".join(str(part) for part in parts)


def build_alert_structure_key(alert: dict[str, Any]) -> str:
    signal = alert.get("signal_payload", alert)
    if isinstance(signal, str):
        try:
            signal = json.loads(signal)
        except json.JSONDecodeError:
            signal = alert
    try:
        return build_signal_unique_key(signal)
    except (AttributeError, KeyError, TypeError):
        return str(alert.get("unique_key", ""))
