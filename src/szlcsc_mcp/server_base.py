from __future__ import annotations

import json
import re
import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .altium import (
    AltiumImportError,
    altium_status,
    inspect_altium_output,
    require_altium_parser,
    run_altium_import,
    run_altium_import_batch,
    run_altium_merge,
    run_altium_smoke_test,
)
from .barcode import parse_szlcsc_barcode
from .client import SZLCSCClient, SZLCSCError
from .downloader import archive_part_assets, download_part, download_parts, sync_datasheets
from .eda import EasyEDAAssetClient, EasyEDAError
from .models import Part
from . import __version__
from .runtime import SourcingMiddleware
from .schemas import Identifier, Identifiers, Query, ResultLimit, Money, Page, Constraints
from .errors import error_payload
from .library import (
    LibraryError,
    archived_cad_review,
    list_entries,
    load_index,
    save_index,
    stage_for_merge,
    summarize_index,
    upsert_part,
)

@asynccontextmanager
async def _lifespan(app):
    try:
        yield
    finally:
        # Closing pools is blocking work and must not hold the event loop.
        await asyncio.to_thread(client.close)
        await asyncio.to_thread(eda_client.close)
        from .server_sourcing import jlc_client
        await asyncio.to_thread(jlc_client.close)
        from .server_suppliers import registry
        await asyncio.to_thread(registry.close)


mcp = FastMCP("szlcsc", version=__version__, strict_input_validation=True,
              mask_error_details=True, middleware=[SourcingMiddleware()], lifespan=_lifespan)
client = SZLCSCClient()
eda_client = EasyEDAAssetClient(
    cache=client.cache, timeout=client.timeout, request_delay=client.request_delay, retries=client.retries
)
_C_CODE_RE = re.compile(r"^C\d+$", re.IGNORECASE)


def _json(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("error"), Exception):
        value = {**value, **error_payload(value["error"])}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def _resolve_c_code(identifier: str) -> str:
    raw = identifier.strip()
    if _C_CODE_RE.fullmatch(raw):
        return raw.upper()
    part = client.get_part(raw)
    if not part.lcsc_code or not _C_CODE_RE.fullmatch(part.lcsc_code):
        raise SZLCSCError(f"No LCSC C-code resolved for {identifier!r}")
    return part.lcsc_code.upper()


def _part_payload(part: Part) -> dict[str, Any]:
    payload = part.to_dict() | {"in_stock": part.in_stock}
    payload["data_quality"] = {
        "price_available": part.small_batch_price_cny is not None or bool(part.price_tiers),
        "stock_available": part.in_stock is not None,
        "parameters_available": bool(part.parameters),
        "datasheet_available": bool(part.datasheet_url),
        "price_basis": "smallest_quantity_domestic_CNY_tier" if part.small_batch_price_cny is not None else None,
        "missing_means_unknown_not_zero": True,
    }
    return payload


@mcp.tool(output_schema=None)
def szlcsc_search(
    query: Query,
    max_results: ResultLimit = 10,
    package: str | None = None,
    brand: str | None = None,
    category_id: int | None = None,
    in_stock: bool | None = None,
    min_price: Money | None = None,
    max_price: Money | None = None,
    sort: int = 0,
    page: Page = 1,
) -> str:
    """搜索国内立创 SZLCSC 的型号/功能关键词；支持封装、品牌、分类、库存及小批量 CNY 单价和分页。

    精确 C 编号/MPN 详情请用 szlcsc_get_part；电气条件用 szlcsc_parametric_search。
    检查 search_relevance.hard_pass。基础/扩展贴片库请用 szlcsc_jlcpcb_search，库存池不同。
    """
    try:
        parts = client.search(
            query,
            max_results=max_results,
            package=package,
            brand=brand,
            category_id=category_id,
            in_stock=in_stock,
            min_price=min_price,
            max_price=max_price,
            sort=sort,
            enrich=True,
            page=page,
        )
        return _json(
            [
                _part_payload(part)
                | {"search_relevance": client.search_relevance(part, query)}
                for part in parts
            ]
        )
    except SZLCSCError as exc:
        return _json(error_payload(exc) | {"source": "szlcsc.com"})


