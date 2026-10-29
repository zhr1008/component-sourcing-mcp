"""Black-box tests of the configured installed MCP EXE, using isolated files."""
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import time
import tomllib
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from zipfile import ZipFile

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import szlcsc_mcp

BASE = Path(__file__).resolve().parent
ROOT = BASE / "functional-output"
CONFIG = Path.home() / ".codex/config.toml"
CONFIG_SERVER = "components"
EXPECTED_VERSION = "0.8.0rc1"
WHEEL = None
REPORT = ROOT / "results.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pdf_files(directory):
    paths = list(Path(directory).rglob("*.pdf"))
    require(paths, "no PDF produced")
    for path in paths:
        require(path.stat().st_size > 1000 and path.read_bytes()[:5] == b"%PDF-", f"invalid PDF: {path}")
    return paths


def files_snapshot(directory, suffixes=None):
    return {str(p.relative_to(directory)): digest(p) for p in Path(directory).rglob("*")
            if p.is_file() and (suffixes is None or p.suffix.lower() in suffixes)}


def export_check(payload):
    require(payload.get("count", 0) > 0, "export contains no parts")
    files = payload.get("files") or payload.get("exported_files") or {}
    require(files, "no export files")
    for fmt, value in files.items():
        path = Path(value)
        require(path.is_file() and path.stat().st_size > 5, f"missing export {path}")
        if fmt == "csv": require(path.read_bytes().startswith(b"\xef\xbb\xbf"), "CSV missing UTF-8 BOM")


class Runner:
    def __init__(self, client, report):
        self.client, self.report = client, report

    def save(self):
        self.report["counts"] = {s: sum(r["status"] == s for r in self.report["cases"])
                                 for s in ("PASS", "PARTIAL", "BLOCKED", "FAIL", "NOT_RUN")}
        names = {r["tool"] for r in self.report["cases"] if r.get("invoked")}
        self.report["unique_tools_invoked"] = len(names)
        self.report["tools_not_invoked"] = sorted(set(self.report["tool_names"]) - names)
        tmp = REPORT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(REPORT)

    def note(self, label, tool, reason, status="NOT_RUN"):
        row = {"label": label, "tool": tool, "status": status, "invoked": False, "reason": reason}
        self.report["cases"].append(row)
        self.save()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    async def call(self, label, name, arguments, check=None, partial=None, timeout=240, expected_error=False):
        for key, value in arguments.items():
            if key in {"library_root", "catalog_root", "output_dir", "output_root", "altium_output_root"} and value is not None:
                require(Path(value).resolve().is_relative_to(ROOT.resolve()), f"write path outside test root: {key}")
            if key == "paths":
                require(all(Path(v).resolve().is_relative_to(ROOT.resolve()) for v in value), "scan outside test root")
        row = {"label": label, "tool": name, "arguments": arguments, "invoked": True}
        start = time.monotonic()
        payload = None
        try:
            result = await self.client.call_tool(name, arguments, raise_on_error=False, timeout=timeout)
            payload = result.structured_content
            if payload is None: payload = json.loads(result.content[0].text)
            row["response"] = payload
            if result.is_error and not expected_error:
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                code = error.get("code") if isinstance(error, dict) else None
                row.update(status="BLOCKED" if code in {"ACCESS_DENIED", "RATE_LIMITED"} else "FAIL", error=error or payload)
            else:
                if expected_error: require(result.is_error, "expected rejection did not happen")
                if check: check(payload)
                row["status"] = "PARTIAL" if partial and partial(payload) else "PASS"
        except Exception as exc:  # noqa: BLE001 - keep subsequent black-box cases runnable
            row.update(status="FAIL", error={"type": type(exc).__name__, "message": str(exc)})
        row["elapsed_seconds"] = round(time.monotonic() - start, 2)
        self.report["cases"].append(row)
        self.save()
        brief = {k: v for k, v in row.items() if k not in {"response", "arguments"}}
        if isinstance(payload, dict):
            brief["observed"] = {k: payload[k] for k in ("count", "processed", "successful", "downloaded", "cached", "errors", "ready", "status", "success", "failed", "applied", "applied_action_count") if k in payload}
        print(json.dumps(brief, ensure_ascii=False, default=str), flush=True)
        return payload


