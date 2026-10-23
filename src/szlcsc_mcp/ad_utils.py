from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp(fmt: str = "%Y%m%d-%H%M%S") -> str:
    return datetime.now(timezone.utc).strftime(fmt)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def selected_ids(values: Iterable[str] | None) -> set[str]:
    if not values:
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def iter_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return []


def attr_any(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default
