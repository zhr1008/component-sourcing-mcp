from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .ad_catalog import load_ad_catalog, save_ad_catalog, scan_altium_libraries
from .ad_utils import attr_any, iter_items, normalize_text, resolved_path


def _ambiguous_footprint_names(catalog: dict[str, Any]) -> set[str]:
    return {
        normalize_text(name)
        for name, asset_ids in (catalog.get("footprint_name_index") or {}).items()
        if len(asset_ids or []) > 1
    }


def _augment_implementation_hints(
    catalog: dict[str, Any],
    ambiguous_names: set[str],
) -> int:
    """Use DATAFILE hints only when a same-name footprint actually needs disambiguation."""
    if not ambiguous_names:
        return 0
    try:
        from altium_monkey import AltiumSchLib
    except ImportError:
        return 0

    by_symbol = {
        (normalize_text(row.get("library_path")), normalize_text(row.get("name"))): row
        for row in (catalog.get("symbols") or {}).values()
    }
    changed = 0
    for path, file_row in (catalog.get("files") or {}).items():
        if file_row.get("kind") != "SchLib":
            continue
        if file_row.get("scan_status") != "ok" or file_row.get("reused") is True:
            continue
        try:
            library = AltiumSchLib(Path(path))
        except Exception:
            continue

        for symbol_obj in iter_items(attr_any(library, "symbols", default=[])):
            symbol_name = str(attr_any(symbol_obj, "name", default="") or "")
            row = by_symbol.get((normalize_text(path), normalize_text(symbol_name)))
            if not row:
                continue

            candidates: list[Any] = []
            for attr_name in ("implementations", "models", "footprints", "objects"):
                candidates.extend(iter_items(attr_any(symbol_obj, attr_name)))
            links = list(row.get("footprint_links") or [])

            for item in candidates:
                model_name = str(attr_any(item, "model_name", "ModelName", "name", default="") or "").strip()
                if not model_name or normalize_text(model_name) not in ambiguous_names:
                    continue
                model_type = str(attr_any(item, "model_type", "ModelType", default="") or "").strip()
                cls_name = item.__class__.__name__.casefold()
                if model_type and "pcb" not in model_type.casefold():
                    continue
                if not model_type and "implementation" not in cls_name and "footprint" not in cls_name:
                    continue

                datafile = str(
                    attr_any(
                        item,
                        "datafile_entity",
                        "model_datafile_entity",
                        "library_name",
                        "datafile",
                        "model_datafile",
                        "ModelDatafileEntity0",
                        "ModelDatafile0",
                        default="",
                    )
                    or ""
                ).strip()
                if not datafile:
                    continue
                datafile_kind = str(attr_any(item, "datafile_kind", "ModelDatafileKind0", default="") or "").strip()

                matched = False
                for link in links:
                    if normalize_text(link.get("name")) != normalize_text(model_name):
                        continue
                    matched = True
                    if not link.get("library"):
                        link["library"] = datafile
                        link["library_kind"] = datafile_kind or None
                        link["source"] = "altium_implementation_datafile"
                        changed += 1
                if not matched:
                    links.append(
                        {
                            "name": model_name,
                            "library": datafile,
                            "library_kind": datafile_kind or None,
                            "source": "altium_implementation_datafile",
                            "is_current": bool(attr_any(item, "is_current", "IsCurrent", default=False)),
                        }
                    )
                    changed += 1
            row["footprint_links"] = links

    return changed


def scan_altium_libraries_resilient(
    paths: Iterable[str | Path],
    *,
    catalog_root: str | Path = "altium-catalog",
    recursive: bool = True,
    hash_files: bool = False,
    reuse_unchanged: bool = True,
) -> dict[str, Any]:
    """Scan libraries, retaining last-good assets when a changed legacy file cannot parse."""
    root = resolved_path(catalog_root)
    before = load_ad_catalog(root)
    result = scan_altium_libraries(
        paths,
        catalog_root=root,
        recursive=recursive,
        hash_files=hash_files,
        reuse_unchanged=reuse_unchanged,
    )
    errors = list(result.get("parse_errors") or [])
    after = load_ad_catalog(root)
    stale_rows: list[dict[str, Any]] = []

    for error in errors:
        path = str(error.get("path") or "")
        old_file = (before.get("files") or {}).get(path)
        if not old_file:
            continue
        kind = str(old_file.get("kind") or error.get("kind") or "")
        asset_ids = list(old_file.get("asset_ids") or [])
        target_map = after.get("symbols") if kind == "SchLib" else after.get("footprints")
        old_map = before.get("symbols") if kind == "SchLib" else before.get("footprints")
        if not isinstance(target_map, dict) or not isinstance(old_map, dict):
            continue

        retained = 0
        for asset_id in asset_ids:
            old_asset = old_map.get(asset_id)
            if old_asset is None:
                continue
            target_map[asset_id] = {
                **old_asset,
                "catalog_stale": True,
                "catalog_stale_reason": "latest file parse failed; retained last successful record",
            }
            retained += 1

        file_row = (after.get("files") or {}).get(path) or {}
        file_row.update(
            {
                "asset_ids": asset_ids,
                "scan_status": "stale_due_to_parse_error",
                "reused_last_good": True,
                "error": error.get("error"),
            }
        )
        after.setdefault("files", {})[path] = file_row
        stale_rows.append(
            {
                "path": path,
                "kind": kind,
                "retained_assets": retained,
                "error": error.get("error"),
            }
        )

    ambiguous_names = _ambiguous_footprint_names(after)
    hint_updates = _augment_implementation_hints(after, ambiguous_names)
    catalog_changed = bool(stale_rows) or hint_updates > 0
    if catalog_changed:
        from .ad_catalog import _resolve_footprint_links

        _resolve_footprint_links(after)
        save_ad_catalog(root, after)

    result.update(
        {
            "symbol_count": len(after.get("symbols") or {}),
            "footprint_count": len(after.get("footprints") or {}),
            "stale_files_retained": len(stale_rows),
            "stale_file_details": stale_rows,
            "ambiguous_footprint_name_count": len(ambiguous_names),
            "implementation_datafile_hints_added": hint_updates,
            "second_catalog_write_performed": catalog_changed,
            "note": "last-good records are retained on parse errors; DATAFILE re-read is limited to duplicate footprint names and extra catalog writes occur only when recovery/hints changed data",
        }
    )
    return result