async def network(r):
    await r.call("国内搜索健康检查", "szlcsc_healthcheck", {"query": "C5291487"},
                 lambda p: require(p["ok"] and p["result_count"] > 0, "healthcheck has no result"))
    await r.call("型号/库存筛选搜索", "szlcsc_search", {"query": "TPR3333", "max_results": 3, "in_stock": True},
                 lambda p: require(p["count"] > 0 and all(x["in_stock"] for x in p["results"]), "search did not return stocked candidates"))
    for identifier in ("C5291487", "C2040"):
        await r.call(f"精确查料 {identifier}", "szlcsc_get_part", {"identifier": identifier, "refresh": True},
                     lambda p, code=identifier: require(p["lcsc_code"] == code and p["mpn"], "wrong exact identity"))
    await r.call("精确 MPN 查料", "szlcsc_get_part", {"identifier": "TPR3333-S3TR"},
                 lambda p: require(p["mpn"].upper() == "TPR3333-S3TR", "MPN is not exact"))
    await r.call("库存与数量报价", "szlcsc_get_stock", {"identifier": "C5291487", "refresh": True},
                 lambda p: require(p["inventory_pool"] == "SZLCSC_domestic_retail", "wrong inventory pool"),
                 lambda p: p["in_stock"] is None)
    await r.call("10 个数量报价", "szlcsc_get_pricing", {"identifier": "C2040", "quantity": 10},
                 lambda p: require(p["order_quantity"] == 10 and p["unit_price_cny"] is not None, "quantity quote missing"))
    await r.call("电压/精度参数化筛选", "szlcsc_parametric_search",
                 {"query": "TPR3333", "constraints": {"output_voltage": "=3.3V", "accuracy": "<=0.2%"}, "max_results": 2, "fetch_pool": 3},
                 lambda p: require(p["count"] > 0, "no parametric candidates"),
                 lambda p: not any(x["constraint_match"]["all_matched"] for x in p["results"]))
    await r.call("两个器件参数对比", "szlcsc_compare_parts", {"identifiers": ["C5291487", "C2040"]},
                 lambda p: require(len(p["parts"]) == 2 and not p["errors"], "comparison lost an identity"))
    await r.call("替代料及评分", "szlcsc_find_alternatives", {"identifier": "C5291487", "max_results": 3, "in_stock_only": False},
                 lambda p: require(all(x.get("score") is not None and x.get("score_reasons") is not None for x in p["candidates"]), "alternative score is missing"),
                 lambda p: not p["candidates"])
    await r.call("包装二维码与在线查料", "szlcsc_parse_barcode", {"raw": "{pc:C5291487,pm:TPR3333-S3TR,qty:10,mc:,hp:null}", "lookup": True},
                 lambda p: require(p["recognized"] and p["quantity"] == 10 and p["part"]["lcsc_code"] == "C5291487", "barcode lookup failed"))
    for kind in ("basic", "preferred", "extended", "no_fee"):
        await r.call(f"JLCPCB {kind} 库筛选", "szlcsc_jlcpcb_search", {"query": "100nF", "library_type": kind, "page_size": 2, "min_stock": 10, "refresh": True},
                     lambda p, k=kind: require(all(x["library_type"] in ({"basic", "preferred"} if k == "no_fee" else {k}) for x in p["results"]), "library filter leaked"),
                     lambda p: p["count"] == 0)
    await r.call("JLCPCB 分类访问状态", "szlcsc_jlcpcb_categories", {"refresh": True},
                 lambda p: require(p["count"] > 0, "no categories"))
    await r.call("转换器状态", "szlcsc_altium_status", {}, lambda p: require("ready" in p and "installed" in p, "invalid toolchain status"))
    await r.call("转换器只读安装诊断", "szlcsc_altium_setup", {}, lambda p: require(p["does_not_install_automatically"], "setup attempted installation"))
    guide = await r.client.read_resource("szlcsc://sourcing-guide")
    prompt = await r.client.get_prompt("select_domestic_component", {"requirements": "3.3V reference"})
    r.report["resource_and_prompt"] = {"resource_read": bool(guide), "prompt_get": bool(prompt)}
    r.save()


