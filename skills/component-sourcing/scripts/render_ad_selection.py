"""Render a reviewed AD selection table, or prepare a draft from an MCP result."""

import argparse
import io
import json
import sys
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from string import Template
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit


UNKNOWN = "待核实"
TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "ad-selection.html"
PASSIVE_ENDINGS = ("电阻", "电容", "电感")
PASSIVE_TYPES = {"电阻阵列", "电感阵列", "电容阵列", "resistor", "capacitor", "inductor"}
SOURCE_FIELDS = (
    "mpn", "lcsc_code", "brand", "package", "component_type", "parameters",
    "ad_comment", "ad_description", "match_status", "evidence", "procurement",
)
HEADERS = (
    ("designator", "位号"),
    ("ad_comment", "型号（Comment）"),
    ("lcsc_code", "料号"),
    ("purpose", "用途"),
    ("package", "封装"),
    ("generic_description", "通用描述"),
    ("specific_description", "具体描述"),
    ("product_url", "商品页链接"),
)


def is_passive(component_type):
    return component_type.casefold() in PASSIVE_TYPES or component_type.endswith(PASSIVE_ENDINGS)


def field_text(row, key, default=UNKNOWN):
    value = row.get(key)
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是文本，不能用 JSON 数字等类型代替。")
    if value != value.strip() or any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} or char in "\ufffe\uffff" for char in value
    ):
        raise ValueError(f"{key} 含首尾空白或不可见字符，请核对原字段。")
    if len(value.encode("utf-16-le")) // 2 > 32767:
        raise ValueError(f"{key} 超过 Excel 文本单元格长度上限。")
    return value


def validate_url(value):
    if value == UNKNOWN:
        return
    parts = urlsplit(value)
    if (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username
            or parts.password or "\\" in value or any(char.isspace() for char in value)):
        raise ValueError("product_url 必须是公开 HTTPS/HTTP 商品页链接，或写待核实。")


@dataclass(frozen=True)
class SelectionRow:
    designator: str
    ad_comment: str
    lcsc_code: str
    purpose: str
    package: str
    component_type: str
    generic_description: str
    specific_description: str
    product_url: str
    source: dict | None

    @classmethod
    def from_dict(cls, payload, draft=False):
        if not isinstance(payload, dict):
            raise ValueError("每一行必须是 JSON 对象。")
        for field in ("ad_comment", "lcsc_code", "package", "component_type", "specific_description", "product_url"):
            if not draft and field not in payload:
                raise ValueError(f"缺少 {field}，未取得的字段可写待核实。")

        component_type = field_text(payload, "component_type")
        generic = field_text(payload, "generic_description", "")
        if generic and not is_passive(component_type):
            raise ValueError("通用描述只用于阻容感，请核对 component_type。")
        url = field_text(payload, "product_url")
        validate_url(url)
        source = payload.get("source")
        if source is not None and not isinstance(source, dict):
            raise ValueError("source 必须是 JSON 对象。")
        return cls(
            field_text(payload, "designator", ""), field_text(payload, "ad_comment"),
            field_text(payload, "lcsc_code"), field_text(payload, "purpose", ""),
            field_text(payload, "package"), component_type,
            (generic or UNKNOWN) if is_passive(component_type) else "",
            field_text(payload, "specific_description"), url, source,
        )


def unwrap_payload(payload):
    """Accept a direct JSON payload, CallToolResult, or JSON-RPC tool response."""
    while isinstance(payload, dict):
        if payload.get("isError") or payload.get("is_error") or payload.get("ok") is False:
            raise ValueError("输入记录为工具失败，请先处理错误。")
        if payload.get("jsonrpc") == "2.0":
            if "result" not in payload:
                raise ValueError("JSON-RPC 响应无 result。")
            payload = payload["result"]
        elif "rows" in payload or "results" in payload:
            return payload
        elif payload.get("structuredContent") is not None:
            payload = payload["structuredContent"]
        elif payload.get("structured_content") is not None:
            payload = payload["structured_content"]
        else:
            content = payload.get("content")
            if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
                    or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str)):
                break
            payload = json.loads(content[0]["text"])
    raise ValueError("未找到规范化 rows 或 szlcsc_select 返回的 results。")


