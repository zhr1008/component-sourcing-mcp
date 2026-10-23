from __future__ import annotations

import re
from typing import Any

from .normalize import as_int, normalize_c_code


def parse_szlcsc_barcode(raw: str) -> dict[str, Any]:
    """Parse common SZLCSC/LCSC bag QR payloads.

    Example: {pc:C6119790,pm:CGA0402X5R224K250GT,qty:100,mc:,cc:1,pdi:200616857,hp:null}
    The format is object-like but is not strict JSON, so parse conservatively without eval().
    """
    text = (raw or "").strip()
    if not text:
        return {"recognized": False, "fields": {}}
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]

    fields: dict[str, str | None] = {}
    for token in re.split(r",(?=[A-Za-z_][A-Za-z0-9_]*\s*:)", text):
        if ":" not in token:
            continue
        key, value = token.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if value.casefold() in {"null", "none", "undefined"}:
            fields[key] = None
        else:
            fields[key] = value

    code = fields.get("pc") or fields.get("code")
    mpn = fields.get("pm") or fields.get("mpn")
    qty = as_int(fields.get("qty"))
    normalized_code = normalize_c_code(code) if code else None
    recognized = bool(normalized_code and re.fullmatch(r"C\d+", normalized_code, re.I))
    return {
        "recognized": recognized,
        "lcsc_code": normalized_code,
        "mpn": mpn,
        "quantity": qty,
        "fields": fields,
    }