async def assets(r):
    single = ROOT / "downloads"
    def downloaded(p):
        from PIL import Image
        pdf_files(p["directory"])
        images = [f for f in Path(p["directory"]).rglob("*-product.*") if f.is_file()]
        require(images and not p.get("errors"), "product image download failed")
        for path in images:
            with Image.open(path) as img: img.verify()
    await r.call("单器件资料及有效商品图下载", "szlcsc_download_part", {"identifier": "C5291487", "output_root": str(single), "force": True}, downloaded)
    await r.call("批量下载与重复编号去重", "szlcsc_download_parts", {"identifiers": ["C5291487", "C2040", "C5291487"], "output_root": str(ROOT / "batch-downloads"), "force": True},
                 lambda p: require(p["processed"] == p["successful"] == 2 and len(pdf_files(ROOT / "batch-downloads")) == 2, "batch PDF/dedup failed"),
                 lambda p: any(x.get("errors") for x in p["results"]))
    sheets = ROOT / "datasheets"
    await r.call("批量 Datasheet 与 manifest", "szlcsc_sync_datasheets", {"identifiers": ["C5291487", "C2040"], "output_dir": str(sheets), "force": False},
                 lambda p: require(p["complete"] and p["manifest_items"] == 2 and len(pdf_files(sheets)) == 2, "datasheet sync incomplete"))
    before = files_snapshot(sheets, {".pdf"})
    await r.call("Datasheet 增量复用", "szlcsc_sync_datasheets", {"identifiers": ["C5291487", "C2040"], "output_dir": str(sheets), "force": False},
                 lambda p: require(p["cached"] == 2 and before == files_snapshot(sheets, {".pdf"}), "incremental sync changed existing PDFs"))
    def eda_check(p):
        target = ROOT / "eda/C5291487"
        require((target / "easyeda_raw.json").stat().st_size > 100, "CAD JSON absent")
        svgs = list(target.rglob("*.svg"))
        require(len(svgs) >= 2 and all("<svg" in f.read_text(encoding="utf-8") for f in svgs), "SVG assets incomplete")
        steps = list(target.rglob("*.step")); objs = list(target.rglob("*.obj"))
        require(steps and objs, "STEP or OBJ missing")
        require(all(b"iso-10303-21" in f.read_bytes()[:4096].lower() for f in steps), "STEP is not a CAD file")
    await r.call("EasyEDA CAD/SVG/OBJ/STEP 下载", "szlcsc_eda_assets", {"identifier": "C5291487", "output_root": str(ROOT / "eda"), "include_svgs": True, "include_obj": True, "include_step": True}, eda_check,
                 lambda p: bool(p.get("errors")))


