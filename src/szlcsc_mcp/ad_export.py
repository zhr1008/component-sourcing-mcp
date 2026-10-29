from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .ad_catalog import load_ad_catalog
from .ad_utils import normalize_text, resolved_path, utc_now, utc_stamp
from .export_utils import csv_cell
from .library_base import LibraryError


def _compact_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    )


def _row(catalog: dict[str, Any], symbol: dict[str, Any]) -> dict[str, Any]:
    enrichment = symbol.get("enrichment") or {}
    verification = symbol.get("verification") or {}
    links = symbol.get("resolved_footprint_links") or []
    footprints_by_id = catalog.get("footprints") or {}
    resolved_ids = [
        candidate
        for link in links
        if link.get("status") == "resolved"
        for candidate in link.get("candidates") or []
    ]
    footprints = [footprints_by_id.get(asset_id) or {} for asset_id in resolved_ids]
    hints = symbol.get("identity_hints") or {}
    local_params = symbol.get("parameters") or {}
    remote_params = enrichment.get("parameters") or {}
    footprint_geometries = [fp.get("geometry") or {} for fp in footprints]
    return {
        "asset_id": symbol.get("asset_id"),
        "symbol": symbol.get("name"),
        "schlib": symbol.get("library_path"),
        "catalog_stale": bool(symbol.get("catalog_stale")),
        "existing_lcsc_code": hints.get("lcsc_code"),
        "existing_mpn": hints.get("mpn"),
        "existing_brand": hints.get("brand"),
        "existing_package": hints.get("package"),
        "lcsc_code": enrichment.get("lcsc_code"),
        "mpn": enrichment.get("mpn"),
        "brand": enrichment.get("brand"),
        "category": enrichment.get("category"),
        "package": enrichment.get("package"),
        "description": enrichment.get("name") or symbol.get("description"),
        "small_batch_price_cny": enrichment.get("small_batch_price_cny"),
        "stock": enrichment.get("stock"),
        "total_stock": enrichment.get("total_stock"),
        "in_stock": enrichment.get("in_stock"),
        "min_order_qty": enrichment.get("min_order_qty"),
        "order_multiple": enrichment.get("order_multiple"),
        "packaging": enrichment.get("packaging"),
        "datasheet_url": enrichment.get("datasheet_url"),
        "datasheet_path": enrichment.get("datasheet_path"),
        "product_url": enrichment.get("product_url"),
        "pin_count": len(symbol.get("pins") or []),
        "pin_numbers": " | ".join(str(pin.get("number") or "") for pin in symbol.get("pins") or []),
        "footprint_link_status": " | ".join(str(link.get("status") or "") for link in links),
        "footprint_links": " | ".join(str(link.get("name") or "") for link in links),
        "resolved_footprint": " | ".join(str(fp.get("name") or "") for fp in footprints),
        "resolved_pcblib": " | ".join(str(fp.get("library_path") or "") for fp in footprints),
        "pad_count": " | ".join(str((fp.get("geometry") or {}).get("pad_count") or "") for fp in footprints),
        "footprint_geometry_json": _compact_json(footprint_geometries),
        "identity_status": enrichment.get("status") or "not_enriched",
        "identity_method": enrichment.get("method"),
        "verification_status": verification.get("status") or "not_verified",
        "verification_authority": verification.get("authority"),
        "verification_pages": " | ".join(str(page) for page in verification.get("datasheet_pages") or []),
        "local_parameters_json": _compact_json(local_params, sort_keys=True),
        "szlcsc_parameters_json": _compact_json(remote_params, sort_keys=True),
        "price_tiers_json": _compact_json(enrichment.get("price_tiers") or []),
        "issues_json": _compact_json((symbol.get("issues") or []) + (verification.get("issues") or [])),
        "last_checked_at": enrichment.get("last_checked_at"),
        "scanned_at": symbol.get("scanned_at"),
    }


def export_ad_catalog_complete(
    catalog_root: str | Path,
    *,
    output_dir: str | Path | None = None,
    export_format: str = "both",
) -> dict[str, Any]:
    """Export the complete Altium component directory without dropping parameters."""
    catalog = load_ad_catalog(catalog_root)
    fmt = str(export_format or "both").casefold().strip()
    if fmt not in {"json", "csv", "both"}:
        raise LibraryError("export_format must be json, csv, or both")

    root = resolved_path(catalog_root)
    out = resolved_path(output_dir) if output_dir else root / "exports"
    out.mkdir(parents=True, exist_ok=True)
    rows = [_row(catalog, symbol) for symbol in (catalog.get("symbols") or {}).values()]
    rows.sort(key=lambda row: (normalize_text(row.get("schlib")), normalize_text(row.get("symbol"))))
    stamp = utc_stamp()
    files: dict[str, str] = {}

    if fmt in {"json", "both"}:
        path = out / f"altium-component-catalog-{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "generated_at": utc_now(),
                    "catalog_revision": catalog.get("revision"),
                    "component_count": len(rows),
                    "components": rows,
                    "authority_policy": "Datasheet is the sole authority for final verification",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        files["json"] = str(path)

    if fmt in {"csv", "both"}:
        path = out / f"altium-component-catalog-{stamp}.csv"
        fieldnames = list(rows[0].keys()) if rows else ["asset_id", "symbol", "schlib", "mpn", "lcsc_code", "package"]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in rows)
        files["csv"] = str(path)

    return {
        "ok": True,
        "count": len(rows),
        "output_dir": str(out),
        "files": files,
        "offline": True,
        "contains_full_szlcsc_parameters": True,
        "contains_library_locations": True,
        "contains_verification_status": True,
    }
