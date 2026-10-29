import json
import csv
from pathlib import Path

import pytest

from szlcsc_mcp.library_base import empty_index, part_entry, rebuild_indexes, save_index
from szlcsc_mcp.library_maintenance import export_library, preview_entry_changes, scan_orphan_files
from szlcsc_mcp.models import Part


def _part(**overrides) -> Part:
    data = {
        "lcsc_code": "C5291487",
        "mpn": "TPR3333-S3TR",
        "brand": "3PEAK",
        "package": "SOT-23G-3",
        "category": "电压基准芯片",
        "small_batch_price_cny": 1.23,
        "stock": 100,
        "total_stock": 100,
        "datasheet_url": "https://atta.szlcsc.com/example.pdf",
        "product_url": "https://item.szlcsc.com/5291487.html",
        "min_order_qty": 1,
        "order_multiple": 1,
        "packaging": "编带",
    }
    data.update(overrides)
    return Part(**data)


def _library_with_one_part(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "library"
    classified = root / "classified" / "datasheets" / "电压基准芯片"
    flat = root / "flat"
    classified.mkdir(parents=True)
    flat.mkdir(parents=True)
    pdf = classified / "电压基准芯片-TPR3333-S3TR-C5291487.pdf"
    flat_pdf = flat / pdf.name
    pdf.write_bytes(b"%PDF-test")
    flat_pdf.write_bytes(b"%PDF-test")
    archive = {
        "basename": "电压基准芯片-TPR3333-S3TR-C5291487",
        "assets": {
            "datasheet": {"classified": [str(pdf)], "flat": [str(flat_pdf)]},
            "schlib": {"classified": [], "flat": []},
            "pcblib": {"classified": [], "flat": []},
            "step": {"classified": [], "flat": []},
            "reports": {"classified": [], "flat": []},
        },
    }
    entry = part_entry(_part(), archive=archive)
    index = empty_index()
    index["parts"]["C5291487"] = entry
    rebuild_indexes(index)
    save_index(root, index)
    return root, entry


def test_preview_groups_procurement_datasheet_and_cad_identity_changes():
    old = part_entry(_part())
    fresh = _part(
        mpn="TPR3333-S3TR-NEW",
        stock=250,
        total_stock=250,
        datasheet_url="https://atta.szlcsc.com/new.pdf",
    )
    result = preview_entry_changes(old, fresh)
    assert result["changed"] is True
    assert result["cad_identity_changed"] is True
    assert result["would_require_cad_review"] is True
    assert "mpn" in result["groups"]["cad_identity"]
    assert "stock" in result["groups"]["procurement"]
    assert "datasheet_url" in result["groups"]["datasheet"]
    assert result["cad_rebuild_planned"] is False


def test_export_writes_json_and_excel_friendly_csv(tmp_path):
    root, _ = _library_with_one_part(tmp_path)
    result = export_library(root, output_dir=tmp_path / "exports", export_format="both")
    assert result["ok"] is True
    assert result["offline"] is True
    assert result["count"] == 1
    json_path = Path(result["files"]["json"])
    csv_path = Path(result["files"]["csv"])
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["parts"][0]["lcsc_code"] == "C5291487"
    assert payload["parts"][0]["has_datasheet"] is True
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "C5291487" in csv_path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("value", ["=1+1", "+1+1", "-1+1", "@SUM(1)", " \t=1+1", "\r=1+1", "\n=1+1", "safe-MPN"])
def test_inventory_csv_preserves_text_without_executing_formulas(tmp_path, value):
    root = tmp_path / "library"
    index = empty_index()
    index["parts"]["C1"] = part_entry(_part(lcsc_code="C1", mpn=value, small_batch_price_cny=-1.0))
    rebuild_indexes(index)
    save_index(root, index)
    result = export_library(root, output_dir=tmp_path / "exports")
    with Path(result["files"]["csv"]).open(encoding="utf-8-sig", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["mpn"] == (value if value == "safe-MPN" else "'" + value)
    assert row["small_batch_price_cny"] == "-1.0"
    original = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    assert original["parts"][0]["mpn"] == value


def test_orphan_scan_reports_unreferenced_files_but_not_referenced_assets(tmp_path):
    root, entry = _library_with_one_part(tmp_path)
    referenced = Path(entry["archive"]["assets"]["datasheet"]["classified"][0])
    orphan_flat = root / "flat" / "manual-leftover.txt"
    orphan_classified = root / "classified" / "3d" / "未分类" / "unused.step"
    orphan_flat.write_text("leftover", encoding="utf-8")
    orphan_classified.parent.mkdir(parents=True, exist_ok=True)
    orphan_classified.write_text("step", encoding="utf-8")

    result = scan_orphan_files(root, include_hashes=True)
    paths = {row["path"] for row in result["orphans"]}
    assert str(orphan_flat.resolve()) in paths
    assert str(orphan_classified.resolve()) in paths
    assert str(referenced.resolve()) not in paths
    assert result["orphan_count"] == 2
    assert result["read_only"] is True
