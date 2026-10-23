from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .ad_catalog import backup_schlib, load_ad_catalog, save_ad_catalog
from .ad_utils import normalize_text, resolved_path, selected_ids, utc_now
from .library_base import LibraryError


def _feature_summary(parameters: dict[str, Any], *, limit: int = 16) -> str:
    pairs: list[str] = []
    for key, value in parameters.items():
        if value in (None, ""):
            continue
        pairs.append(f"{key}={value}")
        if len(pairs) >= limit:
            break
    return "; ".join(pairs)


def desired_symbol_properties(symbol: dict[str, Any], *, parameter_mode: str = "summary") -> dict[str, str]:
    enrichment = symbol.get("enrichment") or {}
    if enrichment.get("status") != "resolved":
        return {}

    result = {
        "Manufacturer": enrichment.get("brand"),
        "Manufacturer Part Number": enrichment.get("mpn"),
        "LCSC Part #": enrichment.get("lcsc_code"),
        "SZLCSC Category": enrichment.get("category"),
        "Package": enrichment.get("package"),
        "Description": enrichment.get("name"),
        "Datasheet": enrichment.get("datasheet_url"),
        "SZLCSC URL": enrichment.get("product_url"),
        "Small Batch Price CNY": enrichment.get("small_batch_price_cny"),
        "SZLCSC Stock": enrichment.get("stock") if enrichment.get("stock") is not None else enrichment.get("total_stock"),
        "MOQ": enrichment.get("min_order_qty"),
        "Packaging": enrichment.get("packaging"),
    }
    params = dict(enrichment.get("parameters") or {})
    mode = str(parameter_mode or "summary").casefold()
    if mode not in {"none", "summary", "all"}:
        raise LibraryError("parameter_mode must be none, summary, or all")
    if mode == "summary" and params:
        result["Features"] = _feature_summary(params)
    elif mode == "all":
        for key, value in params.items():
            if value not in (None, ""):
                result[f"Spec: {key}"] = str(value)
    return {str(key): str(value) for key, value in result.items() if value not in (None, "")}


def build_property_patch_plan(
    catalog_root: str | Path,
    *,
    symbol_ids: Iterable[str] | None = None,
    parameter_mode: str = "summary",
    overwrite_existing: bool = False,
) -> dict[str, Any]:
    catalog = load_ad_catalog(catalog_root)
    wanted = selected_ids(symbol_ids)
    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for asset_id, symbol in sorted((catalog.get("symbols") or {}).items()):
        if wanted and asset_id not in wanted:
            continue
        desired = desired_symbol_properties(symbol, parameter_mode=parameter_mode)
        if not desired:
            continue

        existing = {str(key): str(value) for key, value in (symbol.get("parameters") or {}).items()}
        existing_by_norm = {normalize_text(key): (key, value) for key, value in existing.items()}
        actions: list[dict[str, Any]] = []
        for name, value in desired.items():
            old_pair = existing_by_norm.get(normalize_text(name))
            if old_pair is None:
                operation, old_name, old_value = "add", None, None
            else:
                old_name, old_value = old_pair
                if str(old_value) == str(value):
                    operation = "keep"
                elif overwrite_existing:
                    operation = "update"
                else:
                    operation = "conflict"
            action = {
                "operation": operation,
                "parameter": name,
                "existing_parameter": old_name,
                "old": old_value,
                "new": value,
            }
            actions.append(action)
            if operation == "conflict":
                conflicts.append(
                    {
                        "asset_id": asset_id,
                        "symbol": symbol.get("name"),
                        "library_path": symbol.get("library_path"),
                        **action,
                    }
                )

        rows.append(
            {
                "asset_id": asset_id,
                "symbol": symbol.get("name"),
                "library_path": symbol.get("library_path"),
                "actions": actions,
                "add_count": sum(action["operation"] == "add" for action in actions),
                "update_count": sum(action["operation"] == "update" for action in actions),
                "conflict_count": sum(action["operation"] == "conflict" for action in actions),
            }
        )

    return {
        "ok": True,
        "catalog_root": str(resolved_path(catalog_root)),
        "symbol_count": len(rows),
        "add_count": sum(row["add_count"] for row in rows),
        "update_count": sum(row["update_count"] for row in rows),
        "conflict_count": len(conflicts),
        "overwrite_existing": overwrite_existing,
        "parameter_mode": parameter_mode,
        "symbols": rows,
        "conflicts": conflicts,
        "library_write_performed": False,
        "symbol_graphics_modified": False,
        "footprint_links_modified": False,
    }


def _find_symbol(library: Any, name: str) -> Any | None:
    getter = getattr(library, "get_symbol", None)
    if callable(getter):
        try:
            found = getter(name)
            if found is not None:
                return found
        except Exception:
            pass
    target = normalize_text(name)
    for symbol in list(getattr(library, "symbols", []) or []):
        if normalize_text(getattr(symbol, "name", "")) == target:
            return symbol
    return None


