from pathlib import Path

from .server_base import *  # noqa: F401,F403
from .server_base import SZLCSCError, _json, client, main, mcp
from .library import (
    LibraryError,
    audit_library,
    export_library,
    load_index,
    migrate_library,
    plan_library_migration,
    preview_entry_changes,
    repair_library,
    scan_orphan_files,
    search_entries,
)


@mcp.tool(output_schema=None)
def szlcsc_library_search(
    library_root: str = "szlcsc-library",
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
) -> str:
    """离线检索长期器件库，可按型号/品牌/封装/分类/库存/CAD与归档资产筛选。"""
    try:
        index = load_index(library_root)
        rows = search_entries(
            index, query=query, category=category, brand=brand, package=package,
            in_stock=in_stock, has_datasheet=has_datasheet, has_schlib=has_schlib,
            has_pcblib=has_pcblib, has_step=has_step,
            cad_review_required=cad_review_required, only_cad_owners=only_cad_owners,
            sort_by=sort_by, limit=limit,
        )
        return _json({
            "library_root": str(Path(library_root).expanduser().resolve()),
            "count": len(rows), "sort_by": sort_by, "results": rows, "offline": True,
        })
    except LibraryError as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_sync_preview(
    library_root: str = "szlcsc-library",
    identifiers: list[str] | None = None,
) -> str:
    """同步前只读预览：在线读取最新 SZLCSC 元数据并与本地库比较，不写 library-index。"""
    try:
        index = load_index(library_root)
    except LibraryError as exc:
        return _json({"error": exc})

    parts = index.get("parts") or {}
    targets = identifiers or sorted(parts.keys())
    results: list[dict] = []
    for identifier in targets[:500]:
        try:
            part = client.get_part(identifier)
            code = str(part.lcsc_code or "").upper()
            if not code:
                raise SZLCSCError(f"No LCSC C-code resolved for {identifier!r}")
            old = parts.get(code)
            if old is None:
                results.append({
                    "identifier": identifier,
                    "lcsc_code": code,
                    "ok": False,
                    "status": "not_in_library",
                    "message": "resolved part is not present in the managed library",
                })
                continue
            preview = preview_entry_changes(old, part)
            results.append({
                "identifier": identifier,
                "lcsc_code": code,
                "ok": True,
                **preview,
            })
        except SZLCSCError as exc:
            results.append({"identifier": identifier, "ok": False, "status": "lookup_error", "error": str(exc)})

    return _json({
        "library_root": str(Path(library_root).expanduser().resolve()),
        "count": len(results),
        "changed": sum(1 for row in results if row.get("changed")),
        "would_require_cad_review": sum(1 for row in results if row.get("would_require_cad_review")),
        "results": results,
        "index_revision": int(index.get("revision") or 0),
        "library_write_performed": False,
        "cad_policy": "preview never rebuilds CAD; MPN/package changes are only flagged for review",
    })


@mcp.tool(output_schema=None)
def szlcsc_library_export(
    library_root: str = "szlcsc-library",
    output_dir: str | None = None,
    export_format: str = "both",
    include_asset_paths: bool = True,
    only_cad_owners: bool = False,
) -> str:
    """离线导出长期器件库清单为 JSON/CSV；CSV 使用 UTF-8 BOM，便于 Excel 直接打开中文。"""
    try:
        return _json(export_library(
            library_root,
            output_dir=output_dir,
            export_format=export_format,
            include_asset_paths=include_asset_paths,
            only_cad_owners=only_cad_owners,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_orphans(
    library_root: str = "szlcsc-library",
    include_classified: bool = True,
    include_flat: bool = True,
    include_hashes: bool = False,
    limit: int = 5000,
) -> str:
    """只读扫描 classified/flat 中未被 library-index 引用的孤儿文件；不会自动删除。"""
    try:
        return _json(scan_orphan_files(
            library_root,
            include_classified=include_classified,
            include_flat=include_flat,
            include_hashes=include_hashes,
            limit=limit,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_migration_plan(
    library_root: str = "szlcsc-library",
    identifiers: list[str] | None = None,
) -> str:
    """只读生成长期库迁移计划：识别旧 archive layout/命名/临时 Altium 资产，不修改任何文件。"""
    try:
        return _json(plan_library_migration(library_root, identifiers=identifiers))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_migrate(
    library_root: str = "szlcsc-library",
    identifiers: list[str] | None = None,
    dry_run: bool = True,
    backup_index: bool = True,
) -> str:
    """迁移长期库到当前 archive layout；默认 dry-run，真实执行前先备份 index，旧资产只复制不删除。"""
    try:
        return _json(migrate_library(
            library_root,
            identifiers=identifiers,
            dry_run=dry_run,
            backup_index=backup_index,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_audit(
    library_root: str = "szlcsc-library",
    identifiers: list[str] | None = None,
    verify_hashes: bool = True,
) -> str:
    """只读审计长期器件库：检查 classified/flat、CAD owner、索引与文件完整性。"""
    try:
        return _json(audit_library(library_root, identifiers=identifiers, verify_hashes=verify_hashes))
    except LibraryError as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_repair(
    library_root: str = "szlcsc-library",
    identifiers: list[str] | None = None,
    repair_flat: bool = True,
    reconcile_cad: bool = True,
    dry_run: bool = True,
) -> str:
    """安全修复长期器件库；默认 dry-run，只恢复 flat 镜像和 CAD owner/index 一致性，不重新下载或生成 CAD。"""
    try:
        return _json(repair_library(
            library_root, identifiers=identifiers, repair_flat=repair_flat,
            reconcile_cad=reconcile_cad, dry_run=dry_run,
        ))
    except (LibraryError, OSError) as exc:
        return _json({"error": exc})


# Registers the v0.6 existing-Altium-library catalog/enrichment tools on the same MCP instance.
from . import server_ad as _server_ad  # noqa: E402,F401
from . import server_sourcing as _server_sourcing  # noqa: E402,F401
