import json
import runpy
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/component-sourcing/scripts/render_ad_selection.py"
C_CODE = "C900000001"
PRODUCT = "https://example.org/product/C900000001?type=a&rev=b"


class Table(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.rows = []
        self.links = []
        self.images = 0
        self.in_header = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.rows.append([])
        if tag in {"th", "td"}:
            self.rows[-1].append("")
            self.in_header = tag == "th"
        if tag == "button":
            self.rows[-1][-1] = attrs["data-copy"]
        if tag == "a":
            self.links.append(attrs["href"])
        if tag == "img":
            self.images += 1

    def handle_endtag(self, tag):
        if tag == "th":
            self.in_header = False

    def handle_data(self, data):
        if self.in_header:
            self.rows[-1][-1] += data


def run_cli(*args, payload=None):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *map(str, args)],
        input=None if payload is None else json.dumps(payload, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, check=False,
    )


def read_spreadsheet(path):
    uri = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rows = []
    for row in root.findall(f".//{{{uri}}}row"):
        cells = list(row)
        assert all(c.get("t") == "inlineStr" and c.find(f".//{{{uri}}}f") is None for c in cells)
        rows.append([(c.findtext(f".//{{{uri}}}t") or "") for c in cells])
    return workbook.find(f".//{{{uri}}}sheet").get("name"), rows


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def reviewed_row(**updates):
    return {
        "component_type": "运算放大器", "ad_comment": "TEST-Q1/NOPB", "purpose": '=1+1 & <img src=x>',
        "lcsc_code": C_CODE, "package": "TSOT-23", "specific_description": "运算放大器/待核实",
        "product_url": PRODUCT, **updates,
    }


def test_both_formats_match_with_mixed_rows_and_safe_literal_cells(tmp_path):
    resistor = reviewed_row(
        designator="R1", component_type="贴片电阻", ad_comment="10K", package="0402", purpose="上拉",
        generic_description="贴片电阻/10kΩ/100mW/±1%/待核实/0402",
        specific_description=f"贴片电阻/10kΩ/TEST-0402/100mW/0402/{C_CODE}/FH(风华)",
    )
    amplifier = reviewed_row(lcsc_code="C900000002", package="待核实", product_url="待核实")
    payload = {"reviewed": True, "rows": [resistor, amplifier]}
    stem = tmp_path / "selection"
    result = run_cli("-", "--output", stem, "--xlsx", payload=payload)
    assert result.returncode == 0, result.stderr
    table = Table(stem.with_suffix(".html").read_text(encoding="utf-8"))
    tab, workbook = read_spreadsheet(stem.with_suffix(".xlsx"))
    assert workbook == table.rows and tab == "AD 选型表"
    assert workbook[0] == ["位号", "型号（Comment）", "料号", "用途", "封装", "通用描述", "具体描述", "商品页链接"]
    assert workbook[1][4:7] == ["0402", resistor["generic_description"], resistor["specific_description"]]
    assert workbook[2] == ["", amplifier["ad_comment"], amplifier["lcsc_code"], amplifier["purpose"],
                           "待核实", "", amplifier["specific_description"], "待核实"]
    assert table.images == 0 and table.links == [PRODUCT]


def test_nonpassive_html_default_and_excel_backfill_does_not_replace_it(tmp_path):
    path = write_json(tmp_path / "reviewed.json", {"reviewed": True, "rows": [reviewed_row()]})
    stem = tmp_path / "selection"
    assert run_cli(path, "--output", stem).returncode == 0
    html_file = stem.with_suffix(".html")
    old_html = html_file.read_bytes()
    modified_date = html_file.stat().st_mtime_ns
    assert not stem.with_suffix(".xlsx").exists()
    assert Table(old_html.decode()).rows[0] == ["型号（Comment）", "料号", "用途", "封装", "具体描述", "商品页链接"]
    assert run_cli(path, "--output", stem, "--xlsx-only", "--no-clobber").returncode == 0
    assert html_file.read_bytes() == old_html and html_file.stat().st_mtime_ns == modified_date
    assert read_spreadsheet(stem.with_suffix(".xlsx"))[1] == Table(old_html.decode()).rows


def test_no_clobber_checks_all_targets_and_default_allows_replacement(tmp_path):
    path = write_json(tmp_path / "reviewed.json", {"reviewed": True, "rows": [reviewed_row()]})
    stem = tmp_path / "selection"
    excel = stem.with_suffix(".xlsx")
    html = stem.with_suffix(".html")
    excel.write_bytes(b"existing workbook")

    result = run_cli(path, "--output", stem, "--xlsx", "--no-clobber")
    assert result.returncode != 0 and "输出文件已存在" in result.stderr
    assert excel.read_bytes() == b"existing workbook"
    assert not html.exists()

    result = run_cli(path, "--output", stem, "--xlsx")
    assert result.returncode == 0, result.stderr
    assert read_spreadsheet(excel)[1] == Table(html.read_text(encoding="utf-8")).rows


