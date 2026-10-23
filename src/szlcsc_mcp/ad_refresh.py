from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ad_catalog import load_ad_catalog
from .ad_enrichment import enrich_ad_catalog
from .ad_scan import scan_altium_libraries_resilient
from .ad_utils import resolved_path
from .client import SZLCSCClient
from .library_base import LibraryError


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stale_symbol_ids(catalog_root: str | Path, *, max_age_hours: float = 24.0) -> list[str]:
    catalog = load_ad_catalog(catalog_root)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(float(max_age_hours), 0.0))
    symbols = catalog.get("symbols") or {}
    return sorted(
        asset_id
        for asset_id, symbol in symbols.items()
        if (
            (symbol.get("enrichment") or {}).get("status") != "resolved"
            or (checked := _parse_time((symbol.get("enrichment") or {}).get("last_checked_at"))) is None
            or checked < cutoff
        )
    )


def refresh_ad_catalog(
    client: SZLCSCClient,
    catalog_root: str | Path,
    *,
    max_age_hours: float = 24.0,
    refresh_metadata: bool = True,
    hash_files: bool = False,
) -> dict[str, Any]:
    """Incrementally rescan local Altium libraries and refresh stale SZLCSC metadata."""
    catalog = load_ad_catalog(catalog_root)
    roots = [str(value) for value in catalog.get("scan_roots") or [] if str(value).strip()]
    if not roots:
        raise LibraryError("catalog has no scan_roots; run szlcsc_ad_library_scan first")

    scan = scan_altium_libraries_resilient(
        roots,
        catalog_root=catalog_root,
        recursive=True,
        hash_files=hash_files,
        reuse_unchanged=True,
    )

    enrichment: dict[str, Any] | None = None
    stale: list[str] = []
    if refresh_metadata:
        stale = stale_symbol_ids(catalog_root, max_age_hours=max_age_hours)
        if stale:
            enrichment = enrich_ad_catalog(
                client,
                catalog_root,
                symbol_ids=stale,
                force=True,
            )
        else:
            enrichment = {
                "ok": True,
                "count": 0,
                "resolved": 0,
                "ambiguous": 0,
                "unresolved": 0,
                "lookup_errors": 0,
                "results": [],
                "note": "all enrichment records are within max_age_hours",
            }

    return {
        "ok": bool(scan.get("ok")) and (enrichment is None or bool(enrichment.get("ok"))),
        "catalog_root": str(resolved_path(catalog_root)),
        "local_scan": scan,
        "metadata_refresh_enabled": refresh_metadata,
        "max_age_hours": max_age_hours,
        "stale_symbol_count": len(stale),
        "metadata_refresh": enrichment,
        "verification_policy": "price/stock refresh does not invalidate verification; identity/package changes are marked stale by enrichment",
    }
