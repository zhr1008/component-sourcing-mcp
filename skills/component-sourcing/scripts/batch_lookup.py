"""Deduplicate exact domestic part lookups over one configured MCP session."""

from argparse import ArgumentParser
import asyncio
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys


TOOL = "szlcsc_get_part"
STOP_CODES = {"RATE_LIMITED", "ACCESS_DENIED"}
C_CODE = re.compile(r"c[0-9]+", re.IGNORECASE)
PROCUREMENT_FIELDS = (
    "stock", "total_stock", "stock_status", "in_stock", "min_order_qty",
    "order_multiple", "small_batch_price_cny", "full_pack_qty",
)


def plan_queries(path, column=None, skip_blank=False):
    if path.suffix.lower() == ".csv":
        if column is None:
            raise ValueError("CSV 输入必须用 --column 指定料号列")
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file, strict=True)
            header = next(reader, [])
            if header.count(column) != 1:
                raise ValueError("CSV 中指定列必须恰好出现一次")
            index = header.index(column)
            values = []
            for row in reader:
                if not row and skip_blank:
                    values.append("")
                    continue
                if len(row) != len(header):
                    raise ValueError(f"CSV 第 {len(values) + 2} 条记录的列数与表头不一致")
                values.append(row[index])
        start = 2
    elif path.suffix.lower() == ".json" and column is None:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(values, list):
            raise ValueError("JSON 输入必须是料号字符串数组")
        start = 1
    else:
        raise ValueError("使用 JSON 数组，或用 --column 指定 CSV 的料号列")

    unique = {}
    blank_rows = []
    for row, value in enumerate(values, start=start):
        if not isinstance(value, str):
            raise ValueError(f"第 {row} 条必须是料号文本，前导零依原文保留")
        identifier = value.strip()
        if not identifier and skip_blank:
            blank_rows.append(row)
            continue
        if not identifier or len(identifier) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in identifier):
            raise ValueError(f"第 {row} 条缺少可用的完整料号")
        compact_code = re.sub(r"\s+", "", identifier)
        if C_CODE.fullmatch(compact_code):
            identifier = compact_code.upper()
        item = unique.setdefault(identifier.casefold(), {"identifier": identifier, "input_rows": []})
        item["input_rows"].append(row)
    if not unique and not blank_rows:
        raise ValueError("输入至少需要一条完整 C 号或完整 MPN")
    return len(values), list(unique.values()), blank_rows