@mcp.tool(output_schema=None)
def szlcsc_parametric_search(
    query: Query,
    constraints: Constraints,
    max_results: ResultLimit = 10,
    fetch_pool: ResultLimit = 20,
    package: str | None = None,
    brand: str | None = None,
    in_stock: bool | None = True,
    max_price: Money | None = None,
) -> str:
    """参数化选型。constraints 示例：{"output_voltage":"=3.3V","accuracy":"<=0.2%","tempco":"<=20ppm/°C"}。"""
    try:
        rows = client.parametric_search(
            query,
            constraints,
            max_results=max_results,
            fetch_pool=fetch_pool,
            package=package,
            brand=brand,
            in_stock=in_stock,
            max_price=max_price,
        )
        return _json(
            [
                {
                    "part": _part_payload(row["part"]),
                    "constraint_match": row["match"],
                    "match_score": row["match_score"],
                }
                for row in rows
            ]
        )
    except SZLCSCError as exc:
        return _json(error_payload(exc) | {"source": "szlcsc.com"})


@mcp.tool(output_schema=None)
def szlcsc_get_part(identifier: Identifier, refresh: bool = False) -> str:
    """按 C 编号或明确 MPN 获取国内 SZLCSC 详情、参数、库存、价格、商品页和 Datasheet。"""
    try:
        return _json(_part_payload(client.get_part(identifier, refresh=refresh)))
    except SZLCSCError as exc:
        return _json(error_payload(exc) | {"source": "szlcsc.com"})


@mcp.tool(output_schema=None)
def szlcsc_compare_parts(identifiers: Identifiers) -> str:
    """并排比较多个明确器件，返回基础字段和同名参数差异；不会自动声明可直接替换。"""
    if not identifiers:
        return _json({"error": "identifiers is empty"})
    parts: list[Part] = []
    errors: list[dict[str, str]] = []
    for identifier in identifiers[:20]:
        try:
            parts.append(client.get_part(identifier))
        except SZLCSCError as exc:
            errors.append({"identifier": identifier, "error": str(exc)})
    keys = sorted({key for part in parts for key in part.parameters})
    rows = []
    for key in keys:
        values = {part.display_id: part.parameters.get(key) for part in parts}
        normalized = {str(value).strip().casefold() for value in values.values() if value not in (None, "")}
        rows.append({"parameter": key, "values": values, "different": len(normalized) > 1})
    return _json(
        {
            "parts": [_part_payload(part) for part in parts],
            "parameter_rows": rows,
            "errors": errors,
            "warning": "仅比较商城字段；pinout、绝对最大额定值及应用条件仍需核对制造商 datasheet。",
        }
    )


@mcp.tool(output_schema=None)
def szlcsc_find_alternatives(
    identifier: Identifier, max_results: ResultLimit = 10, in_stock_only: bool = True
) -> str:
    """获取 SZLCSC 替代料候选并排序。结果不是 drop-in 兼容声明。"""
    try:
        source = client.get_part(identifier)
        parts = client.alternatives(
            identifier, max_results=max_results, in_stock_only=in_stock_only
        )
        return _json(
            {
                "source": _part_payload(source),
                "candidates": [
                    (lambda ranking: {
                        "part": _part_payload(part),
                        # Keep ranking object for backward compatibility, while also
                        # exposing score/reasons at the candidate top level so simple
                        # agents do not accidentally report Score=None.
                        "score": ranking["score"],
                        "score_reasons": ranking["reasons"],
                        "ranking": ranking,
                    })(client.alternative_score(source, part))
                    for part in parts
                ],
            }
        )
    except SZLCSCError as exc:
        return _json(error_payload(exc) | {"source": "szlcsc.com"})


