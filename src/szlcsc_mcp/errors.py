"""Stable, public error vocabulary shared by upstream clients and MCP tools."""

from __future__ import annotations

from typing import Any


class SourcingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "UPSTREAM_ERROR",
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


def error_payload(
    error: Exception | str, *, code: str = "TOOL_ERROR"
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "code": getattr(error, "code", code),
        "message": str(error),
        "retryable": getattr(error, "retryable", False),
    }
    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None:
        detail["retry_after_seconds"] = round(retry_after, 3)
    return {"ok": False, "error": detail}
