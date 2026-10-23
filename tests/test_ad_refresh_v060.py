from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from szlcsc_mcp.ad_catalog import empty_catalog, load_ad_catalog, save_ad_catalog
from szlcsc_mcp.ad_refresh import stale_symbol_ids


def _stamp(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_stale_symbol_ids_only_returns_old_or_unresolved_records(tmp_path):
    root = tmp_path / "catalog"
    data = empty_catalog()
    data["symbols"] = {
        "fresh": {"enrichment": {"status": "resolved", "last_checked_at": _stamp(1)}},
        "old": {"enrichment": {"status": "resolved", "last_checked_at": _stamp(48)}},
        "unresolved": {"enrichment": {"status": "unresolved", "last_checked_at": _stamp(1)}},
        "never": {},
    }
    save_ad_catalog(root, data)

    assert stale_symbol_ids(root, max_age_hours=24) == ["never", "old", "unresolved"]


def test_catalog_keeps_verification_independent_from_dynamic_metadata_age(tmp_path):
    root = tmp_path / "catalog"
    data = empty_catalog()
    data["symbols"]["symbol:1"] = {
        "name": "TPR3333-S3TR",
        "enrichment": {
            "status": "resolved",
            "lcsc_code": "C5291487",
            "mpn": "TPR3333-S3TR",
            "package": "SOT-23G-3",
            "stock": 10,
            "small_batch_price_cny": 1.23,
            "last_checked_at": _stamp(72),
        },
        "verification": {
            "status": "verified",
            "authority": "datasheet",
            "datasheet_pages": [3, 18],
        },
    }
    save_ad_catalog(root, data)

    catalog = load_ad_catalog(root)
    assert stale_symbol_ids(root, max_age_hours=24) == ["symbol:1"]
    assert catalog["symbols"]["symbol:1"]["verification"]["status"] == "verified"
    assert catalog["symbols"]["symbol:1"]["verification"]["authority"] == "datasheet"
