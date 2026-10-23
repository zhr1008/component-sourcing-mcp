from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any

from .altium import AltiumImportError, require_altium_parser
from .ad_catalog import CATALOG_FILENAME, load_ad_catalog, search_ad_catalog
from .ad_enrichment import (
    build_verification_queue,
    enrich_ad_catalog,
    record_datasheet_verification,
    sync_ad_catalog_datasheets,
)
from .ad_export import export_ad_catalog_complete
from .ad_properties import apply_property_patch, build_property_patch_plan
from .ad_refresh import refresh_ad_catalog
from .ad_scan import scan_altium_libraries_resilient
from .library_base import LibraryError
from .server_base import _json, client, mcp


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


@mcp.tool(output_schema=None)
def szlcsc_ad_library_status(catalog_root: str = "altium-catalog") -> str:
    """查看现有 Altium SchLib/PcbLib 目录索引状态、解析依赖和 Datasheet 权威核验策略。"""
    try:
        root = Path(catalog_root).expanduser().resolve()
        catalog = load_ad_catalog(root)
        symbols = catalog.get("symbols") or {}
        footprints = catalog.get("footprints") or {}
        status_counts: dict[str, int] = {}
        verification_counts: dict[str, int] = {}
        stale_assets = 0
        for row in symbols.values():
            status = str((row.get("enrichment") or {}).get("status") or "not_enriched")
            status_counts[status] = status_counts.get(status, 0) + 1
            verified = str((row.get("verification") or {}).get("status") or "not_verified")
            verification_counts[verified] = verification_counts.get(verified, 0) + 1
            stale_assets += int(bool(row.get("catalog_stale")))
        monkey_version = _package_version("altium-monkey")
        try:
            require_altium_parser()
            parser_ready = True
        except AltiumImportError:
            parser_ready = False
        return _json({
            "catalog_root": str(root),
            "catalog_path": str(root / CATALOG_FILENAME),
            "catalog_exists": (root / CATALOG_FILENAME).is_file(),
            "schema_version": catalog.get("schema_version"),
            "revision": catalog.get("revision"),
            "scan_roots": catalog.get("scan_roots") or [],
            "library_file_count": len(catalog.get("files") or {}),
            "symbol_count": len(symbols),
            "footprint_count": len(footprints),
            "stale_asset_count": stale_assets,
            "identity_status_counts": status_counts,
            "verification_status_counts": verification_counts,
            "altium_monkey_version": monkey_version,
            "parser_ready": parser_ready,
            "parser_install_extra": "component-sourcing-mcp[altium]" if not parser_ready else None,
            "authority_policy": "Datasheet is the sole authority for final pin/package/footprint/electrical verification. SZLCSC/EasyEDA CAD is auxiliary only.",
            "symbol_policy": "existing Altium symbol graphics are preserved; SZLCSC/EasyEDA symbols are never substituted by this workflow",
        })
    except LibraryError as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_scan(
    paths: list[str],
    catalog_root: str = "altium-catalog",
    recursive: bool = True,
    hash_files: bool = False,
    reuse_unchanged: bool = True,
) -> str:
    """直接读取现有 SchLib/PcbLib，建立 symbol/pin/参数/footprint/pad/库路径目录；不替换 Symbol 图形。"""
    try:
        if not paths:
            return _json({"error": "paths must contain at least one SchLib/PcbLib file or directory"})
        return _json(scan_altium_libraries_resilient(
            paths,
            catalog_root=catalog_root,
            recursive=recursive,
            hash_files=hash_files,
            reuse_unchanged=reuse_unchanged,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_search(
    catalog_root: str = "altium-catalog",
    query: str | None = None,
    resolved_only: bool = False,
    verification_status: str | None = None,
    limit: int = 200,
) -> str:
    """离线查询现有 Altium 元件目录，可按 Symbol/库名/MPN/C编号/品牌/分类检索。"""
    try:
        rows = search_ad_catalog(
            catalog_root,
            query=query,
            resolved_only=resolved_only,
            verification_status=verification_status,
            limit=limit,
        )
        return _json({
            "catalog_root": str(Path(catalog_root).expanduser().resolve()),
            "count": len(rows),
            "results": rows,
            "offline": True,
        })
    except LibraryError as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_enrich(
    catalog_root: str = "altium-catalog",
    symbol_ids: list[str] | None = None,
    force: bool = False,
    search_limit: int = 12,
) -> str:
    """用国内 SZLCSC 补齐现有 Symbol 的品牌/C编号/MPN/分类/价格/库存/参数/资料链接；仅精确身份自动绑定。"""
    try:
        return _json(enrich_ad_catalog(
            client,
            catalog_root,
            symbol_ids=symbol_ids,
            force=force,
            search_limit=search_limit,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_datasheets(
    catalog_root: str = "altium-catalog",
    symbol_ids: list[str] | None = None,
    force: bool = False,
) -> str:
    """为已精确识别的 Altium 元件下载 Datasheet；最终 pin/package/footprint 核验只能使用这些 Datasheet 证据。"""
    try:
        return _json(sync_ad_catalog_datasheets(
            client,
            catalog_root,
            symbol_ids=symbol_ids,
            force=force,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_verification_queue(
    catalog_root: str = "altium-catalog",
    symbol_ids: list[str] | None = None,
    include_verified: bool = False,
) -> str:
    """生成 Datasheet 人工/Agent 核验队列，包含本地 Symbol pins、关联 Footprint pads/几何及所需证据格式。"""
    try:
        return _json(build_verification_queue(
            catalog_root,
            symbol_ids=symbol_ids,
            include_verified=include_verified,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_verify(
    asset_id: str,
    datasheet_evidence: dict[str, Any],
    catalog_root: str = "altium-catalog",
) -> str:
    """记录基于 Datasheet 页码证据的 Symbol↔Footprint 核验；EasyEDA/立创封装本身不能把结果置为 verified。"""
    try:
        return _json(record_datasheet_verification(
            catalog_root,
            asset_id=asset_id,
            evidence=datasheet_evidence,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_property_plan(
    catalog_root: str = "altium-catalog",
    symbol_ids: list[str] | None = None,
    parameter_mode: str = "summary",
    overwrite_existing: bool = False,
) -> str:
    """只读预览 SchLib 属性补全计划；默认不覆盖已有不同字段，且永不替换 Symbol 图形/Footprint 关联。"""
    try:
        return _json(build_property_patch_plan(
            catalog_root,
            symbol_ids=symbol_ids,
            parameter_mode=parameter_mode,
            overwrite_existing=overwrite_existing,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_property_apply(
    catalog_root: str = "altium-catalog",
    symbol_ids: list[str] | None = None,
    parameter_mode: str = "summary",
    overwrite_existing: bool = False,
    dry_run: bool = True,
    backup: bool = True,
) -> str:
    """把 enrichment 写回现有 SchLib 参数；默认 dry-run+backup，只改属性记录，不改 Symbol 图形和 Footprint implementation。"""
    try:
        return _json(apply_property_patch(
            catalog_root,
            symbol_ids=symbol_ids,
            parameter_mode=parameter_mode,
            overwrite_existing=overwrite_existing,
            dry_run=dry_run,
            backup=backup,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_export(
    catalog_root: str = "altium-catalog",
    output_dir: str | None = None,
    export_format: str = "both",
) -> str:
    """导出完整 AD 元件索引：库位置、属性、封装、价格、资料链接、完整商城参数和核验状态，支持 JSON/Excel友好CSV。"""
    try:
        return _json(export_ad_catalog_complete(
            catalog_root,
            output_dir=output_dir,
            export_format=export_format,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_ad_library_refresh(
    catalog_root: str = "altium-catalog",
    max_age_hours: float = 24.0,
    refresh_metadata: bool = True,
    hash_files: bool = False,
) -> str:
    """增量更新现有 AD 库目录：只重扫变化库文件，并按 age 刷新 SZLCSC 元数据；价格库存变化不会使已核验封装失效。"""
    try:
        return _json(refresh_ad_catalog(
            client,
            catalog_root,
            max_age_hours=max_age_hours,
            refresh_metadata=refresh_metadata,
            hash_files=hash_files,
        ))
    except (LibraryError, OSError, ValueError) as exc:
        return _json({"error": exc})
