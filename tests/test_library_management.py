import json
from pathlib import Path

from szlcsc_mcp.library import (
    audit_library,
    load_index,
    repair_library,
    save_index,
    search_entries,
    upsert_part,
)
from szlcsc_mcp.models import Part


def make_part(
    code: str,
    *,
    mpn: str,
    category: str,
    brand: str = "TEST",
    package: str = "SOT-23",
    stock: int = 10,
    price: float = 1.0,
) -> Part:
    return Part(
        lcsc_code=code,
        mpn=mpn,
        category=category,
        brand=brand,
        package=package,
        stock=stock,
        total_stock=stock,
        small_batch_price_cny=price,
    )


def add_archived_part(root: Path, part: Part, *, with_step: bool = True, with_datasheet: bool = True):
    base = f"{part.category}-{part.mpn}-{part.lcsc_code}"
    sch = root / "classified" / "schlib" / str(part.category) / f"{base}.SchLib"
    pcb = root / "classified" / "pcblib" / str(part.category) / f"{base}.PcbLib"
    step = root / "classified" / "3d" / str(part.category) / f"{base}.step"
    pdf = root / "classified" / "datasheets" / str(part.category) / f"{base}.pdf"
    for path, payload in ((sch, b"sch"), (pcb, b"pcb")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    if with_step:
        step.parent.mkdir(parents=True, exist_ok=True)
        step.write_bytes(b"step")
    if with_datasheet:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"pdf")
    archive = {
        "basename": base,
        "category_segments": [str(part.category)],
        "assets": {
            "schlib": {"classified": [str(sch)], "flat": []},
            "pcblib": {"classified": [str(pcb)], "flat": []},
            "step": {"classified": [str(step)] if with_step else [], "flat": []},
            "datasheet": {"classified": [str(pdf)] if with_datasheet else [], "flat": []},
            "reports": {"classified": [], "flat": []},
        },
    }
    review = {
        "case_dir": str(root / "gone-temp" / str(part.lcsc_code)),
        "quality": {"has_schlib": True, "has_pcblib": True, "concerns": []},
        "schlibs": [],
        "pcblibs": [],
        "step_files": [],
    }
    return upsert_part(root, part, review, archive=archive)


def test_library_search_filters_assets_stock_and_price(tmp_path):
    root = tmp_path / "lib"
    add_archived_part(root, make_part("C1", mpn="REF3033", category="电压基准芯片", price=2.5))
    add_archived_part(root, make_part("C2", mpn="TPR3333", category="电压基准芯片", price=1.2, stock=0), with_step=False)
    add_archived_part(root, make_part("C3", mpn="INA181A2", category="电流检测芯片", price=0.8))
    rows = search_entries(
        load_index(root),
        category="电压基准芯片",
        in_stock=True,
        has_step=True,
        sort_by="price",
    )
    assert [row["lcsc_code"] for row in rows] == ["C1"]
    assert rows[0]["assets"]["has_schlib"] is True


def test_library_search_query_relevance_prefers_exact_mpn(tmp_path):
    root = tmp_path / "lib"
    add_archived_part(root, make_part("C10", mpn="TPR3333-S3TR", category="电压基准芯片"))
    add_archived_part(root, make_part("C11", mpn="ABC3333", category="TPR3333 相关器件"))
    rows = search_entries(load_index(root), query="TPR3333-S3TR")
    assert rows[0]["lcsc_code"] == "C10"
    assert "mpn" in rows[0]["matched_fields"]


def test_library_audit_detects_missing_flat_mirrors(tmp_path):
    root = tmp_path / "lib"
    add_archived_part(root, make_part("C20", mpn="REF3033", category="电压基准芯片"))
    report = audit_library(root)
    assert report["checked_parts"] == 1
    assert report["repairable_count"] > 0
    assert any(item["kind"].startswith("flat_") for item in report["issues"])


def test_library_repair_dry_run_does_not_write_flat(tmp_path):
    root = tmp_path / "lib"
    saved = add_archived_part(root, make_part("C30", mpn="REF3033", category="电压基准芯片"))
    sch = Path(saved["entry"]["archive"]["assets"]["schlib"]["classified"][0])
    target = root / "flat" / sch.name
    result = repair_library(root, dry_run=True)
    assert result["action_count"] > 0
    assert target.exists() is False


def test_library_repair_restores_flat_and_updates_index(tmp_path):
    root = tmp_path / "lib"
    saved = add_archived_part(root, make_part("C40", mpn="REF3033", category="电压基准芯片"))
    sch = Path(saved["entry"]["archive"]["assets"]["schlib"]["classified"][0])
    target = root / "flat" / sch.name
    result = repair_library(root, dry_run=False)
    assert target.read_bytes() == sch.read_bytes()
    assert result["post_audit"]["severity_counts"]["error"] == 0
    entry = load_index(root)["parts"]["C40"]
    assert str(target) in entry["archive"]["assets"]["schlib"]["flat"]


def test_library_repair_replaces_corrupt_flat_mirror(tmp_path):
    root = tmp_path / "lib"
    saved = add_archived_part(root, make_part("C50", mpn="REF3033", category="电压基准芯片"))
    sch = Path(saved["entry"]["archive"]["assets"]["schlib"]["classified"][0])
    target = root / "flat" / sch.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"bad")
    index = load_index(root)
    index["parts"]["C50"]["archive"]["assets"]["schlib"]["flat"] = [str(target)]
    save_index(root, index)
    assert any(item["kind"] == "flat_schlib_mismatch" for item in audit_library(root)["issues"])
    repair_library(root, dry_run=False)
    assert target.read_bytes() == b"sch"


def test_library_repair_reconciles_invalid_cad_owner(tmp_path):
    root = tmp_path / "lib"
    add_archived_part(root, make_part("C60", mpn="SAME", category="测试"))
    upsert_part(root, make_part("C61", mpn="SAME", category="测试"), None)
    index = load_index(root)
    index["parts"]["C61"]["cad_owner"] = "C999"
    index["parts"]["C61"]["cad_shared"] = False
    save_index(root, index)
    assert any(item["kind"] == "cad_owner_missing" for item in audit_library(root)["issues"])
    result = repair_library(root, dry_run=False)
    entry = load_index(root)["parts"]["C61"]
    assert entry["cad_owner"] == "C60"
    assert entry["cad_shared"] is True
    assert any(a["action"] == "reconcile_cad_owner" for a in result["actions"])