def candidate_row(payload):
    if not isinstance(payload, dict):
        raise ValueError("选型候选必须是 JSON 对象。")
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("选型候选 evidence 必须是 JSON 对象。")
    kind = field_text(payload, "component_type")
    generic_in_tool = kind.endswith("电容") or kind == "贴片电阻"
    return {
        "ad_comment": payload.get("ad_comment"),
        "lcsc_code": payload.get("lcsc_code"),
        "package": payload.get("package"),
        "component_type": kind,
        "generic_description": payload.get("ad_description") if generic_in_tool else "",
        # The current MCP provides only the generic Description for resistors and capacitors.
        "specific_description": UNKNOWN if generic_in_tool else payload.get("ad_description"),
        "product_url": evidence.get("product_url"),
        "source": {field: payload[field] for field in SOURCE_FIELDS if field in payload},
    }


def technical_lookup_rows(payload):
    rows = []
    skipped = 0
    for item in payload["results"]:
        if not isinstance(item, dict):
            raise ValueError("批查结果的每项必须是 JSON 对象。")
        if item.get("status") != "found":
            skipped += 1
            continue
        part = item.get("part")
        if not isinstance(part, dict) or not (part.get("lcsc_code") or part.get("mpn")):
            raise ValueError("批查命中条目缺少器件详情或料号。")
        rows.append({
            "lcsc_code": part.get("lcsc_code"),
            "package": part.get("package"),
            "product_url": part.get("product_url"),
            "source": dict(part, identifier=item.get("identifier"), input_rows=item.get("input_rows")),
        })
    if skipped or payload.get("blank_count"):
        print("批查未取得详情的项目保留在来源报告，底稿只包含已命中条目。", file=sys.stderr)
    return rows


def load_rows(payload, draft=False):
    payload = unwrap_payload(payload)
    if "rows" in payload:
        values = payload["rows"]
        if "reviewed" in payload and not isinstance(payload["reviewed"], bool):
            raise ValueError("reviewed 必须是布尔值。")
        if payload.get("reviewed") is not True and not draft:
            raise ValueError("核对字段后明确设置 reviewed: true，或显式预览 --draft。")
    else:
        if not draft:
            raise ValueError("工具结果是候选或技术详情，需要先核对。使用 --draft --write-rows 生成待核对底稿。")
        if not isinstance(payload["results"], list):
            raise ValueError("results 必须是 JSON 数组。")
        if "input_count" in payload and "unique_count" in payload:
            values = technical_lookup_rows(payload)
        else:
            values = [candidate_row(row) for row in payload["results"]]
    if not isinstance(values, list) or not values:
        raise ValueError("选型表至少需要一条记录。")

    rows = []
    for index, value in enumerate(values, 1):
        try:
            rows.append(SelectionRow.from_dict(value, draft=draft))
        except ValueError as exc:
            raise ValueError(f"第 {index} 行：{exc}") from exc
    return rows


def table_values(rows):
    columns = dict(HEADERS)
    if not any(row.designator for row in rows):
        del columns["designator"]
    if not any(is_passive(row.component_type) for row in rows):
        del columns["generic_description"]
    return columns, [tuple(getattr(row, field) for field in columns) for row in rows]


def html_cell(value, key, heading):
    if not value:
        return "<td></td>"
    text = escape(value, quote=True)
    link = ""
    if key == "product_url" and value != UNKNOWN:
        link = f' <a href="{text}" target="_blank" rel="noopener noreferrer">打开</a>'
    return f'<td><button type="button" data-copy="{text}" title="复制{heading}">{text}</button>{link}</td>'


