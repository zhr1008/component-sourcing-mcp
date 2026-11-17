import csv
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "skills/component-sourcing/scripts/plan_selection_groups.py"


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        csv.writer(file).writerows(rows)


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *map(str, args)],
        capture_output=True, encoding="utf-8", check=False, timeout=10,
    )


def test_equivalent_complete_conditions_group_without_changing_units_or_references(tmp_path):
    source = tmp_path / "阻容清单.csv"
    write_csv(source, [
        ("位号", "阻值", "封装", "功率"),
        (" R1 ", " 1m ", "0402", "100mW"),
        ("R2,R3", "1m", " 0402", "100mW"),
        ("R4\nR5", "1M", "0402", "100mW"),
        ("R6", "1m", "0402", "125mW"),
    ])

    result = run_cli(source, "--key", "阻值", "--key", "封装", "--key", "功率", "--reference", "位号")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "complete", "input_count": 4, "grouped_count": 4,
        "group_count": 3, "ungrouped_count": 0,
        "groups": [
            {"conditions": {"阻值": "1m", "封装": "0402", "功率": "100mW"},
             "input_rows": [2, 3], "references": [" R1 ", "R2,R3"]},
            {"conditions": {"阻值": "1M", "封装": "0402", "功率": "100mW"},
             "input_rows": [4], "references": ["R4\nR5"]},
            {"conditions": {"阻值": "1m", "封装": "0402", "功率": "125mW"},
             "input_rows": [5], "references": ["R6"]},
        ],
        "ungrouped": [],
    }


def test_incomplete_rows_remain_separate_and_file_output_preserves_details(tmp_path):
    source = tmp_path / "rows.csv"
    output = tmp_path / "plan.json"
    write_csv(source, [
        ("位号", "容值", "精度", "耐压"),
        ("C1", "1uF", "", "待核实"),
        ("C2", "1uF", "", "待核实"),
        ("C3", "1uF", "10%", "50V"),
    ])
    options = ("--key", "容值", "--key", "精度", "--key", "耐压", "--reference", "位号")

    result = run_cli(source, *options, "--output", output)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout) == {
        "status": "partial", "input_count": 3, "grouped_count": 1,
        "group_count": 1, "ungrouped_count": 2, "output": str(output),
    }
    full = json.loads(output.read_text(encoding="utf-8"))
    assert full["ungrouped"] == [
        {"input_row": 2, "missing_keys": ["精度", "耐压"], "reference": "C1"},
        {"input_row": 3, "missing_keys": ["精度", "耐压"], "reference": "C2"},
    ]
    assert full["groups"] == [
        {"conditions": {"容值": "1uF", "精度": "10%", "耐压": "50V"},
         "input_rows": [4], "references": ["C3"]},
    ]

    original = output.read_bytes()
    replay = run_cli(source, *options, "--output", output)
    assert replay.returncode == 2
    assert output.read_bytes() == original


def test_duplicate_columns_and_malformed_rows_fail_without_writing_plan(tmp_path):
    source = tmp_path / "rows.csv"
    output = tmp_path / "plan.json"
    write_csv(source, [("位号", "容值", "容值"), ("C1", "1uF", "1uF")])
    result = run_cli(source, "--key", "容值", "--output", output)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "failed"
    assert not output.exists()

    write_csv(source, [("位号", "容值"), ("C1", "1uF", "unmapped")])
    result = run_cli(source, "--key", "容值", "--output", output)
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "failed"
    assert not output.exists()
