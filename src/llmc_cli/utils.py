"""Small shared utilities."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def human_bytes(num: int | float | None) -> str:
    if num is None:
        return "unknown"
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} TiB"


def human_count(num: int | float | None) -> str:
    if num is None:
        return "unknown"
    value = float(num)
    for suffix in ("", "K", "M", "B", "T"):
        if abs(value) < 1000.0 or suffix == "T":
            return f"{value:.1f}{suffix}" if suffix else str(int(value))
        value /= 1000.0
    return str(num)


def slugify_model(model: str) -> str:
    name = model.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name) or "model"


def parse_kv(pairs: list[str] | None, *, label: str = "value") -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"Expected {label} in key=value form, got {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in {pair!r}")
        result[key] = value
    return result


def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_parent(path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def dir_size(path: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def is_remote_id(model: str) -> bool:
    path = Path(model).expanduser()
    return not path.exists() and "/" in model and not model.startswith((".", "/"))


def bool_from_str(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean, got {value!r}")