def render_html(columns, values, draft):
    headings = "".join(f'<th scope="col">{heading}</th>' for heading in columns.values())
    cells = [
        "<tr>" + "".join(html_cell(value, key, heading) for (key, heading), value in zip(columns.items(), row))
        + "</tr>"
        for row in values
    ]
    note = '<p class="draft">待核对底稿。请按精确料号核对商品页字段，电气和库证据另行复核。</p>' if draft else ""
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        title="AD 选型表 · 待核对底稿" if draft else "AD 选型表",
        draft_note=note,
        headings=headings,
        rows="\n".join(cells),
    ).encode("utf-8")


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCUMENT_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORKBOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
WORKSHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"


def render_xlsx(headings, values, draft):
    # This fixed table has at most eight columns. Inline strings preserve exact
    # text and never turn '+', '-' or '=' into a formula.
    sheet_rows = []
    for index, row in enumerate((tuple(headings), *values), 1):
        cells = "".join(
            f'<c r="{column}{index}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
            for column, value in zip("ABCDEFGH", row)
        )
        sheet_rows.append(f'<row r="{index}">{cells}</row>')

    sheet_name = "AD 选型表 - 待核对" if draft else "AD 选型表"
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{WORKBOOK}"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{WORKSHEET}"/></Types>'
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{PACKAGE_RELS}"><Relationship Id="rId1"'
            f' Type="{DOCUMENT_RELS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{DOCUMENT_RELS}">'
            f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{PACKAGE_RELS}"><Relationship Id="rId1"'
            f' Type="{DOCUMENT_RELS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
        ),
    }
    book = io.BytesIO()
    with zipfile.ZipFile(book, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8"?>' + content)
    return book.getvalue()


def write_atomic(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="已核对 rows 或 MCP 选型候选 JSON，'-' 从标准输入读取")
    parser.add_argument("--output", type=Path, metavar="STEM", help="输出路径不含 .html 或 .xlsx")
    excel = parser.add_mutually_exclusive_group()
    excel.add_argument("--xlsx", action="store_true", help="另生成与 HTML 同列同值的文本 Excel")
    excel.add_argument("--xlsx-only", action="store_true", help="只生成 Excel，保留同名 HTML")
    parser.add_argument("--draft", action="store_true", help="允许导入选型候选并标为待核对")
    parser.add_argument("--write-rows", type=Path, help="写待核对 JSON 底稿，可与 --draft 单独使用")
    args = parser.parse_args(argv)
    if not args.output and not args.write_rows:
        parser.error("提供 --output STEM 或 --draft --write-rows FILE.json。")
    if (args.xlsx or args.xlsx_only) and not args.output:
        parser.error("--xlsx 或 --xlsx-only 需要 --output STEM。")
    if args.write_rows and not args.draft:
        parser.error("--write-rows 需要 --draft。")

    try:
        read_stdin = args.input == Path("-")
        source = sys.stdin.buffer.read().decode("utf-8-sig") if read_stdin else args.input.read_text(encoding="utf-8-sig")
        rows = load_rows(json.loads(source), draft=args.draft)
        outputs = {}
        if args.output:
            columns, values = table_values(rows)
            if not args.xlsx_only:
                outputs[Path(f"{args.output}.html")] = render_html(columns, values, args.draft)
            if args.xlsx or args.xlsx_only:
                outputs[Path(f"{args.output}.xlsx")] = render_xlsx(columns.values(), values, args.draft)
        if args.write_rows:
            draft_path = args.write_rows
            if draft_path.suffix.lower() != ".json":
                raise ValueError("--write-rows 的输出必须使用 .json。")
            draft_rows = {
                "reviewed": False,
                "rows": [{key: value for key, value in asdict(row).items() if value is not None} for row in rows],
            }
            outputs[draft_path] = (json.dumps(draft_rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        paths = [path.resolve() for path in outputs]
        if (not read_stdin and args.input.resolve() in paths) or len(set(paths)) != len(paths):
            raise ValueError("输入、底稿和导出文件需要使用不同路径。")
        for path, content in outputs.items():
            write_atomic(path, content)
            print(path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
