from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from .strategy import HeadShoulderTopConfig


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "head_shoulder_top.yaml"


def get_symbol_prefix(symbol: str) -> str:
    prefix = ""
    for ch in symbol:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix.lower()


def merge_dicts(*dicts: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in dicts:
        if item:
            result.update(item)
    return result


def create_config_from_dict(config_dict: dict[str, Any]) -> HeadShoulderTopConfig:
    valid_keys = {field.name for field in fields(HeadShoulderTopConfig)}
    filtered = {key: value for key, value in config_dict.items() if key in valid_keys}
    config = HeadShoulderTopConfig(**filtered)
    if config.break_by not in {"close", "low"}:
        raise ValueError("break_by 只能是 close 或 low")
    return config


def load_raw_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_head_shoulder_config(
    symbol: str,
    timeframe: str,
    overrides: dict[str, Any] | None = None,
    path: Path = DEFAULT_CONFIG_PATH,
) -> HeadShoulderTopConfig:
    raw = load_raw_config(path)
    symbol_prefix = get_symbol_prefix(symbol)
    merged = merge_dicts(
        raw.get("default", {}),
        raw.get("timeframes", {}).get(timeframe, {}),
        raw.get("symbols", {}).get(symbol_prefix, {}).get(timeframe, {}),
        overrides,
    )
    return create_config_from_dict(merged)