def _parameter_objects(symbol: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for parameter in list(getattr(symbol, "parameters", []) or []):
        name = str(getattr(parameter, "name", "") or "").strip()
        if name:
            result[normalize_text(name)] = parameter
    return result


def _apply_symbol_actions(symbol: Any, row: dict[str, Any], library_path: Path) -> list[dict[str, Any]]:
    existing = _parameter_objects(symbol)
    pending: list[dict[str, Any]] = []
    for action in row["actions"]:
        key = normalize_text(action["parameter"])
        parameter = existing.get(key)
        if action["operation"] == "add":
            created = symbol.add_parameter(action["parameter"], action["new"])
            if created is not None:
                existing[key] = created
        elif action["operation"] == "update":
            if parameter is None:
                created = symbol.add_parameter(action["parameter"], action["new"])
                if created is not None:
                    existing[key] = created
            else:
                setattr(parameter, "text", action["new"])
        pending.append(
            {
                "asset_id": row["asset_id"],
                "library_path": str(library_path),
                "symbol": row["symbol"],
                **action,
            }
        )
    return pending


def apply_property_patch(
    catalog_root: str | Path,
    *,
    symbol_ids: Iterable[str] | None = None,
    parameter_mode: str = "summary",
    overwrite_existing: bool = False,
    dry_run: bool = True,
    backup: bool = True,
) -> dict[str, Any]:
    plan = build_property_patch_plan(
        catalog_root,
        symbol_ids=symbol_ids,
        parameter_mode=parameter_mode,
        overwrite_existing=overwrite_existing,
    )
    if dry_run:
        return {
            **plan,
            "dry_run": True,
            "applied": False,
            "backup_paths": [],
            "note": "dry-run only; existing SchLib files were not modified",
        }

    try:
        from altium_monkey import AltiumSchLib
    except ImportError as exc:
        raise LibraryError("property apply requires component-sourcing-mcp[altium]; review the dependency security notes before installing") from exc

    by_library: dict[str, list[dict[str, Any]]] = {}
    for row in plan["symbols"]:
        actionable = [action for action in row["actions"] if action["operation"] in {"add", "update"}]
        if actionable:
            by_library.setdefault(str(row["library_path"]), []).append({**row, "actions": actionable})

    backup_paths: list[str] = []
    applied_actions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for library_path, symbol_rows in by_library.items():
        path = resolved_path(library_path)
        if not path.is_file():
            failures.append({"library_path": str(path), "error": "SchLib not found"})
            continue

        try:
            if backup:
                backup_paths.append(backup_schlib(path, catalog_root))
            library = AltiumSchLib(path)
            library_actions: list[dict[str, Any]] = []
            for row in symbol_rows:
                symbol = _find_symbol(library, str(row["symbol"]))
                if symbol is None:
                    raise LibraryError(f"symbol not found during apply: {row['symbol']} in {path}")
                library_actions.extend(_apply_symbol_actions(symbol, row, path))

            # Commit the in-memory changes first. Catalog metadata is updated only
            # after this save succeeds, so a failed library write cannot claim a
            # property patch that never reached disk.
            library.save(path)
            applied_actions.extend(library_actions)
        except Exception as exc:
            failures.append({"library_path": str(path), "error": f"{type(exc).__name__}: {exc}"})

    catalog = load_ad_catalog(catalog_root)
    by_asset: dict[str, list[dict[str, Any]]] = {}
    for action in applied_actions:
        by_asset.setdefault(action["asset_id"], []).append(action)

    for asset_id, actions in by_asset.items():
        symbol = (catalog.get("symbols") or {}).get(asset_id)
        if not symbol:
            continue
        params = dict(symbol.get("parameters") or {})
        for action in actions:
            name = action.get("existing_parameter") or action["parameter"]
            params[str(name)] = str(action["new"])
        symbol["parameters"] = params
        symbol["property_patch"] = {
            "last_applied_at": utc_now(),
            "applied_count": len(actions),
            "overwrite_existing": overwrite_existing,
            "parameter_mode": parameter_mode,
        }

    if applied_actions:
        save_ad_catalog(catalog_root, catalog)

    return {
        **plan,
        "ok": not failures,
        "dry_run": False,
        "library_write_performed": bool(applied_actions),
        "applied": bool(applied_actions) and not failures,
        "applied_action_count": len(applied_actions),
        "applied_actions": applied_actions,
        "backup_paths": backup_paths,
        "failures": failures,
        "rescan_recommended": bool(applied_actions),
        "symbol_graphics_modified": False,
        "footprint_links_modified": False,
        "note": "Only successfully saved SchLib parameter records update catalog metadata. Symbol graphics and footprint implementation links are not replaced.",
    }
