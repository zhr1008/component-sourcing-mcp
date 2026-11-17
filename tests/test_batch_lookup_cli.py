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


def test_invalid_input_and_existing_output_stop_before_mcp(monkeypatch, tmp_path, capsys, installed_components):
    _, config_calls, audit = installed_components
    csv = tmp_path / "parts.csv"
    csv.write_text("MPN,notes\nC900000001,x\n", encoding="utf-8")
    assert run_cli(monkeypatch, capsys, csv)[0] == 2
    invalid = write_json(tmp_path / "bad.json", {"identifier": "C900000001"})
    assert run_cli(monkeypatch, capsys, invalid)[0] == 2
    source = write_json(tmp_path / "parts.json", ["C900000001"])
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
