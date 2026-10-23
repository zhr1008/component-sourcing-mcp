"""Request-local cache provenance; contextvars isolate concurrent tool calls."""

from contextvars import ContextVar
from functools import wraps
from typing import Any

from .models import Part

trace: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "sourcing_trace", default=None
)
bypass: ContextVar[bool] = ContextVar("sourcing_refresh", default=False)


def record(url: str, fetched_at: int, *, cached: bool) -> None:
    entries = trace.get()
    if entries is not None:
        entries.append(
            {"endpoint": url, "fetched_at_unix": fetched_at, "cached": cached}
        )


def tracked(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        parent = trace.get()
        token = trace.set([]) if parent is None else None
        refresh_token = bypass.set(bypass.get() or kwargs.get("refresh", False))
        try:
            result = fn(*args, **kwargs)
            entries = trace.get() or []
            if token is not None:
                summary = {
                    "oldest_fetched_at_unix": min(
                        (r["fetched_at_unix"] for r in entries), default=None
                    ),
                    "request_count": len(entries),
                    "cache_hits": sum(r["cached"] for r in entries),
                    "refresh_requested": bypass.get(),
                    "stale_fallback": False,
                }
                rows = result if isinstance(result, list) else [result]
                for row in rows:
                    part = row.get("part") if isinstance(row, dict) else row
                    if isinstance(part, Part):
                        part.data_freshness = summary.copy()
            return result
        finally:
            bypass.reset(refresh_token)
            if token is not None:
                trace.reset(token)

    return wrapped
