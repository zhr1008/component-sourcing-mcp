from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .export_utils import csv_cell
from .library_base import LibraryError, _sha256, cad_key, load_index, summarize_index
from .models import Part

_SYNC_FIELDS = (
    "mpn",
    "brand",
    "package",
    "category",
    "small_batch_price_cny",
    "stock",
    "total_stock",
    "in_stock",
    "datasheet_url",
    "product_url",
    "min_order_qty",
    "order_multiple",
    "packaging",
)

_FIELD_GROUPS = {
    "cad_identity": {"mpn", "package"},
    "classification": {"brand", "category"},
    "datasheet": {"datasheet_url"},
    "procurement": {
        "small_batch_price_cny",
        "stock",
        "total_stock",
        "in_stock",
        "min_order_qty",
        "order_multiple",
        "packaging",
        "product_url",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _part_value(part: Part, field: str) -> Any:
    if field == "in_stock":
        return part.in_stock
    return getattr(part, field, None)


def preview_entry_changes(old: dict[str, Any], part: Part) -> dict[str, Any]:
    """Compare a stored library entry with a freshly fetched SZLCSC Part.

    This helper is pure and never writes the managed library. It separates CAD
    identity changes from ordinary procurement metadata so an Agent can decide
    whether a later sync requires human review.
    """
    changes: dict[str, dict[str, Any]] = {}
    for field in _SYNC_FIELDS:
        before = old.get(field)
        after = _part_value(part, field)
        if before != after:
            changes[field] = {"old": before, "new": after}

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    assigned: set[str] = set()
    for group, fields in _FIELD_GROUPS.items():
        rows = {field: changes[field] for field in fields if field in changes}
        if rows:
            grouped[group] = rows
            assigned.update(rows)
    other = {field: row for field, row in changes.items() if field not in assigned}
    if other:
        grouped["other"] = other

    old_key = old.get("cad_key") or cad_key(old)
    new_key = cad_key(part)
    identity_changed = old_key != new_key
    return {
        "changed": bool(changes),
        "changes": changes,
        "groups": grouped,
        "change_count": len(changes),
        "old_cad_key": old_key,
        "new_cad_key": new_key,
        "cad_identity_changed": identity_changed,
        "would_require_cad_review": identity_changed,
        "cad_rebuild_planned": False,
    }


def _resolve_asset_path(root: Path, raw: Any) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _asset_paths(root: Path, entry: dict[str, Any], kind: str, view: str) -> list[Path]:
    archive = entry.get("archive") or {}
    assets = archive.get("assets") or {}
    row = assets.get(kind) or {}
    return [
        _resolve_asset_path(root, value)
        for value in (row.get(view) or [])
        if str(value).strip()
    ]


def _export_row(root: Path, code: str, entry: dict[str, Any], *, include_asset_paths: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "lcsc_code": code,
        "mpn": entry.get("mpn"),
        "brand": entry.get("brand"),
        "package": entry.get("package"),
        "category": entry.get("category"),
        "category_slug": entry.get("category_slug"),
        "small_batch_price_cny": entry.get("small_batch_price_cny"),
        "stock": entry.get("stock"),
        "total_stock": entry.get("total_stock"),
        "in_stock": entry.get("in_stock"),
        "min_order_qty": entry.get("min_order_qty"),
        "order_multiple": entry.get("order_multiple"),
        "packaging": entry.get("packaging"),
        "product_url": entry.get("product_url"),
        "datasheet_url": entry.get("datasheet_url"),
        "cad_key": entry.get("cad_key"),
        "cad_owner": entry.get("cad_owner") or code,
        "cad_shared": bool(entry.get("cad_shared")),
        "cad_review_required": bool(entry.get("cad_review_required")),
        "archive_basename": (entry.get("archive") or {}).get("basename"),
        "metadata_updated_at": entry.get("metadata_updated_at"),
    }
    for kind in ("datasheet", "schlib", "pcblib", "step", "reports"):
        paths = _asset_paths(root, entry, kind, "classified")
        existing = [path for path in paths if path.is_file()]
        row[f"{kind}_referenced_count"] = len(paths)
        row[f"{kind}_existing_count"] = len(existing)
        row[f"has_{kind}"] = bool(existing)
        if include_asset_paths:
            row[f"{kind}_paths"] = " | ".join(str(path) for path in paths)
    return row


def export_library(
    root: str | Path,
    *,
    output_dir: str | Path | None = None,
    export_format: str = "both",
    include_asset_paths: bool = True,
    only_cad_owners: bool = False,
) -> dict[str, Any]:
    """Export a managed library inventory to JSON and/or Excel-friendly CSV."""
    root_path = Path(root).expanduser().resolve()
    index = load_index(root_path)
    fmt = str(export_format or "both").strip().casefold()
    if fmt not in {"json", "csv", "both"}:
        raise LibraryError("export_format must be one of: json, csv, both")

    rows: list[dict[str, Any]] = []
    for code, entry in sorted((index.get("parts") or {}).items()):
        if only_cad_owners and str(entry.get("cad_owner") or code) != code:
            continue
        rows.append(_export_row(root_path, code, entry, include_asset_paths=include_asset_paths))

    out = Path(output_dir).expanduser().resolve() if output_dir else root_path / "exports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"szlcsc-library-{stamp}"
    files: dict[str, str] = {}

    if fmt in {"json", "both"}:
        json_path = out / f"{base}.json"
        payload = {
            "schema_version": 1,
            "generated_at": _now(),
            "library_root": str(root_path),
            "summary": summarize_index(index),
            "count": len(rows),
            "parts": rows,
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        files["json"] = str(json_path)

    if fmt in {"csv", "both"}:
        csv_path = out / f"{base}.csv"
        fieldnames = list(rows[0].keys()) if rows else ["lcsc_code", "mpn", "brand", "package", "category"]
        # utf-8-sig keeps Chinese readable when the file is opened directly in Excel.
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in rows)
        files["csv"] = str(csv_path)

    return {
        "ok": True,
        "library_root": str(root_path),
        "output_dir": str(out),
        "format": fmt,
        "count": len(rows),
        "only_cad_owners": only_cad_owners,
        "include_asset_paths": include_asset_paths,
        "files": files,
        "offline": True,
    }


def scan_orphan_files(
    root: str | Path,
    *,
    include_classified: bool = True,
    include_flat: bool = True,
    include_hashes: bool = False,
    limit: int = 5000,
) -> dict[str, Any]:
    """Find files under classified/flat that are not referenced by library-index.

    The scan is read-only. It intentionally does not inspect merged/ or exports/
    because those trees are generated outputs and are not expected to be indexed
    per procurement entry.
    """
    root_path = Path(root).expanduser().resolve()
    index = load_index(root_path)
    referenced: set[Path] = set()
    for entry in (index.get("parts") or {}).values():
        archive = entry.get("archive") or {}
        assets = archive.get("assets") or {}
        for row in assets.values():
            if not isinstance(row, dict):
                continue
            for view in ("classified", "flat"):
                for raw in row.get(view) or []:
                    if str(raw).strip():
                        referenced.add(_resolve_asset_path(root_path, raw))

    scan_roots: list[tuple[str, Path]] = []
    if include_classified:
        scan_roots.append(("classified", root_path / "classified"))
    if include_flat:
        scan_roots.append(("flat", root_path / "flat"))

    orphans: list[dict[str, Any]] = []
    scanned_files = 0
    for view, base in scan_roots:
        if not base.is_dir():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            scanned_files += 1
            resolved = path.resolve(strict=False)
            if resolved in referenced:
                continue
            item: dict[str, Any] = {
                "view": view,
                "path": str(resolved),
                # Use the discovered path instead of the resolved target so a
                # symlink pointing outside the root cannot break relative reporting.
                "relative_path": str(path.relative_to(root_path)),
                "size_bytes": path.stat().st_size,
            }
            if include_hashes:
                item["sha256"] = _sha256(path)
            orphans.append(item)

    max_rows = max(1, min(int(limit), 20000))
    shown = orphans[:max_rows]
    return {
        "ok": True,
        "library_root": str(root_path),
        "scanned_files": scanned_files,
        "referenced_file_count": len(referenced),
        "orphan_count": len(orphans),
        "returned_count": len(shown),
        "truncated": len(orphans) > len(shown),
        "orphans": shown,
        "read_only": True,
        "note": "No files are deleted automatically; merged/ and exports/ are intentionally outside this scan.",
    }
