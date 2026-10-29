"""Shared CSV text handling for spreadsheet-facing exports."""

from typing import Any


def csv_cell(value: Any) -> Any:
    """Escape formula-like text while preserving native numeric cells and JSON."""
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n"))
        or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value
