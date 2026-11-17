import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/component-sourcing/scripts/batch_lookup.py"
FAKE_SERVER = """
import json
from pathlib import Path
import sys

audit = Path(sys.argv[1])
audit.with_suffix(".started").touch()
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    params = request.get("params", {})
    match request["method"]:
        case "initialize":
            result = {"protocolVersion": params["protocolVersion"],
                      "serverInfo": {"name": "lookup-fixture", "version": "1"},
                      "capabilities": {"tools": {}}}
        case "tools/list":
            result = {"tools": [{"name": "szlcsc_get_part", "inputSchema": {"type": "object"}}]}
        case "tools/call":
            with audit.open("a", encoding="utf-8") as log:
                log.write(json.dumps(params) + "\\n")
            identifier = params["arguments"]["identifier"]
            if identifier == "C900000008":
                continue
            part = {
                "lcsc_code": identifier if identifier.startswith("C") else "C900000006",
                "mpn": "Fixture-MPN" if identifier.startswith("C") else identifier,
                "brand": "FH(风华)",
                "package": "0402",
                "parameters": {"Unit": "100mW"},
                "product_url": "https://example.org/item",
                "total_stock": None,
                "procurement": {"source": "not_requested", "quantity_price_verified": False},
                "data_freshness": {"cache_hits": 1},
            }
            if identifier == "C900000002":
                part = {"ok": False, "error": {"code": "PART_NOT_FOUND", "message": "exact identity not found"}}
            if identifier == "C900000003":
                part["lcsc_code"] = "C999999999"
            if identifier == "C900000004":
                result = {"isError": True, "content": [], "structuredContent": {
                    "ok": False, "error": {"code": "RATE_LIMITED", "message": "wait", "retry_after_seconds": 60},
                }}
            elif identifier.startswith("Fixture-"):
                result = {"content": [], "structuredContent": part}
            else:
                result = {"content": [{"type": "text", "text": json.dumps(part, ensure_ascii=False)}]}
        case _:
            result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


@pytest.fixture
def installed_components(monkeypatch, tmp_path):
    server = tmp_path / "stdio_fixture.py"
    server.write_text(FAKE_SERVER, encoding="utf-8")
    audit = tmp_path / "requests.jsonl"
    config_calls = []
    config = {
        "name": "components",
        "enabled": True,
        "transport": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-X", "utf8", str(server), str(audit)],
            "cwd": str(tmp_path),
        },
        "startup_timeout_sec": 5,
        "tool_timeout_sec": 5,
    }
    original_run = subprocess.run
    original_which = shutil.which

    def which(name, *args, **kwargs):
        if Path(name).stem.lower() == "codex":
            return "codex.exe"
        return original_which(name, *args, **kwargs)

    def run(command, *args, **kwargs):
        if Path(command[0]).stem.lower() == "codex":
            assert list(command[1:]) == ["mcp", "get", "components", "--json"]
            config_calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(config), "")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", which)
    return config, config_calls, audit


def run_cli(monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *map(str, arguments)])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    return caught.value.code, capsys.readouterr()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def read_requests(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_plan_deduplicates_csv_without_starting_components(monkeypatch, tmp_path, capsys, installed_components):
    _, config_calls, audit = installed_components
    source = tmp_path / "source.csv"
    source.write_text(
        'MPN,notes\nC0900000001,"two\nlines"\nFixture-MPN/NOPB,x\nc 0900000001,y\nfixture-mpn/nopb,z\n',
        encoding="utf-8-sig",
    )
    code, output = run_cli(monkeypatch, capsys, source, "--column", "MPN", "--plan")
    assert code == 0, output.err
    summary = json.loads(output.out)
    assert summary["status"] == "planned"
    assert (summary["input_count"], summary["unique_count"]) == (4, 2)
    assert not config_calls and not audit.with_suffix(".started").exists()


def test_live_stdio_reads_each_identity_once_and_preserves_partial_evidence(
    monkeypatch, tmp_path, capsys, installed_components,
):
    _, config_calls, audit = installed_components
    source = write_json(tmp_path / "identifiers.json", [
        " c900000001 ", "Fixture-MPN/NOPB", "C900000001", "C900000002",
        "C900000003", "C900000004", "C900000005", "fixture-mpn/nopb",
    ])
    evidence = tmp_path / "evidence.json"
    code, output = run_cli(monkeypatch, capsys, source, "--refresh", "--output", evidence)
    assert code == 1, (output.out, output.err)
    report = json.loads(evidence.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    rows = report["results"]
    assert [row["identifier"] for row in rows] == [
        "C900000001", "Fixture-MPN/NOPB", "C900000002", "C900000003", "C900000004", "C900000005",
    ]
    assert [row["status"] for row in rows] == ["found", "found", "error", "error", "error", "unqueried"]
    assert [row["input_rows"] for row in rows] == [[1, 3], [2, 8], [4], [5], [6], [7]]
    assert rows[0]["part"]["package"] == "0402"
    assert rows[0]["part"]["parameters"] == {"Unit": "100mW"}
    assert rows[0]["part"]["brand"] == "FH(风华)"
    assert rows[0]["part"]["total_stock"] is None
    assert rows[0]["part"]["data_freshness"] == {"cache_hits": 1}
    assert rows[1]["part"]["mpn"] == "Fixture-MPN/NOPB"
    assert rows[2]["error"]["code"] == "PART_NOT_FOUND"
    assert "part" not in rows[3] or rows[3]["part"] is None
    assert rows[4]["error"]["code"] == "RATE_LIMITED"
    assert rows[4]["error"]["retry_after_seconds"] == 60
    assert len(config_calls) == 1
    assert read_requests(audit) == [
        {"name": "szlcsc_get_part", "arguments": {
            "identifier": row["identifier"], "include_procurement": False, "refresh": True,
        }}
        for row in rows[:5]
    ]


def test_stdout_csv_retains_logical_source_rows_and_normalizes_c_code(
    monkeypatch, tmp_path, capsys, installed_components,
):
    _, _, audit = installed_components
    source = tmp_path / "source.csv"
    source.write_text('ID,note\nc0900000001,"two\nlines"\nC 0900000001,y\n', encoding="utf-8-sig")
    code, output = run_cli(monkeypatch, capsys, source, "--column", "ID")
    assert code == 0, output.err
    report = json.loads(output.out)
    assert report["status"] == "complete"
    assert report["results"][0]["input_rows"] == [2, 3]
    assert read_requests(audit) == [{"name": "szlcsc_get_part", "arguments": {
        "identifier": "C0900000001", "include_procurement": False, "refresh": False,
    }}]


def test_reuse_partial_report_checks_static_details_and_merges_current_rows(
    monkeypatch, tmp_path, capsys, installed_components,
):
    _, config_calls, audit = installed_components
    previous_input = write_json(tmp_path / "previous-input.json", ["C900000001", "C900000002"])
    previous_report = tmp_path / "previous.json"
    code, output = run_cli(monkeypatch, capsys, previous_input, "--output", previous_report)
    assert code == 1, output.err
    old = json.loads(previous_report.read_text(encoding="utf-8"))
    template = old["results"][0]["part"]
    old["results"].extend([
        {"identifier": "C900000006", "status": "found", "part": {
            **template, "lcsc_code": "C900000006", "total_stock": 0,
        }},
        {"identifier": "C900000007", "status": "found", "part": {
            **template, "lcsc_code": "C900000007", "price_tiers": [{"unit_price": 1}],
        }},
        {"identifier": "C900000009", "status": "found", "part": {
            **template, "lcsc_code": "C900000009", "warnings": ["Detail enrichment failed: INVALID_RESPONSE"],
        }},
        {"identifier": "C900000010", "status": "found", "part": {
            **template, "lcsc_code": "C900000011",
        }},
        {"identifier": "C900000012", "status": "unqueried"},
    ])
    write_json(previous_report, old)
    source = write_json(tmp_path / "current.json", [
        "C900000002", "c900000001", "C900000006", "C900000001", "C900000007", "C900000009",
        "C900000010", "C900000012",
    ])
    code, output = run_cli(monkeypatch, capsys, source, "--reuse", previous_report)
    assert code == 1, output.err
    report = json.loads(output.out)
    assert (report["reused"], report["to_query"], report["found"]) == (1, 6, 6)
    assert report["results"][0]["error"]["code"] == "PART_NOT_FOUND"
    assert report["results"][1]["reused"] is True
    assert report["results"][1]["input_rows"] == [2, 4]
    assert report["results"][1]["part"] == template
    assert len(config_calls) == 2
    assert [entry["arguments"]["identifier"] for entry in read_requests(audit)[2:]] == [
        "C900000002", "C900000006", "C900000007", "C900000009", "C900000010", "C900000012",
    ]


def test_all_reused_and_plan_skip_mcp_and_refresh_bypasses_report(
    monkeypatch, tmp_path, capsys, installed_components,
):
    _, config_calls, audit = installed_components
    source = write_json(tmp_path / "current.json", ["Fixture-MPN/NOPB"])
    previous_report = write_json(tmp_path / "previous.json", {"results": [{
        "identifier": "fixture-mpn/nopb", "status": "found", "part": {
            "mpn": "Fixture-MPN/NOPB", "procurement": {"source": "not_requested", "quantity_price_verified": False},
        },
    }]})
    code, output = run_cli(monkeypatch, capsys, source, "--reuse", previous_report, "--plan")
    assert code == 0, output.err
    summary = json.loads(output.out)
    assert (summary["status"], summary["reused"], summary["to_query"]) == ("planned", 1, 0)
    code, output = run_cli(monkeypatch, capsys, source, "--reuse", previous_report)
    assert code == 0, output.err
    assert json.loads(output.out)["results"][0]["reused"] is True
    assert not config_calls and not audit.with_suffix(".started").exists()
    code, output = run_cli(monkeypatch, capsys, source, "--reuse", previous_report, "--refresh")
    assert code == 0, output.err
    summary = json.loads(output.out)
    assert (summary["reused"], summary["to_query"]) == (0, 1)
    assert read_requests(audit)[0]["arguments"]["refresh"] is True


def test_skip_blank_records_source_lines_and_all_blank_never_starts_mcp(
    monkeypatch, tmp_path, capsys, installed_components,
):
    _, config_calls, audit = installed_components
    source = tmp_path / "source.csv"
    source.write_text('ID,note\n,missing\n\nC900000001,"two\nlines"\n ,missing\n', encoding="utf-8-sig")
    assert run_cli(monkeypatch, capsys, source, "--column", "ID")[0] == 2
    code, output = run_cli(monkeypatch, capsys, source, "--column", "ID", "--skip-blank")
    assert code == 1, output.err
    report = json.loads(output.out)
    assert (report["input_count"], report["blank_count"], report["found"]) == (4, 3, 1)
    assert report["blank_rows"] == [2, 3, 5]
    assert report["results"][0]["input_rows"] == [4]
    assert [entry["arguments"]["identifier"] for entry in read_requests(audit)] == ["C900000001"]
    all_blank = write_json(tmp_path / "blank.json", ["", " "])
    code, output = run_cli(monkeypatch, capsys, all_blank, "--skip-blank")
    assert code == 1, output.err
    report = json.loads(output.out)
    assert report["blank_rows"] == [1, 2] and not report["results"]
    assert len(config_calls) == 1


def test_invalid_input_and_existing_output_stop_before_mcp(monkeypatch, tmp_path, capsys, installed_components):
    _, config_calls, audit = installed_components
    csv = tmp_path / "parts.csv"
    csv.write_text("MPN,notes\nC900000001,x\n", encoding="utf-8")
    assert run_cli(monkeypatch, capsys, csv)[0] == 2
    invalid = write_json(tmp_path / "bad.json", {"identifier": "C900000001"})
    assert run_cli(monkeypatch, capsys, invalid)[0] == 2
    source = write_json(tmp_path / "parts.json", ["C900000001"])
    incomplete_report = write_json(tmp_path / "summary.json", {"status": "complete"})
    assert run_cli(monkeypatch, capsys, source, "--reuse", incomplete_report)[0] == 2
    existing = tmp_path / "evidence.json"
    existing.write_text("original", encoding="utf-8")
    assert run_cli(monkeypatch, capsys, source, "--output", existing)[0] == 2
    assert existing.read_text(encoding="utf-8") == "original"
    assert not config_calls and not audit.with_suffix(".started").exists()


def test_tool_timeout_retains_prior_rows_and_skips_later_calls(
    monkeypatch, tmp_path, capsys, installed_components,
):
    configuration, _, audit = installed_components
    configuration["tool_timeout_sec"] = 0.25
    source = write_json(tmp_path / "identifiers.json", ["C900000001", "C900000008", "C900000009"])
    code, output = run_cli(monkeypatch, capsys, source)
    assert code == 1, output.err
    report = json.loads(output.out)
    assert [row["status"] for row in report["results"]] == ["found", "error", "unqueried"]
    assert [entry["arguments"]["identifier"] for entry in read_requests(audit)] == ["C900000001", "C900000008"]


@pytest.mark.parametrize("configured_policy", [
    {"enabled_tools": ["szlcsc_get_stock"]},
    {"enabled_tools": []},
    {"disabled_tools": ["szlcsc_get_part"]},
])
def test_respects_tool_visibility_policy_before_launch(
    monkeypatch, tmp_path, capsys, installed_components, configured_policy,
):
    configuration, config_calls, audit = installed_components
    configuration.update(configured_policy)
    source = write_json(tmp_path / "identifiers.json", ["C900000001"])
    code, _ = run_cli(monkeypatch, capsys, source)
    assert code == 2
    assert len(config_calls) == 1
    assert not audit.with_suffix(".started").exists()
