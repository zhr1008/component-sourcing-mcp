from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .library_base import (
    _index_path,
    _norm_text,
    _sha256,
    load_index,
    rebuild_indexes,
    reconcile_cad_group,
    save_index,
    summarize_index,
)
from .storage import mirror_file

def _asset_state(entry: dict[str, Any]) -> dict[str, bool]:
    """Return availability flags based on canonical classified archive files."""
    archive = entry.get("archive") or {}
    assets = archive.get("assets") or {}
    result: dict[str, bool] = {}
    for kind in ("datasheet", "schlib", "pcblib", "step", "reports"):
        row = assets.get(kind) or {}
        classified = [Path(str(p)) for p in (row.get("classified") or []) if str(p).strip()]
        result[f"has_{kind}"] = any(path.is_file() for path in classified)
    return result


def search_entries(
    index: dict[str, Any],
    *,
    query: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    package: str | None = None,
    in_stock: bool | None = None,
    has_datasheet: bool | None = None,
    has_schlib: bool | None = None,
    has_pcblib: bool | None = None,
    has_step: bool | None = None,
    cad_review_required: bool | None = None,
    only_cad_owners: bool = False,
    sort_by: str = "relevance",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search the managed library with procurement/CAD/archive filters.

    The result is deliberately local-only: it never contacts SZLCSC and is safe
    to use for large libraries while offline.
    """
    needle = _norm_text(query)
    tokens = [token for token in re.split(r"\s+", needle) if token]
    category_n = _norm_text(category)
    brand_n = _norm_text(brand)
    package_n = _norm_text(package)
    rows: list[dict[str, Any]] = []
    for code, entry in sorted((index.get("parts") or {}).items()):
        if only_cad_owners and str(entry.get("cad_owner") or code) != code:
            continue
        if category_n and category_n not in {
            _norm_text(entry.get("category")),
            _norm_text(entry.get("category_slug")),
        }:
            continue
        if brand_n and brand_n not in _norm_text(entry.get("brand")):
            continue
        if package_n and package_n not in _norm_text(entry.get("package")):
            continue
        if in_stock is not None and bool(entry.get("in_stock")) != in_stock:
            continue
        if cad_review_required is not None and bool(entry.get("cad_review_required")) != cad_review_required:
            continue

        state = _asset_state(entry)
        wanted_assets = {
            "has_datasheet": has_datasheet,
            "has_schlib": has_schlib,
            "has_pcblib": has_pcblib,
            "has_step": has_step,
        }
        if any(expected is not None and state.get(key) != expected for key, expected in wanted_assets.items()):
            continue

        haystack_fields = {
            "lcsc_code": str(entry.get("lcsc_code") or ""),
            "mpn": str(entry.get("mpn") or ""),
            "brand": str(entry.get("brand") or ""),
            "package": str(entry.get("package") or ""),
            "category": str(entry.get("category") or ""),
        }
        haystack = " ".join(haystack_fields.values()).casefold()
        if tokens and not all(token in haystack for token in tokens):
            continue

        score = 0
        matched_fields: list[str] = []
        if tokens:
            for field, raw in haystack_fields.items():
                text = raw.casefold()
                field_hits = sum(1 for token in tokens if token in text)
                if field_hits:
                    matched_fields.append(field)
                    weight = 8 if field in {"lcsc_code", "mpn"} else 3
                    score += field_hits * weight
            if needle and needle in haystack_fields["mpn"].casefold():
                score += 12
            if needle and needle == haystack_fields["lcsc_code"].casefold():
                score += 20

        row = {
            "lcsc_code": code,
            "mpn": entry.get("mpn"),
            "brand": entry.get("brand"),
            "package": entry.get("package"),
            "category": entry.get("category"),
            "small_batch_price_cny": entry.get("small_batch_price_cny"),
            "stock": entry.get("stock"),
            "total_stock": entry.get("total_stock"),
            "in_stock": entry.get("in_stock"),
            "cad_owner": entry.get("cad_owner") or code,
            "cad_shared": bool(entry.get("cad_shared")),
            "cad_review_required": bool(entry.get("cad_review_required")),
            "assets": state,
            "archive_basename": (entry.get("archive") or {}).get("basename"),
            "match_score": score,
            "matched_fields": matched_fields,
        }
        rows.append(row)

    sort_key = str(sort_by or "relevance").casefold()
    if sort_key == "price":
        rows.sort(key=lambda row: (row.get("small_batch_price_cny") is None, row.get("small_batch_price_cny") or float("inf"), row["lcsc_code"]))
    elif sort_key == "stock":
        rows.sort(key=lambda row: (-(int(row.get("total_stock") or row.get("stock") or 0)), row["lcsc_code"]))
    elif sort_key == "mpn":
        rows.sort(key=lambda row: (_norm_text(row.get("mpn")), row["lcsc_code"]))
    else:
        rows.sort(key=lambda row: (-int(row.get("match_score") or 0), _norm_text(row.get("mpn")), row["lcsc_code"]))
    return rows[: max(1, min(int(limit), 1000))]


def audit_library(
    root: str | Path,
    *,
    identifiers: Iterable[str] | None = None,
    verify_hashes: bool = True,
) -> dict[str, Any]:
    """Audit index/CAD/archive/flat-mirror consistency without changing files."""
    root_path = Path(root).expanduser().resolve()
    index = load_index(root_path)
    wanted = {str(x).strip().upper() for x in (identifiers or []) if str(x).strip()}
    issues: list[dict[str, Any]] = []
    checked = 0

    def add_issue(code: str, severity: str, kind: str, message: str, *, path: str | None = None, repairable: bool = False) -> None:
        issues.append({
            "lcsc_code": code,
            "severity": severity,
            "kind": kind,
            "message": message,
            "path": path,
            "repairable": repairable,
        })

    parts = index.get("parts") or {}
    for code, entry in sorted(parts.items()):
        if wanted and code.upper() not in wanted:
            continue
        checked += 1
        archive = entry.get("archive") or {}
        basename = str(archive.get("basename") or "")
        assets = archive.get("assets") or {}
        if not archive:
            add_issue(code, "warning", "archive_missing", "part has no archive metadata", repairable=False)
        elif not basename:
            add_issue(code, "warning", "basename_missing", "archive basename is missing", repairable=False)

        for kind in ("datasheet", "schlib", "pcblib", "step", "reports"):
            row = assets.get(kind) or {}
            classified = [Path(str(p)) for p in (row.get("classified") or []) if str(p).strip()]
            flat = [Path(str(p)) for p in (row.get("flat") or []) if str(p).strip()]
            for path in classified:
                if not path.is_file():
                    add_issue(code, "error", f"classified_{kind}_missing", "classified archive file is missing", path=str(path), repairable=False)
                elif basename and not path.name.startswith(basename):
                    add_issue(code, "warning", f"classified_{kind}_name", "classified filename does not start with archive basename", path=str(path), repairable=False)

            if kind == "reports":
                continue
            existing_classified = [path for path in classified if path.is_file()]
            if existing_classified and len(flat) < len(existing_classified):
                add_issue(code, "warning", f"flat_{kind}_missing", "flat mirror entry is missing", repairable=True)
            for idx, source in enumerate(existing_classified):
                target = flat[idx] if idx < len(flat) else root_path / "flat" / source.name
                if not target.is_file():
                    add_issue(code, "warning", f"flat_{kind}_missing", "flat mirror file is missing", path=str(target), repairable=True)
                    continue
                if verify_hashes and _sha256(source) != _sha256(target):
                    add_issue(code, "warning", f"flat_{kind}_mismatch", "flat mirror content differs from classified archive", path=str(target), repairable=True)

        owner = str(entry.get("cad_owner") or code).upper()
        owner_entry = parts.get(owner)
        if owner_entry is None:
            add_issue(code, "error", "cad_owner_missing", f"cad_owner {owner} is not present in index", repairable=True)
        elif entry.get("cad_key") and owner_entry.get("cad_key") != entry.get("cad_key"):
            add_issue(code, "error", "cad_owner_key_mismatch", "cad_owner has a different CAD key", repairable=True)
        if bool(entry.get("cad_shared")) != (owner != code):
            add_issue(code, "warning", "cad_shared_flag", "cad_shared flag is inconsistent with cad_owner", repairable=True)

        altium = entry.get("altium") or {}
        state = _asset_state(entry)
        if altium.get("has_schlib") and not state.get("has_schlib"):
            raw_case = str(altium.get("case_dir") or "")
            case_has = bool(raw_case and Path(raw_case).is_dir() and list(Path(raw_case).glob("*.SchLib")))
            if not case_has:
                add_issue(code, "error", "schlib_unavailable", "index says SchLib exists but neither archive nor source case has one", repairable=False)
        if altium.get("has_pcblib") and not state.get("has_pcblib"):
            raw_case = str(altium.get("case_dir") or "")
            case_has = bool(raw_case and Path(raw_case).is_dir() and list(Path(raw_case).glob("*.PcbLib")))
            if not case_has:
                add_issue(code, "error", "pcblib_unavailable", "index says PcbLib exists but neither archive nor source case has one", repairable=False)

    severity_counts = {
        level: sum(1 for item in issues if item["severity"] == level)
        for level in ("error", "warning", "info")
    }
    return {
        "ok": severity_counts["error"] == 0,
        "library_root": str(root_path),
        "checked_parts": checked,
        "issue_count": len(issues),
        "severity_counts": severity_counts,
        "repairable_count": sum(1 for item in issues if item.get("repairable")),
        "issues": issues,
        "summary": summarize_index(index),
    }


def repair_library(
    root: str | Path,
    *,
    identifiers: Iterable[str] | None = None,
    repair_flat: bool = True,
    reconcile_cad: bool = True,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Repair safe, deterministic library inconsistencies.

    This never regenerates CAD or downloads missing source assets. It only
    rebuilds CAD indexes/ownership and recreates flat mirrors from canonical
    classified files.
    """
    root_path = Path(root).expanduser().resolve()
    index = load_index(root_path)
    wanted = {str(x).strip().upper() for x in (identifiers or []) if str(x).strip()}
    actions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    if reconcile_cad:
        before = {
            code: (entry.get("cad_owner"), bool(entry.get("cad_shared")))
            for code, entry in (index.get("parts") or {}).items()
        }
        rebuild_indexes(index)
        for key in list((index.get("cad_index") or {}).keys()):
            reconcile_cad_group(index, key)
        for code, entry in (index.get("parts") or {}).items():
            if wanted and code.upper() not in wanted:
                continue
            after = (entry.get("cad_owner"), bool(entry.get("cad_shared")))
            if before.get(code) != after:
                actions.append({
                    "lcsc_code": code,
                    "action": "reconcile_cad_owner",
                    "before": before.get(code),
                    "after": after,
                })

    for code, entry in sorted((index.get("parts") or {}).items()):
        if wanted and code.upper() not in wanted:
            continue
        archive = entry.get("archive") or {}
        assets = archive.get("assets") or {}
        for kind in ("datasheet", "schlib", "pcblib", "step"):
            row = assets.get(kind) or {}
            classified = [Path(str(p)) for p in (row.get("classified") or []) if str(p).strip()]
            if not classified:
                continue
            flat_paths: list[str] = []
            for source in classified:
                if not source.is_file():
                    unresolved.append({
                        "lcsc_code": code,
                        "kind": kind,
                        "reason": "classified_source_missing",
                        "path": str(source),
                    })
                    continue
                target = root_path / "flat" / source.name
                needs_copy = not target.is_file() or _sha256(source) != _sha256(target)
                if repair_flat and needs_copy:
                    actions.append({
                        "lcsc_code": code,
                        "action": "restore_flat_mirror",
                        "kind": kind,
                        "source": str(source),
                        "target": str(target),
                    })
                    if not dry_run:
                        mirror_file(source, target, overwrite=True)
                if target.is_file() or (repair_flat and needs_copy and not dry_run):
                    flat_paths.append(str(target))
                elif not dry_run and not needs_copy:
                    flat_paths.append(str(target))
                elif dry_run:
                    flat_paths.append(str(target))
            if repair_flat and flat_paths:
                row["flat"] = flat_paths
                assets[kind] = row
        archive["assets"] = assets
        entry["archive"] = archive

    if not dry_run and actions:
        rebuild_indexes(index)
        index_path = save_index(root_path, index)
    else:
        index_path = _index_path(root_path)

    audit = audit_library(root_path, identifiers=wanted or None, verify_hashes=True) if not dry_run else None
    return {
        "ok": not unresolved and (audit is None or audit.get("ok")),
        "dry_run": dry_run,
        "library_root": str(root_path),
        "action_count": len(actions),
        "actions": actions,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "index_path": str(index_path),
        "post_audit": audit,
        "note": "repair never regenerates CAD or redownloads missing classified assets",
    }