@mcp.tool(output_schema=None)
def szlcsc_download_part(
    identifier: str, output_root: str = "components", force: bool = True
) -> str:
    """下载并整理单个器件：part.json、README.md、datasheet.pdf（若可用）和商品图片。"""
    try:
        return _json(download_part(client, identifier, output_root, force=force))
    except (SZLCSCError, OSError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_download_parts(
    identifiers: list[str], output_root: str = "components", force: bool = True
) -> str:
    """批量下载并整理多个器件完整资料目录。"""
    return _json(download_parts(client, identifiers[:100], output_root, force=force))


@mcp.tool(output_schema=None)
def szlcsc_sync_datasheets(
    identifiers: list[str], output_dir: str = "datasheets", force: bool = False
) -> str:
    """批量同步 Datasheet 到一个目录并增量维护 manifest.json；适合 BOM/MPN 列表。"""
    return _json(sync_datasheets(client, identifiers[:200], output_dir, force=force))




@mcp.tool(output_schema=None)
def szlcsc_eda_assets(
    identifier: str,
    output_root: str = "eda-assets",
    include_svgs: bool = True,
    include_obj: bool = True,
    include_step: bool = True,
    force: bool = True,
) -> str:
    """下载 EasyEDA 原始 EDA 资产：CAD JSON、符号/封装 SVG、OBJ/STEP（若该器件提供）。"""
    try:
        code = _resolve_c_code(identifier)
        result = eda_client.download_bundle(
            code,
            output_root,
            include_svgs=include_svgs,
            include_obj=include_obj,
            include_step=include_step,
            force=force,
        )
        result["warning"] = (
            "EasyEDA 资产是第三方库数据；Symbol/Footprint/3D 均需按制造商 datasheet 复核后再用于生产。"
        )
        return _json(result)
    except (SZLCSCError, EasyEDAError, OSError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_altium_status() -> str:
    """检查可选第三方 altium-cruncher 是否已安装，以及 easyeda-import 子命令是否可用。"""
    return _json(altium_status())


@mcp.tool(output_schema=None)
def szlcsc_altium_setup() -> str:
    """返回 Altium 转换工具链诊断和安全安装/升级命令；本工具本身不会执行安装。"""
    status = altium_status()
    status["does_not_install_automatically"] = True
    return _json(status)


@mcp.tool(output_schema=None)
def szlcsc_altium_smoke_test(
    identifiers: list[str],
    output_root: str = "altium-smoke",
    preview: bool = True,
    require_3d: bool = False,
    test_merge: bool = True,
    timeout: int = 300,
) -> str:
    """实机 Altium 冒烟测试：最多 5 个 C 编号，检查 SchLib/PcbLib/report/可选3D，并可测试合并。"""
    try:
        codes = [_resolve_c_code(identifier) for identifier in identifiers[:5]]
        return _json(run_altium_smoke_test(
            codes, output_root, preview=preview, require_3d=require_3d, test_merge=test_merge, timeout=timeout
        ))
    except (SZLCSCError, AltiumImportError, OSError) as exc:
        return _json({"status": "error", **error_payload(exc)})


@mcp.tool(output_schema=None)
def szlcsc_altium_review(identifier: str, output_root: str = "altium") -> str:
    """读取已生成的 Altium EasyEDA 导入报告，汇总 pin/pad/3D placement 告警，不修改文件。"""
    try:
        code = _resolve_c_code(identifier)
        return _json(inspect_altium_output(code, output_root))
    except (SZLCSCError, AltiumImportError, OSError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_altium_import(
    identifier: str,
    output_root: str = "altium",
    preview: bool = True,
    symbol_only: bool = False,
    no_3d_model_download: bool = False,
    no_3d_model_placement: bool = False,
    pin_name_visibility: str = "source",
    pin_designator_visibility: str = "source",
    pin_text_orientation: str = "default",
    use_source_pin_electrical: bool = False,
    use_source_pin_ieee_symbols: bool = False,
    use_mcp_easyeda_cache: bool = True,
    timeout: int = 240,
) -> str:
    """调用本机 altium-cruncher，把指定 C 编号/MPN 的 EasyEDA 数据转换为 Altium SchLib/PcbLib/3D。"""
    try:
        if not symbol_only:
            require_altium_parser()
        code = _resolve_c_code(identifier)
        source_json = None
        source_warning = None
        if use_mcp_easyeda_cache:
            try:
                source_json = eda_client.write_altium_source(
                    code, f"{output_root}/_szlcsc_source/{code}.json"
                )
            except (EasyEDAError, OSError) as exc:
                source_warning = (
                    "MCP EasyEDA source cache failed; altium-cruncher will try its own live fetch: "
                    + str(exc)
                )
        result = run_altium_import(
                code,
                output_root,
                input_json=source_json,
                preview=preview,
                symbol_only=symbol_only,
                no_3d_model_download=no_3d_model_download,
                no_3d_model_placement=no_3d_model_placement,
                pin_name_visibility=pin_name_visibility,
                pin_designator_visibility=pin_designator_visibility,
                pin_text_orientation=pin_text_orientation,
                use_source_pin_electrical=use_source_pin_electrical,
                use_source_pin_ieee_symbols=use_source_pin_ieee_symbols,
                timeout=timeout,
            )
        if source_warning:
            result["source_warning"] = source_warning
        result["source_json"] = str(source_json) if source_json else None
        return _json(result)
    except (SZLCSCError, AltiumImportError, EasyEDAError, OSError) as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_altium_import_batch(
    identifiers: list[str],
    output_root: str = "altium",
    preview: bool = True,
    no_3d_model_download: bool = False,
    no_3d_model_placement: bool = False,
    use_mcp_easyeda_cache: bool = True,
    timeout: int = 240,
) -> str:
    """批量调用 altium-cruncher 生成 Altium 库；最多 50 个器件，逐个执行并分别报告失败。"""
    try:
        require_altium_parser()
    except AltiumImportError as exc:
        return _json({"error": exc})
    codes: list[str] = []
    resolve_errors: list[dict[str, str]] = []
    for identifier in identifiers[:50]:
        try:
            codes.append(_resolve_c_code(identifier))
        except SZLCSCError as exc:
            resolve_errors.append({"identifier": identifier, "error": str(exc)})
    source_jsons: dict[str, str] = {}
    source_errors: list[dict[str, str]] = []
    if use_mcp_easyeda_cache:
        for code in codes:
            try:
                path = eda_client.write_altium_source(
                    code, f"{output_root}/_szlcsc_source/{code}.json"
                )
                source_jsons[code] = str(path)
            except (EasyEDAError, OSError) as exc:
                source_errors.append({"lcsc_code": code, "error": str(exc)})
    try:
        result = run_altium_import_batch(
            codes,
            output_root,
            input_jsons=source_jsons,
            preview=preview,
            no_3d_model_download=no_3d_model_download,
            no_3d_model_placement=no_3d_model_placement,
            timeout=timeout,
        )
        result["resolve_errors"] = resolve_errors
        result["source_errors"] = source_errors
        result["source_jsons"] = source_jsons
        return _json(result)
    except (AltiumImportError, OSError) as exc:
        return _json({
            "error": exc,
            "resolve_errors": resolve_errors,
            "source_errors": source_errors,
        })



@mcp.tool(output_schema=None)
def szlcsc_library_status(
    library_root: str = "szlcsc-library",
    query: str | None = None,
    category: str | None = None,
    only_cad_owners: bool = False,
) -> str:
    """查看本地 SZLCSC/Altium 器件库索引、分类、重复 MPN/CAD 组和已登记器件。"""
    try:
        index = load_index(library_root)
        return _json({
            "library_root": str(Path(library_root).expanduser().resolve()),
            "summary": summarize_index(index),
            "parts": list_entries(
                index, query=query, category=category, only_cad_owners=only_cad_owners
            ),
        })
    except LibraryError as exc:
        return _json({"error": exc})


@mcp.tool(output_schema=None)
def szlcsc_library_add(
    identifiers: list[str],
    library_root: str = "szlcsc-library",
    altium_output_root: str = "altium",
    import_missing: bool = True,
    rebuild_cad: bool = False,
    dedupe_cad: bool = True,
    preview: bool = False,
    no_3d_model_download: bool = False,
    timeout: int = 240,
) -> str:
    """把器件登记进本地库；C编号保留采购元数据，MPN+封装用于 CAD 去重，可按需生成 Altium 库。"""
    results: list[dict[str, Any]] = []
    for identifier in identifiers[:100]:
        try:
            part = client.get_part(identifier)
            if not part.lcsc_code:
                raise SZLCSCError(f"No LCSC C-code resolved for {identifier!r}")
            index_before = load_index(library_root)
            existing = (index_before.get("parts") or {}).get(part.lcsc_code.upper())
            review = None
            imported = False
            skipped_shared = False
            if import_missing:
                # First register metadata so CAD dedupe can discover an existing owner.
                provisional = upsert_part(library_root, part, None, dedupe_cad=dedupe_cad)
                entry = provisional["entry"]
                owner = str(entry.get("cad_owner") or part.lcsc_code).upper()
                if owner != part.lcsc_code.upper():
                    owner_entry = (load_index(library_root).get("parts") or {}).get(owner) or {}
                    owner_review = archived_cad_review(owner_entry) or inspect_altium_output(owner, altium_output_root)
                    owner_has_cad = bool(
                        owner_review.get("quality", {}).get("has_schlib")
                        or owner_review.get("quality", {}).get("has_pcblib")
                    )
                    if owner_has_cad:
                        skipped_shared = True
                        review = owner_review
                existing_altium = (existing or {}).get("altium") or {}
                existing_has_cad = bool(existing_altium.get("has_schlib") or existing_altium.get("has_pcblib"))
                should_build = rebuild_cad or not existing or not existing_has_cad
                if not skipped_shared and should_build:
                    source_json = None
                    try:
                        source_json = eda_client.write_altium_source(
                            part.lcsc_code, f"{altium_output_root}/_szlcsc_source/{part.lcsc_code}.json"
                        )
                    except (EasyEDAError, OSError):
                        source_json = None
                    review = run_altium_import(
                        part.lcsc_code,
                        altium_output_root,
                        input_json=source_json,
                        preview=preview,
                        no_3d_model_download=no_3d_model_download,
                        timeout=timeout,
                    )
                    imported = bool(review.get("ok"))
            if review is None and part.lcsc_code:
                candidate = inspect_altium_output(part.lcsc_code, altium_output_root)
                if candidate.get("exists"):
                    review = candidate
            archive = archive_part_assets(
                client,
                part,
                library_root,
                review=review,
                force=rebuild_cad,
            )
            saved = upsert_part(
                library_root,
                part,
                review,
                archive=archive,
                dedupe_cad=dedupe_cad,
            )
            results.append({
                "identifier": identifier,
                "ok": True,
                "imported": imported,
                "cad_deduplicated": skipped_shared,
                "archive": archive,
                **saved,
            })
        except (SZLCSCError, EasyEDAError, AltiumImportError, LibraryError, OSError) as exc:
            results.append({"identifier": identifier, "ok": False, "error": str(exc)})
    try:
        index = load_index(library_root)
        summary = summarize_index(index)
    except LibraryError:
        summary = None
    return _json({
        "count": len(results),
        "success": sum(1 for row in results if row.get("ok")),
        "failed": sum(1 for row in results if not row.get("ok")),
        "results": results,
        "summary": summary,
    })


@mcp.tool(output_schema=None)
def szlcsc_library_sync(
    library_root: str = "szlcsc-library",
    identifiers: list[str] | None = None,
    dedupe_cad: bool = True,
) -> str:
    """刷新库内价格、库存、Datasheet 等采购元数据；不会因这些变化重建 CAD，MPN/封装变化会标记复核。"""
    try:
        index = load_index(library_root)
    except LibraryError as exc:
        return _json({"error": exc})
    targets = identifiers or sorted((index.get("parts") or {}).keys())
    results: list[dict[str, Any]] = []
    for identifier in targets[:500]:
        try:
            part = client.get_part(identifier)
            code = str(part.lcsc_code or "").upper()
            if not code:
                raise SZLCSCError(f"No LCSC C-code resolved for {identifier!r}")
            old = (load_index(library_root).get("parts") or {}).get(code) or {}
            old_meta = {k: old.get(k) for k in (
                "mpn", "package", "category", "small_batch_price_cny", "stock", "total_stock",
                "datasheet_url", "product_url", "min_order_qty", "order_multiple", "packaging"
            )}
            saved = upsert_part(library_root, part, None, dedupe_cad=dedupe_cad)
            new = saved["entry"]
            changes = {k: {"old": old_meta.get(k), "new": new.get(k)} for k in old_meta if old_meta.get(k) != new.get(k)}
            results.append({
                "identifier": identifier,
                "lcsc_code": code,
                "ok": True,
                "changes": changes,
                "cad_review_required": bool(new.get("cad_review_required")),
                "cad_rebuilt": False,
            })
        except (SZLCSCError, LibraryError, OSError) as exc:
            results.append({"identifier": identifier, "ok": False, "error": str(exc)})
    final = load_index(library_root)
    return _json({
        "count": len(results),
        "changed": sum(1 for row in results if row.get("changes")),
        "results": results,
        "summary": summarize_index(final),
        "cad_policy": "price/stock/datasheet changes never rebuild CAD automatically; MPN/package changes are flagged",
    })


@mcp.tool(output_schema=None)
def szlcsc_library_merge(
    library_root: str = "szlcsc-library",
    output_dir: str | None = None,
    category: str | None = None,
    conflicts: str = "rename",
    timeout: int = 240,
) -> str:
    """按库分类把已登记的独立 SchLib/PcbLib 合并成多器件 Altium 库；共享 CAD 只合并一次。"""
    try:
        index = load_index(library_root)
        staged = stage_for_merge(index, library_root, category=category)
        rows: list[dict[str, Any]] = []
        base_out = Path(output_dir).expanduser().resolve() if output_dir else (Path(library_root).expanduser().resolve() / "merged")
        for cat, group in sorted(staged["groups"].items()):
            cat_out = base_out / cat
            for kind_key in ("schlib_dir", "pcblib_dir"):
                source = group.get(kind_key)
                if not source:
                    continue
                mode = conflicts if kind_key == "schlib_dir" else "rename"
                merged = run_altium_merge(source, cat_out, conflicts=mode, timeout=timeout)
                merged["category"] = cat
                merged["owners"] = list(group.get("owners") or [])
                rows.append(merged)
        return _json({
            "ok": all(row.get("ok") for row in rows) if rows else False,
            "merge_results": rows,
            "missing": staged["missing"],
            "staging_root": staged["staging_root"],
            "output_dir": str(base_out),
            "note": "PcbLib merge uses rename conflict handling because current altium-cruncher only supports that mode for PcbLib.",
        })
    except (LibraryError, AltiumImportError, OSError) as exc:
        return _json({"error": exc})

@mcp.tool(output_schema=None)
def szlcsc_parse_barcode(raw: str, lookup: bool = True) -> str:
    """解析立创包装袋常见二维码文本（pc=C编号、pm=MPN、qty=数量）；lookup=true 时顺便查询器件详情。"""
    parsed = parse_szlcsc_barcode(raw)
    if lookup and parsed.get("recognized") and parsed.get("lcsc_code"):
        try:
            parsed["part"] = _part_payload(client.get_part(str(parsed["lcsc_code"])))
        except SZLCSCError as exc:
            parsed["lookup_error"] = str(exc)
    return _json(parsed)


@mcp.tool(output_schema=None)
def szlcsc_healthcheck(query: str = "STM32") -> str:
    """轻量检查 SZLCSC 搜索接口与本地缓存是否可用；不读取价格库存详情。"""
    try:
        parts = client.search(query, max_results=1, enrich=False)
        return _json({
            "ok": True,
            "query": query,
            "result_count": len(parts),
            "sample": _part_payload(parts[0]) if parts else None,
            "cache": client.cache.status(),
        })
    except SZLCSCError as exc:
        return _json({"query": query, "cache": client.cache.status(), **error_payload(exc)})


@mcp.tool(output_schema=None)
def szlcsc_cache_status(clear: bool = False, prune_days: int | None = None) -> str:
    """查看 SQLite 请求缓存；clear=true 清空；prune_days=N 清理 N 天以前缓存。"""
    result: dict[str, Any] = {}
    if clear:
        result["cleared"] = client.cache.clear()
    elif prune_days is not None:
        result["pruned"] = client.cache.prune(max(prune_days, 0) * 86400)
    result.update(client.cache.status())
    return _json(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Domestic SZLCSC sourcing MCP server")
    parser.add_argument("--transport", choices=("stdio", "http", "sse"), default="stdio")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        # This server exposes local filesystem/CAD tools. Remote hosting requires
        # a separate authenticated, restricted deployment; CLI binds loopback only.
        mcp.run(transport=args.transport, host=args.host, port=args.port, show_banner=False,
                host_origin_protection=True)


if __name__ == "__main__":
    main()