async def altium(r):
    status = await r.call("Altium 转换前工具链诊断", "szlcsc_altium_status", {})
    if not status or not status.get("ready"):
        for tool in ("szlcsc_altium_import", "szlcsc_altium_import_batch", "szlcsc_altium_review", "szlcsc_altium_smoke_test"):
            r.note("工具链未就绪", tool, status or "no toolchain status", "BLOCKED")
        return
    def imported(p):
        require(p.get("ok"), "converter returned nonzero exit status")
        for key in ("schlibs", "pcblibs"):
            paths = p.get(key) or []
            require(paths, f"no {key}")
            for value in paths:
                path = Path(value)
                require(path.is_file() and path.stat().st_size > 512, f"empty library: {path}")
                require(path.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1"), f"not a binary Altium compound file: {path}")
        require((p.get("quality", {}).get("footprint") or {}).get("pad_count_match") is not False, "pad count mismatch")
        require((p.get("quality", {}).get("symbol_footprint_links") or {}).get("verified_from_saved_libraries"), "saved libraries have no verified footprint link")
    await r.call("真实 EasyEDA→Altium 单器件转换及 3D", "szlcsc_altium_import",
                 {"identifier": "C5291487", "output_root": str(ROOT / "altium-single"), "preview": True, "timeout": 120}, imported,
                 lambda p: not p.get("step_files"), timeout=240)
    def batch_check(p):
        require(p.get("count") == 2 and len(p.get("results") or []) == 2, "batch import did not process two parts")
        for row in p["results"]: imported(row)
        require(not p.get("resolve_errors") and not p.get("source_errors"), "batch source resolution errors")
    await r.call("真实批量 Altium 转换", "szlcsc_altium_import_batch",
                 {"identifiers": ["C5291487", "C2040"], "output_root": str(ROOT / "altium-batch"), "preview": True, "timeout": 120}, batch_check, timeout=360)
    await r.call("生成报告复核读取", "szlcsc_altium_review",
                 {"identifier": "C5291487", "output_root": str(ROOT / "altium-single")},
                 lambda p: require(p.get("exists") and p.get("quality", {}).get("has_schlib") and p.get("quality", {}).get("has_pcblib"), "review cannot find generated binary libraries"))
    await r.call("真实双器件 3D 与合并 smoke", "szlcsc_altium_smoke_test",
                 {"identifiers": ["C5291487", "C2040"], "output_root": str(ROOT / "altium-smoke"), "preview": True, "require_3d": True, "test_merge": True, "timeout": 120},
                 lambda p: require(p.get("ok") and all(x.get("automated_pass") for x in p["results"]) and p["merge"]["ok"], "Altium smoke checks failed"), timeout=420)


async def archive(r):
    root = ROOT / "managed-library"
    common = {"library_root": str(root)}
    await r.call("登记真实双器件及 CAD/PDF 双视图归档", "szlcsc_library_add",
                 {**common, "identifiers": ["C5291487", "C2040"], "altium_output_root": str(ROOT / "altium-batch"), "import_missing": False},
                 lambda p: require(p["success"] == 2 and p["failed"] == 0 and all(x["archive"]["assets"]["schlib"]["classified"] and x["archive"]["assets"]["pcblib"]["classified"] for x in p["results"]), "CAD-backed archive incomplete"),
                 lambda p: any(x["archive"].get("errors") for x in p["results"]))
    await r.call("归档库非空状态", "szlcsc_library_status", common,
                 lambda p: require(len(p["parts"]) == 2, "archive lost parts"))
    await r.call("离线资料/CAD 条件检索", "szlcsc_library_search", {**common, "query": "TPR3333", "has_datasheet": True, "has_schlib": True, "has_pcblib": True},
                 lambda p: require(p["offline"] and p["count"] == 1 and p["results"][0]["lcsc_code"] == "C5291487", "archive filter returned wrong result"))
    index = root / "library-index.json"
    before_index = digest(index)
    await r.call("采购信息同步预览无写入", "szlcsc_library_sync_preview", common,
                 lambda p: require(digest(index) == before_index and p.get("count", p.get("part_count", 0)) == 2, "preview changed index or skipped parts"))
    cad_before = files_snapshot(root, {".schlib", ".pcblib", ".step"})
    await r.call("采购信息同步保持 CAD", "szlcsc_library_sync", common,
                 lambda p: require(p["count"] == 2 and all(x["ok"] and not x["cad_rebuilt"] for x in p["results"]) and cad_before == files_snapshot(root, {".schlib", ".pcblib", ".step"}), "sync rebuilt CAD or failed"))
    await r.call("双器件 JSON/Excel CSV 导出", "szlcsc_library_export", {**common, "output_dir": str(ROOT / "archive-export")}, export_check)
    await r.call("归档文件及哈希审计", "szlcsc_library_audit", {**common, "verify_hashes": True},
                 lambda p: require(p["checked_parts"] == 2 and p["issue_count"] == 0, "nonempty archive audit failed"))
    stored = json.loads(index.read_text(encoding="utf-8"))
    asset = stored["parts"]["C5291487"]["archive"]["assets"]["datasheet"]
    canonical, mirror = Path(asset["classified"][0]), Path(asset["flat"][0])
    expected = digest(canonical)
    require(mirror.resolve().is_relative_to(ROOT.resolve()), "mirror fixture escaped")
    mirror.unlink()
    before_index = digest(index)
    await r.call("缺失镜像修复预览无写入", "szlcsc_library_repair", {**common, "dry_run": True},
                 lambda p: require(p["action_count"] > 0 and not mirror.exists() and digest(index) == before_index, "repair dry-run wrote or found no action"))
    await r.call("实际恢复丢失镜像并校验哈希", "szlcsc_library_repair", {**common, "dry_run": False},
                 lambda p: require(p["ok"] and p["action_count"] > 0 and digest(mirror) == digest(canonical) == expected, "repair did not restore identical PDF"))
    orphan = root / "flat/unindexed-test.pdf"
    shutil.copy2(canonical, orphan)
    await r.call("发现真实孤立 PDF 且不删除", "szlcsc_library_orphans", {**common, "include_hashes": True},
                 lambda p: require(any(Path(x["path"]) == orphan and x["sha256"] == expected for x in p["orphans"]) and orphan.exists(), "orphan scan missed unindexed file"))
    # A legacy index in a separate root references copies of real, freshly generated assets.
    legacy = ROOT / "legacy-library"
    legacy.mkdir(exist_ok=True)
    for entry in stored["parts"].values():
        for kind, row in entry["archive"]["assets"].items():
            sources = row.get("classified") or []
            copies = []
            for n, src in enumerate(sources):
                dst = legacy / "legacy-assets" / entry["lcsc_code"] / f"{kind}-{n}{Path(src).suffix}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst); copies.append(str(dst))
            row["classified"], row["flat"] = copies, []
        entry["archive"]["layout_version"] = 1
        entry["altium"]["case_dir"] = ""
    legacy_index = legacy / "library-index.json"
    legacy_index.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    legacy_hashes = files_snapshot(legacy / "legacy-assets")
    legacy_before = digest(legacy_index)
    margs = {"library_root": str(legacy)}
    await r.call("旧布局非空迁移计划", "szlcsc_library_migration_plan", margs,
                 lambda p: require(p["safe_to_apply"] and p["needs_migration_count"] == 2 and p["copy_count"] > 0 and digest(legacy_index) == legacy_before, "migration plan empty or wrote index"))
    await r.call("旧布局迁移 dry-run", "szlcsc_library_migrate", {**margs, "dry_run": True},
                 lambda p: require(not p["applied"] and digest(legacy_index) == legacy_before, "migration dry-run wrote"))
    await r.call("实际迁移/备份/保留原文件", "szlcsc_library_migrate", {**margs, "dry_run": False, "backup_index": True},
                 lambda p: require(p["ok"] and p["applied"] and p["migrated_parts"] == 2 and p["copied_file_count"] > 0 and digest(p["backup_path"]) == legacy_before and legacy_hashes == files_snapshot(legacy / "legacy-assets"), "migration/backup integrity failed"))
    await r.call("迁移重复执行无变更", "szlcsc_library_migration_plan", margs,
                 lambda p: require(p["needs_migration_count"] == 0 and p["copy_count"] == 0, "migration not idempotent"))
    await r.call("归档 SchLib/PcbLib 分类合并", "szlcsc_library_merge", {**common, "output_dir": str(ROOT / "managed-merged"), "timeout": 120},
                 lambda p: require(p["ok"] and len(p["merge_results"]) >= 2 and list((ROOT / "managed-merged").rglob("*.SchLib")) and list((ROOT / "managed-merged").rglob("*.PcbLib")), "managed library merge produced no binaries"), timeout=360)


async def ad(r):
    lib = ROOT / "ad-fixtures"
    lib.mkdir(exist_ok=True)
    originals = {}
    for path in (ROOT / "altium-batch").rglob("*"):
        if path.suffix.lower() in {".schlib", ".pcblib"}:
            dest = lib / f"{path.parent.name}-{path.name}"
            shutil.copy2(path, dest); originals[str(path)] = digest(path)
    require(len(list(lib.glob("*.SchLib"))) >= 2, "no real SchLib fixture")
    root = ROOT / "ad-catalog"
    common = {"catalog_root": str(root)}
    await r.call("读取实际双器件 SchLib/PcbLib", "szlcsc_ad_library_scan", {**common, "paths": [str(lib)], "hash_files": True},
                 lambda p: require(p["symbol_count"] >= 2 and p["footprint_count"] >= 2 and not p.get("parse_errors"), "AD parser skipped real library"))
    await r.call("非空 AD 索引/解析依赖状态", "szlcsc_ad_library_status", common,
                 lambda p: require(p["parser_ready"] and p["symbol_count"] >= 2 and p["footprint_count"] >= 2, "AD index empty"))
    await r.call("离线 Symbol 精确搜索", "szlcsc_ad_library_search", {**common, "query": "TPR3333"},
                 lambda p: require(p["offline"] and p["count"] == 1 and len(p["results"][0]["pins"]) == 3, "AD search/pins failed"))
    await r.call("AD 双器件在线身份补全", "szlcsc_ad_library_enrich", {**common, "force": True},
                 lambda p: require(p["count"] >= 2 and p.get("resolved", 0) >= 2, "AD identities not resolved"))
    await r.call("AD 原厂资料实际下载", "szlcsc_ad_library_datasheets", common,
                 lambda p: require(p["ok"] and p["ready"] == 2 and len(pdf_files(root)) >= 2, "AD datasheets incomplete"))
    await r.call("生成非空 Datasheet 核验队列", "szlcsc_ad_library_verification_queue", common,
                 lambda p: require(p["count"] >= 2 and all(x["symbol_pins"] and x["footprint_links"] for x in p["queue"]), "verification queue omitted actual pins/footprints"))
    catalog = json.loads((root / "altium-catalog.json").read_text(encoding="utf-8"))
    symbol = next(x for x in catalog["symbols"].values() if "TPR3333" in x["name"])
    before = files_snapshot(lib)
    await r.call("拒绝非 Datasheet 权威来源", "szlcsc_ad_library_verify", {**common, "asset_id": symbol["asset_id"], "datasheet_evidence": {"source": "easyeda", "pages": [4]}}, expected_error=True)
    await r.call("AD 属性补全非空计划", "szlcsc_ad_library_property_plan", common,
                 lambda p: require(p.get("add_count", 0) + p.get("update_count", 0) > 0, "property plan has no actions"))
    await r.call("AD 属性写入 dry-run 保持库文件", "szlcsc_ad_library_property_apply", {**common, "dry_run": True},
                 lambda p: require(p["dry_run"] and not p["applied"] and before == files_snapshot(lib), "dry run altered SchLib"))
    await r.call("AD 属性实际写入及自动备份", "szlcsc_ad_library_property_apply", {**common, "dry_run": False, "backup": True},
                 lambda p: require(p["applied"] and p["applied_action_count"] > 0 and not p["failures"] and p["backup_paths"] and all(Path(x).is_file() for x in p["backup_paths"]), "actual property write/backup failed"))
    await r.call("属性写回后重扫和元数据增量刷新", "szlcsc_ad_library_refresh", {**common, "max_age_hours": 0, "hash_files": True},
                 lambda p: require(not p.get("error"), "AD refresh failed"))
    await r.call("完整 AD 索引 JSON/CSV 导出", "szlcsc_ad_library_export", {**common, "output_dir": str(ROOT / "ad-export")}, export_check)
    require(all(digest(path) == value for path, value in originals.items()), "original conversion artifacts modified")
    r.report["ad_original_conversion_files_unchanged"] = True
    r.save()


async def final_checks(r):
    for kind in ("no_fee", "extended"):
        await r.call(f"冷却期后 JLCPCB {kind} 实际请求", "szlcsc_jlcpcb_search", {"query": "100nF", "library_type": kind, "page_size": 2, "refresh": True},
                     lambda p: require(p["count"] > 0, "no candidates"), lambda p: not p.get("complete", True))
    await r.call("替代料第二样本 10kΩ 0603", "szlcsc_find_alternatives", {"identifier": "C25804", "max_results": 3, "in_stock_only": False},
                 lambda p: require(all(x.get("score") is not None for x in p["candidates"]), "missing scores"), lambda p: not p["candidates"])
    await r.call("本地测试缓存状态", "szlcsc_cache_status", {})
    await r.call("仅测试缓存过期清理", "szlcsc_cache_status", {"prune_days": 30})
    await r.call("仅测试缓存清空", "szlcsc_cache_status", {"clear": True})


async def integrity(r):
    root = ROOT / "ad-catalog"
    common = {"catalog_root": str(root)}
    path = root / "altium-catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    symbol = next(x for x in catalog["symbols"].values() if x["name"] == "TPR3333-S3TR")
    aid = symbol["asset_id"]
    evidence = {
        "source": "datasheet", "manufacturer": "3PEAK", "mpn": "TPR3333-S3TR", "package": "SOT23G-3",
        "pages": [3, 4, 12, 13],
        "pins": [{"number": "1", "name": "IN"}, {"number": "2", "name": "OUT"}, {"number": "3", "name": "GND"}],
        "terminal_numbers": ["1", "2", "3"],
        "mechanical_checks": [],
        "review_note": "Source PDF pages 4 and 12 were rendered and visually read. Full land-pattern and 3D checks are not complete; do not mark manufacturing verified.",
    }
    await r.call("拒绝缺失 Datasheet 页码", "szlcsc_ad_library_verify", {**common, "asset_id": aid, "datasheet_evidence": {"source": "datasheet", "pages": []}}, expected_error=True)
    await r.call("原厂引脚与已关联焊盘核对且机械证据不足不置 verified", "szlcsc_ad_library_verify", {**common, "asset_id": aid, "datasheet_evidence": evidence},
                 lambda p: require(p["pin_numbers_match"] and not p["pin_name_differences"] and p["status"] == "mechanical_review_required" and p["footprint_checks"] and all(x["pad_numbers_match"] for x in p["footprint_checks"]), "linked footprint/pin verification failed or falsely marked verified"))
    def geometry_check(p):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        fp = next(x for x in parsed["footprints"].values() if x["name"] == "TPR3333-S3TR")
        require(len(fp["pads"]) == 3, "missing actual pads")
        require(all(0 < row["height_mils"] < 500 for row in fp["pads"]),
                "SOT23 pad height was not converted from Altium internal units: " + str([row["height_mils"] for row in fp["pads"]]))
    await r.call("SOT23 焊盘高度物理范围检查", "szlcsc_ad_library_scan", {**common, "paths": [str(ROOT / "ad-fixtures")], "hash_files": True}, geometry_check)
    await r.call("属性写入后 C编号字段重新读取", "szlcsc_ad_library_search", {**common, "query": "TPR3333"},
                 lambda p: require(p["count"] == 1 and p["results"][0]["parameters"]["LCSC Part #"] == "C5291487", "written properties not persisted"))
    # Compare graphical primitives from the saved library against its pre-write backup.
    from altium_monkey import AltiumSchLib
    write_case = next(x for x in r.report["cases"] if x["tool"] == "szlcsc_ad_library_property_apply" and x.get("response", {}).get("applied"))
    primitive_rows = []
    for backup in write_case["response"]["backup_paths"]:
        saved = ROOT / "ad-fixtures" / Path(backup).name.split("-", 1)[1]
        # Backup filenames contain a root hash prefix followed by the original basename.
        before_lib, after_lib = AltiumSchLib(backup), AltiumSchLib(saved)
        for name in AltiumSchLib.get_symbol_names(backup):
            def graphics(library, name=name):
                return [x for x in library.symbol_to_ir(name).to_dict()["records"] if x["kind"] in {"pin", "rectangle", "ellipse", "line", "polyline", "polygon", "arc", "bezier", "label"}]
            require(graphics(before_lib) == graphics(after_lib), "property write changed primitive geometry: " + name)
            primitive_rows.append({"symbol": name, "backup": backup, "saved": str(saved), "unchanged": True})
    r.report["property_write_geometry_comparison"] = primitive_rows
    # Manual GUI/PDF observations are recorded outside this automated harness.
    # A new run must collect its own GUI evidence; never infer manual PASS here.
    # Existing return metadata contradicts actual disk writes; keep this failure visible.
    await r.call("属性写入及重复无动作调用的状态一致", "szlcsc_ad_library_property_apply", {**common, "dry_run": False},
                 lambda p: require(write_case["response"]["library_write_performed"] and not p["library_write_performed"] and not p["applied"] and p["applied_action_count"] == 0, "property write report differs from actual saved changes"))
    await r.call("测试缓存清空后仍为空", "szlcsc_cache_status", {},
                 lambda p: require(p["entries"] == p["memory_entries"] == 0 and Path(p["path"]) == ROOT / "cache.sqlite3", "cache clear did not persist or wrong cache"))
    await r.call("重新生成真实查询缓存", "szlcsc_get_part", {"identifier": "C5291487"}, lambda p: require(p["lcsc_code"] == "C5291487", "query after errors unusable"))
    with sqlite3.connect(ROOT / "cache.sqlite3") as db:
        count = db.execute("SELECT COUNT(*) FROM http_cache").fetchone()[0]
        require(count > 0, "cache fixture empty")
        db.execute("UPDATE http_cache SET fetched_at = ?", (int(time.time()) - 40 * 86400,))
    await r.call("实际删除过期测试缓存", "szlcsc_cache_status", {"prune_days": 30},
                 lambda p: require(p["pruned"] == count and p["entries"] == p["memory_entries"] == 0, "prune failed on aged real cached requests"))
    r.save()


async def category_recheck(r):
    await r.call("分类接口独立进程实际请求复核", "szlcsc_jlcpcb_categories", {"refresh": True},
                 lambda p: require(p["count"] > 0, "category list empty"))


async def main(phase):
    ROOT.mkdir(parents=True, exist_ok=True)
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8-sig"))["mcp_servers"][CONFIG_SERVER]
    require(Path(cfg["command"]).parent == Path(__import__('sys').executable).parent, "test interpreter and configured server are not the same installed environment")
    require(metadata.version("component-sourcing-mcp") == EXPECTED_VERSION, "unexpected installed version")
    with ZipFile(WHEEL) as wheel:
        site = Path(szlcsc_mcp.__file__).parent.parent
        pyfiles = [n for n in wheel.namelist() if n.startswith("szlcsc_mcp/") and n.endswith(".py")]
        for name in pyfiles: require((site / name).read_bytes().replace(b"\r\n", b"\n") == wheel.read(name).replace(b"\r\n", b"\n"), f"installed code mismatch: {name}")
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {
        "started_at_utc": datetime.now(UTC).isoformat(), "version": EXPECTED_VERSION,
        "wheel_sha256": digest(WHEEL),
        "configured_command": cfg["command"], "source_matches_release_python_files": len(pyfiles),
        "test_root": str(ROOT), "cases": [], "manual_altium_gui": "NOT_RUN", "datasheet_engineering_verification": "NOT_RUN",
    }
    env = {**os.environ, **cfg.get("env", {}), "SZLCSC_CACHE_PATH": str(ROOT / "cache.sqlite3"), "PYTHONUTF8": "1"}
    report["converter_profile"] = "default configured environment; no temporary converter override"
    transport = StdioTransport(cfg["command"], cfg.get("args", []), env=env, cwd=str(ROOT), log_file=ROOT / f"server-{phase}.log")
    async with Client(transport, timeout=600) as client:
        tools = await client.list_tools()
        require(len(tools) == 49, "wrong tool count")
        report["tool_names"] = [t.name for t in tools]
        report["protocol"] = client.protocol_version
        (ROOT / "tool-schemas.json").write_text(json.dumps([t.model_dump(mode="json", by_alias=True) for t in tools], ensure_ascii=False, indent=2), encoding="utf-8")
        runner = Runner(client, report)
        await {"network": network, "assets": assets, "altium": altium, "archive": archive, "ad": ad, "final": final_checks, "integrity": integrity, "categories": category_recheck}[phase](runner)
        runner.save()
        print(json.dumps({"phase": phase, "counts": report["counts"], "unique_tools_invoked": report["unique_tools_invoked"], "report": str(REPORT)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("network", "assets", "altium", "archive", "ad", "final", "integrity", "categories"))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--codex-config", type=Path, default=CONFIG)
    parser.add_argument("--server-name", default=CONFIG_SERVER)
    parser.add_argument("--expected-version", default=EXPECTED_VERSION)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    ROOT = args.output_root.resolve()
    require(ROOT != BASE and ROOT != BASE.parent and ROOT != Path.home(), "use a dedicated test output directory")
    CONFIG, EXPECTED_VERSION, WHEEL = args.codex_config, args.expected_version, args.wheel
    CONFIG_SERVER = args.server_name
    REPORT = ROOT / "results.json"
    asyncio.run(main(args.phase))