def component_config():
    try:
        process = subprocess.run(
            ["codex", "mcp", "get", "components", "--json"],
            capture_output=True, encoding="utf-8", check=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(process.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError("无法读取本机 Codex 的 components 配置") from exc

    transport = data.get("transport", {})
    if not data.get("enabled") or transport.get("type") != "stdio":
        raise ValueError("批查需要已启用且使用 stdio 的 components")
    enabled = data.get("enabled_tools")
    if TOOL in (data.get("disabled_tools") or []) or (enabled is not None and TOOL not in enabled):
        raise ValueError("当前 components 配置未启用精确详情工具")
    return data


def tool_body(response):
    if hasattr(response, "structured_content"):
        payload = response.structured_content
        is_error = response.is_error
    else:
        payload = response.structuredContent
        is_error = response.isError
    if payload is None:
        content = response.content
        if len(content) == 1 and content[0].type == "text":
            try:
                payload = json.loads(content[0].text)
            except ValueError:
                payload = None
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if not isinstance(payload, dict):
        return {"error": {"code": "MCP_TOOL_ERROR", "message": "工具未返回结构化器件详情"}}
    if is_error and "error" not in payload:
        return {"error": {"code": "MCP_TOOL_ERROR", "message": "精确详情调用失败"}}
    return payload


def part_result(body, identifier):
    if "error" in body or body.get("ok") is False:
        error = body.get("error")
        if not isinstance(error, dict):
            error = {"code": "MCP_TOOL_ERROR", "message": "工具未返回结构化错误"}
        return {"status": "error", "error": error}
    field = "lcsc_code" if C_CODE.fullmatch(identifier) else "mpn"
    identity = body.get(field)
    if not isinstance(identity, str) or identity.casefold() != identifier.casefold():
        return {
            "status": "error",
            "error": {"code": "IDENTITY_MISMATCH", "message": "工具返回的身份与完整查询料号不一致"},
        }
    return {"status": "found", "part": body}


def access_issue(part):
    warnings = part.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, str):
                code = warning.rsplit(": ", 1)[-1]
                if code in STOP_CODES:
                    return code
    return None


def reusable_parts(path):
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("--reuse 需要此前保存的完整批查报告 JSON")
    parts = {}
    for row in report["results"]:
        if not isinstance(row, dict) or row.get("status") != "found":
            continue
        identifier, part = row.get("identifier"), row.get("part")
        if not isinstance(identifier, str) or not isinstance(part, dict):
            continue
        procurement = part.get("procurement")
        warnings = part.get("warnings")
        detail_failed = isinstance(warnings, list) and any(
            isinstance(warning, str) and warning.startswith("Detail enrichment failed: ")
            for warning in warnings
        )
        if (
            not isinstance(procurement, dict) or procurement.get("source") != "not_requested"
            or procurement.keys() - {"source", "quantity_price_verified"}
            or procurement.get("quantity_price_verified") is not False
            or any(part.get(field) is not None for field in PROCUREMENT_FIELDS)
            or part.get("warehouse_stock") not in (None, {})
            or part.get("price_tiers") not in (None, [])
            or part_result(part, identifier)["status"] != "found"
            or detail_failed or access_issue(part)
        ):
            continue
        key = identifier.casefold()
        if key in parts:
            raise ValueError("--reuse 报告含重复的可复用料号")
        parts[key] = part
    return parts


async def lookup(session, queries, refresh=False, timeout=120):
    results = []
    stopped = None
    for query in queries:
        identifier = query["identifier"]
        if stopped:
            results.append({**query, "status": "unqueried", "reason": stopped})
            continue
        try:
            async with asyncio.timeout(timeout):
                response = await session.call_tool(
                    TOOL, {"identifier": identifier, "include_procurement": False, "refresh": refresh},
                )
        except Exception:
            stopped = "MCP_CALL_FAILED"
            results.append({**query, "status": "error", "error": {
                "code": stopped, "message": "MCP 调用中断，已保留此前结果",
            }})
            continue
        result = part_result(tool_body(response), identifier)
        results.append({**query, **result})
        error_code = result.get("error", {}).get("code")
        if isinstance(error_code, str) and error_code in STOP_CODES:
            stopped = error_code
        elif "part" in result:
            stopped = access_issue(result["part"])
    return results


async def query_configured(queries, refresh):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise ValueError("请用已安装 MCP Python SDK 的环境运行") from exc

    config = component_config()
    transport = config["transport"]
    environment = {key: os.environ[key] for key in (transport.get("env_vars") or []) if key in os.environ}
    environment.update(transport.get("env") or {})
    server = StdioServerParameters(
        command=transport["command"], args=transport.get("args") or [],
        env=environment or None, cwd=transport.get("cwd"),
    )
    startup_timeout = config.get("startup_timeout_sec") or 30
    timeout = config.get("tool_timeout_sec") or 120
    results = None
    try:
        with open(os.devnull, "w", encoding="utf-8") as diagnostics:
            async with stdio_client(server, errlog=diagnostics) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    async with asyncio.timeout(startup_timeout):
                        await session.initialize()
                    results = await lookup(session, queries, refresh=refresh, timeout=timeout)
    except Exception as exc:
        if results is None:
            raise ValueError("本机 components stdio 会话未能初始化") from exc
    return results


def write_report(path, report):
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as output:
        output.write(payload)


def main(argv=None):
    parser = ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("input", type=Path, help="精确 C 号/完整 MPN 的 JSON 数组或 CSV")
    parser.add_argument("--column", help="CSV 要查询的唯一列名")
    parser.add_argument("--output", type=Path, help="保存完整结果的新 JSON 文件")
    parser.add_argument("--plan", action="store_true", help="只统计输入、复用和待查项，不发起查询")
    parser.add_argument("--reuse", type=Path, help="复用上次完整 JSON 报告里的精确技术详情")
    parser.add_argument("--skip-blank", action="store_true", help="跳过空白料号并报告原行")
    parser.add_argument("--refresh", action="store_true", help="重新查询全部料号并绕过缓存")
    args = parser.parse_args(argv)
    try:
        count, queries, blank_rows = plan_queries(args.input, args.column, args.skip_blank)
        previous = reusable_parts(args.reuse) if args.reuse and not args.refresh else {}
        reused = {}
        pending = []
        for query in queries:
            identifier = query["identifier"]
            part = previous.get(identifier.casefold())
            if part is None:
                pending.append(query)
            else:
                reused[identifier] = {**query, "status": "found", "part": part, "reused": True}
        summary = {
            "input_count": count, "unique_count": len(queries), "reused": len(reused),
            "to_query": len(pending), "blank_count": len(blank_rows),
        }
        if args.plan:
            if args.output:
                raise ValueError("--plan 从标准输出返回计划，不创建文件")
            print(json.dumps({"status": "planned", **summary}, ensure_ascii=False))
            return 0
        if args.output and (args.output.suffix.lower() != ".json" or args.output.exists() or args.output.is_symlink()):
            raise ValueError("--output 需要新的 .json 文件")
        if args.output and not args.output.parent.is_dir():
            raise ValueError("--output 的目录需要已存在")
        queried = asyncio.run(query_configured(pending, args.refresh)) if pending else []
        by_identifier = reused | {row["identifier"]: row for row in queried}
        results = [by_identifier[query["identifier"]] for query in queries]
        summary.update({status: sum(item["status"] == status for item in results)
                        for status in ("found", "error", "unqueried")})
        summary["status"] = "complete" if summary["found"] == len(queries) and not blank_rows else "partial"
        report = {**summary, "results": results, "blank_rows": blank_rows}
        if args.output:
            write_report(args.output, report)
            report = {**summary, "output": str(args.output)}
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        return 0 if summary["status"] == "complete" else 1
    except (OSError, ValueError, csv.Error) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
