from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .library_base import (
    LibraryError,
    _index_path,
    _sha256,
    load_index,
    rebuild_indexes,
    save_index,
    summarize_index,
)
from .models import Part
from .storage import (
    ARCHIVE_LAYOUT_VERSION,
    ARCHIVE_NAMING_POLICY,
    archive_roots,
    mirror_file,
    safe_segment,
)

_ASSET_KINDS = ("datasheet", "schlib", "pcblib", "step", "reports")
_DEFAULT_SUFFIX = {
    "datasheet": ".pdf",
    "schlib": ".SchLib",
    "pcblib": ".PcbLib",
    "step": ".step",
    "reports": ".json",
}
_CASE_PATTERNS = {
    "schlib": ("*.SchLib", "*.schlib"),
    "pcblib": ("*.PcbLib", "*.pcblib"),
    "step": ("*.step", "*.STEP", "*.stp", "*.STP"),
}


def _resolve(root: Path, raw: Any) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _part_from_entry(code: str, entry: dict[str, Any]) -> Part:
    return Part(
        lcsc_code=code,
        mpn=entry.get("mpn"),
        brand=entry.get("brand"),
        package=entry.get("package"),
        category=entry.get("category"),
        datasheet_url=entry.get("datasheet_url"),
        product_url=entry.get("product_url"),
    )


def _dedupe_existing(paths: Iterable[Path]) -> list[Path]:
    rows: list[Path] = []
    seen_paths: set[str] = set()
    seen_content: set[tuple[str, str]] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        key = str(resolved).casefold()
        if key in seen_paths or not resolved.is_file():
            continue
        seen_paths.add(key)
        digest = _sha256(resolved)
        content_key = (str(digest or key), resolved.suffix.casefold())
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        rows.append(resolved)
    return rows


def _source_candidates(root: Path, entry: dict[str, Any], kind: str) -> tuple[list[Path], list[str], bool]:
    archive = entry.get("archive") or {}
    assets = archive.get("assets") or {}
    row = assets.get(kind) or {}
    referenced: list[Path] = []
    missing: list[str] = []
    for view in ("classified", "flat"):
        for raw in row.get(view) or []:
            if not str(raw).strip():
                continue
            path = _resolve(root, raw)
            if path.is_file():
                referenced.append(path)
            else:
                missing.append(str(path))

    case_used = False
    if kind in _CASE_PATTERNS:
        raw_case = str((entry.get("altium") or {}).get("case_dir") or "").strip()
        if raw_case:
            case_dir = _resolve(root, raw_case)
            if case_dir.is_dir():
                case_rows: list[Path] = []
                for pattern in _CASE_PATTERNS[kind]:
                    case_rows.extend(case_dir.glob(pattern))
                if case_rows:
                    case_used = True
                    referenced.extend(case_rows)

    return _dedupe_existing(referenced), missing, case_used


def _numbered_target(directory: Path, basename: str, source: Path, kind: str, index: int) -> Path:
    suffix = source.suffix or _DEFAULT_SUFFIX[kind]
    extra = "" if index == 0 else f"-{index + 1}"
    return directory / f"{basename}{extra}{suffix}"


def _report_target(directory: Path, basename: str, source: Path, index: int) -> Path:
    source_resolved = source.resolve(strict=False)
    directory_resolved = directory.resolve(strict=False)
    # A report already living in the canonical report directory with the current
    # basename is stable. Keep it exactly as-is so repeated migration plans are
    # idempotent even when report roles themselves contain numbering.
    if source_resolved.parent == directory_resolved and source.name.startswith(f"{basename}-"):
        return source_resolved

    suffix = source.suffix or ".json"
    role = safe_segment(source.stem, "report", max_len=80)
    prefix = basename.casefold()
    if role.casefold().startswith(prefix):
        role = role[len(basename):].strip("-_ ") or "report"
    extra = "" if index == 0 else f"-{index + 1}"
    return directory / f"{basename}-{role}{extra}{suffix}"