def test_no_clobber_final_write_protects_target_created_after_preflight(tmp_path):
    write_atomic = runpy.run_path(str(SCRIPT))["write_atomic"]
    target = tmp_path / "selection.html"
    target.write_bytes(b"other writer")

    with pytest.raises(FileExistsError):
        write_atomic(target, b"new selection", no_clobber=True)

    assert target.read_bytes() == b"other writer"
    assert list(tmp_path.iterdir()) == [target]


def test_raw_mcp_candidates_are_draft_and_prepare_does_not_render(tmp_path):
    raw = {"results": [{
        "component_type": "贴片电容", "mpn": "TEST-0402", "lcsc_code": C_CODE, "package": "0402",
        "brand": "示例品牌", "parameters": {"capacitance": "100nF"}, "match_status": "catalog_match",
        "ad_comment": "100nF/50V", "ad_description": "贴片电容/100nF/50V/±10%/X7R/0402",
        "evidence": {"product_url": PRODUCT},
    }]}
    structured = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": raw}}
    path = write_json(tmp_path / "mcp.json", structured)
    stem = tmp_path / "selection"
    reviewed = tmp_path / "review.json"
    rejected = run_cli(path, "--output", stem)
    assert rejected.returncode != 0 and "候选" in rejected.stderr
    accepted = run_cli(path, "--draft", "--write-rows", reviewed)
    assert accepted.returncode == 0, accepted.stderr
    assert list(tmp_path.glob("*.html")) == []
    data = json.loads(reviewed.read_text(encoding="utf-8"))
    assert data["reviewed"] is False
    row = data["rows"][0]
    assert row["package"] == "0402" and row["product_url"] == PRODUCT
    assert row["generic_description"] == raw["results"][0]["ad_description"]
    assert row["specific_description"] == "待核实"
    assert row["source"] == raw["results"][0]
    assert run_cli(reviewed, "--output", stem).returncode != 0

    text_content = {"content": [{"type": "text", "text": json.dumps(raw, ensure_ascii=False)}]}
    draft = run_cli("-", "--draft", "--output", stem, "--xlsx", payload=text_content)
    assert draft.returncode == 0, draft.stderr
    assert "待核对底稿" in stem.with_suffix(".html").read_text(encoding="utf-8")
    assert "待核对" in read_spreadsheet(stem.with_suffix(".xlsx"))[0]


def test_bad_url_hidden_chars_wrong_text_type_and_tool_error_fail_before_outputs(tmp_path):
    stem = tmp_path / "selection"
    inputs = [
        {"reviewed": True, "rows": [reviewed_row(product_url="javascript:alert(1)")]},
        {"reviewed": True, "rows": [reviewed_row(ad_comment="ABC\u200b")]},
        {"reviewed": True, "rows": [reviewed_row(package=402)]},
        {"rows": [reviewed_row()]},
        {"isError": True, "structuredContent": {"reviewed": True, "rows": [reviewed_row()]}},
    ]
    for value in inputs:
        assert run_cli("-", "--output", stem, payload=value).returncode != 0
        assert not stem.with_suffix(".html").exists()


def test_batch_report_handoff_keeps_evidence_and_only_drafts_found_items(tmp_path):
    part = {"lcsc_code": C_CODE, "mpn": "TEST-0402", "package": "0402", "product_url": PRODUCT,
            "parameters": {"功率": "100mW"}, "procurement": {"source": "not_requested"}}
    report = {"status": "partial", "input_count": 3, "unique_count": 2, "results": [
        {"identifier": C_CODE, "input_rows": [1, 3], "status": "found", "part": part},
        {"identifier": "OTHER", "input_rows": [2], "status": "error"},
    ]}
    source = write_json(tmp_path / "technical.json", report)
    draft = tmp_path / "draft.json"
    result = run_cli(source, "--draft", "--write-rows", draft)
    assert result.returncode == 0, result.stderr
    assert "来源报告" in result.stderr
    assert not list(tmp_path.glob("*.html"))
    data = json.loads(draft.read_text(encoding="utf-8"))
    assert data["reviewed"] is False and len(data["rows"]) == 1
    row = data["rows"][0]
    assert (row["lcsc_code"], row["package"], row["product_url"]) == (C_CODE, "0402", PRODUCT)
    assert row["specific_description"] == row["ad_comment"] == "待核实"
    assert row["source"]["input_rows"] == [1, 3] and row["source"]["parameters"] == part["parameters"]
    assert run_cli(draft, "--output", tmp_path / "final").returncode != 0