def _targets_for_kind(
    layout: dict[str, Any],
    kind: str,
    sources: list[Path],
) -> tuple[list[str], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    directory = {
        "datasheet": layout["datasheet_dir"],
        "schlib": layout["schlib_dir"],
        "pcblib": layout["pcblib_dir"],
        "step": layout["model3d_dir"],
        "reports": layout["report_dir"],
    }[kind]

    classified_paths: list[str] = []
    flat_paths: list[str] = []
    file_actions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    for idx, source in enumerate(sources):
        classified = (
            _report_target(directory, layout["basename"], source, idx)
            if kind == "reports"
            else _numbered_target(directory, layout["basename"], source, kind, idx)
        )
        targets = [("classified", classified)]
        if kind != "reports":
            targets.append(("flat", layout["flat_root"] / classified.name))

        for view, target in targets:
            source_resolved = source.resolve(strict=False)
            target_resolved = target.resolve(strict=False)
            source_hash = _sha256(source_resolved)
            target_hash = _sha256(target_resolved)
            if source_resolved == target_resolved:
                operation = "keep"
            elif target_resolved.is_file() and source_hash == target_hash:
                operation = "reuse"
            elif target_resolved.is_file():
                operation = "conflict"
                blockers.append({
                    "kind": "target_conflict",
                    "asset_kind": kind,
                    "view": view,
                    "source": str(source_resolved),
                    "target": str(target_resolved),
                    "source_sha256": source_hash,
                    "target_sha256": target_hash,
                    "message": "canonical target already exists with different content",
                })
            else:
                operation = "copy"

            file_actions.append({
                "operation": operation,
                "asset_kind": kind,
                "view": view,
                "source": str(source_resolved),
                "target": str(target_resolved),
                "source_sha256": source_hash,
                "target_sha256": target_hash,
            })
            if view == "classified":
                classified_paths.append(str(target_resolved))
            else:
                flat_paths.append(str(target_resolved))

    return classified_paths, flat_paths, file_actions, blockers


def plan_library_migration(
    root: str | Path,
    *,
    identifiers: Iterable[str] | None = None,
    target_layout_version: int = ARCHIVE_LAYOUT_VERSION,
) -> dict[str, Any]:
    """Build a read-only migration plan for legacy managed-library archives.

    v0.5.3 formalizes archive layout v2. The index schema stays v1; layout v2
    records the naming policy and normalizes recoverable assets into the current
    classified + flat structure. No files or index data are modified here.
    """
    if int(target_layout_version) != ARCHIVE_LAYOUT_VERSION:
        raise LibraryError(
            f"unsupported target layout version {target_layout_version}; supported={ARCHIVE_LAYOUT_VERSION}"
        )

    root_path = Path(root).expanduser().resolve()
    index = load_index(root_path)
    wanted = {str(x).strip().upper() for x in (identifiers or []) if str(x).strip()}
    parts = index.get("parts") or {}
    rows: list[dict[str, Any]] = []

    for code, entry in sorted(parts.items()):
        if wanted and code.upper() not in wanted:
            continue
        part = _part_from_entry(code, entry)
        layout = archive_roots(root_path, part)
        archive = entry.get("archive") or {}
        target_assets: dict[str, dict[str, list[str]]] = {}
        file_actions: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        case_sources: list[str] = []

        for kind in _ASSET_KINDS:
            sources, missing_refs, case_used = _source_candidates(root_path, entry, kind)
            if case_used:
                case_sources.append(kind)
            classified, flat, kind_actions, kind_blockers = _targets_for_kind(layout, kind, sources)
            target_assets[kind] = {"classified": classified, "flat": flat}
            file_actions.extend(kind_actions)
            blockers.extend(kind_blockers)

            if missing_refs:
                if sources:
                    warnings.append({
                        "kind": "missing_legacy_reference_recoverable",
                        "asset_kind": kind,
                        "paths": missing_refs,
                        "message": "some legacy references are missing, but another local source can rebuild the canonical view",
                    })
                else:
                    blockers.append({
                        "kind": "referenced_asset_missing",
                        "asset_kind": kind,
                        "paths": missing_refs,
                        "message": "legacy index references local assets that cannot be found; migration will not drop them silently",
                    })

            if kind in {"schlib", "pcblib"}:
                claimed = bool((entry.get("altium") or {}).get(f"has_{kind}"))
                if claimed and not sources:
                    blockers.append({
                        "kind": "claimed_cad_source_missing",
                        "asset_kind": kind,
                        "message": f"index claims {kind} exists but no archive/case source can be found",
                    })

        target_archive = dict(archive)
        target_archive.update({
            "layout_version": ARCHIVE_LAYOUT_VERSION,
            "naming_policy": ARCHIVE_NAMING_POLICY,
            "basename": layout["basename"],
            "category_segments": layout["category_segments"],
            "classified_root": str(layout["classified_root"]),
            "flat_root": str(layout["flat_root"]),
            "assets": target_assets,
        })

        current_layout = int(archive.get("layout_version") or 0)
        metadata_changed = any([
            current_layout != ARCHIVE_LAYOUT_VERSION,
            archive.get("naming_policy") != ARCHIVE_NAMING_POLICY,
            archive.get("basename") != layout["basename"],
            archive.get("category_segments") != layout["category_segments"],
            archive.get("assets") != target_assets,
        ])
        copy_actions = [item for item in file_actions if item["operation"] == "copy"]
        conflicts = [item for item in file_actions if item["operation"] == "conflict"]
        needs_migration = metadata_changed or bool(copy_actions) or bool(conflicts)

        retained_sources = sorted({
            item["source"]
            for item in file_actions
            if item["operation"] in {"copy", "reuse", "conflict"}
            and item["source"] != item["target"]
        })
        rows.append({
            "lcsc_code": code,
            "mpn": entry.get("mpn"),
            "category": entry.get("category"),
            "current_layout_version": current_layout,
            "target_layout_version": ARCHIVE_LAYOUT_VERSION,
            "current_basename": archive.get("basename"),
            "target_basename": layout["basename"],
            "needs_migration": needs_migration,
            "metadata_update_required": metadata_changed,
            "case_sources_used": case_sources,
            "file_actions": file_actions,
            "copy_count": len(copy_actions),
            "conflict_count": len(conflicts),
            "warnings": warnings,
            "blockers": blockers,
            "retained_legacy_sources": retained_sources,
            "target_archive": target_archive,
        })

    blockers = [
        {"lcsc_code": row["lcsc_code"], **blocker}
        for row in rows
        for blocker in row["blockers"]
    ]
    return {
        "ok": not blockers,
        "safe_to_apply": not blockers,
        "library_root": str(root_path),
        "index_schema_version": int(index.get("schema_version") or 0),
        "index_schema_change_planned": False,
        "target_layout_version": ARCHIVE_LAYOUT_VERSION,
        "naming_policy": ARCHIVE_NAMING_POLICY,
        "part_count": len(rows),
        "needs_migration_count": sum(1 for row in rows if row["needs_migration"]),
        "already_current_count": sum(1 for row in rows if not row["needs_migration"]),
        "copy_count": sum(row["copy_count"] for row in rows),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "parts": rows,
        "library_write_performed": False,
        "source_files_deleted": False,
        "summary": summarize_index(index),
    }


def _backup_index(root: Path, index: dict[str, Any]) -> str | None:
    source = _index_path(root)
    if not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = root / ".migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"library-index-r{int(index.get('revision') or 0)}-{stamp}.json"
    shutil.copy2(source, target)
    return str(target)


def migrate_library(
    root: str | Path,
    *,
    identifiers: Iterable[str] | None = None,
    dry_run: bool = True,
    backup_index: bool = True,
) -> dict[str, Any]:
    """Apply the current managed-library archive migration safely.

    Default is dry-run. Real apply is refused when the plan has blockers. The
    index is backed up before mutation, canonical files are copied (never moved),
    and legacy source files are intentionally retained for rollback/review.
    """
    root_path = Path(root).expanduser().resolve()
    plan = plan_library_migration(root_path, identifiers=identifiers)
    if dry_run:
        return {
            **plan,
            "dry_run": True,
            "applied": False,
            "backup_path": None,
            "index_write_performed": False,
            "note": "dry-run only; no files or index data were changed",
        }

    if not plan["safe_to_apply"]:
        return {
            **plan,
            "dry_run": False,
            "applied": False,
            "backup_path": None,
            "index_write_performed": False,
            "error": "migration blocked; resolve all blockers before apply",
        }

    if plan["needs_migration_count"] == 0:
        return {
            **plan,
            "dry_run": False,
            "applied": False,
            "backup_path": None,
            "index_write_performed": False,
            "note": "library is already on the current archive layout",
        }

    index = load_index(root_path)
    before_sha = _sha256(_index_path(root_path))
    backup_path = _backup_index(root_path, index) if backup_index else None
    copied: list[dict[str, Any]] = []

    for row in plan["parts"]:
        if not row["needs_migration"]:
            continue
        for action in row["file_actions"]:
            if action["operation"] != "copy":
                continue
            source = Path(action["source"])
            target = Path(action["target"])
            if not source.is_file():
                raise LibraryError(f"migration source disappeared after planning: {source}")
            if target.is_file():
                if _sha256(source) != _sha256(target):
                    raise LibraryError(f"migration target changed after planning: {target}")
                continue
            mirror_file(source, target, overwrite=False)
            copied.append({
                "lcsc_code": row["lcsc_code"],
                "asset_kind": action["asset_kind"],
                "view": action["view"],
                "source": str(source),
                "target": str(target),
            })
        entry = (index.get("parts") or {}).get(row["lcsc_code"])
        if entry is not None:
            entry["archive"] = row["target_archive"]

    rebuild_indexes(index)
    index_path = save_index(root_path, index)
    after_sha = _sha256(index_path)

    from .library_management import audit_library

    post_audit = audit_library(root_path, identifiers=identifiers, verify_hashes=True)
    return {
        "ok": bool(post_audit.get("ok")),
        "dry_run": False,
        "applied": True,
        "library_root": str(root_path),
        "target_layout_version": ARCHIVE_LAYOUT_VERSION,
        "migrated_parts": plan["needs_migration_count"],
        "copied_file_count": len(copied),
        "copied_files": copied,
        "backup_path": backup_path,
        "index_path": str(index_path),
        "index_sha256_before": before_sha,
        "index_sha256_after": after_sha,
        "index_write_performed": True,
        "source_files_deleted": False,
        "legacy_sources_retained": sorted({
            source
            for row in plan["parts"]
            for source in row["retained_legacy_sources"]
        }),
        "post_audit": post_audit,
        "rollback_hint": (
            "Restore the backup library-index.json if rollback is needed. Migration copies assets and does not delete legacy sources; "
            "after rollback, use library_orphans to review canonical copies before any manual cleanup."
        ),
    }
