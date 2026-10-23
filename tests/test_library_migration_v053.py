from pathlib import Path

from szlcsc_mcp.library_base import _index_path, _sha256, empty_index, load_index, part_entry, rebuild_indexes, save_index
from szlcsc_mcp.library_migration import migrate_library, plan_library_migration
from szlcsc_mcp.models import Part
from szlcsc_mcp.storage import ARCHIVE_LAYOUT_VERSION, archive_existing_files


def _part() -> Part:
    return Part(
        lcsc_code="C5291487",
        mpn="TPR3333-S3TR",
        brand="3PEAK",
        package="SOT-23G-3",
        category="电压基准芯片",
        datasheet_url="https://atta.szlcsc.com/example.pdf",
    )


def _legacy_library(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    root = tmp_path / "library"
    legacy = root / "legacy"
    case_dir = root / "altium" / "C5291487"
    flat = root / "flat"
    legacy.mkdir(parents=True)
    case_dir.mkdir(parents=True)
    flat.mkdir(parents=True)

    pdf = legacy / "C5291487.pdf"
    flat_pdf = flat / "legacy-C5291487.pdf"
    sch = case_dir / "C5291487.SchLib"
    pcb = case_dir / "C5291487.PcbLib"
    step = case_dir / "C5291487.step"
    pdf.write_bytes(b"%PDF-legacy")
    flat_pdf.write_bytes(b"%PDF-legacy")
    sch.write_bytes(b"legacy-sch")
    pcb.write_bytes(b"legacy-pcb")
    step.write_bytes(b"legacy-step")

    archive = {
        "layout_version": 1,
        "basename": "legacy-C5291487",
        "assets": {
            "datasheet": {"classified": [str(pdf)], "flat": [str(flat_pdf)]},
            "schlib": {"classified": [], "flat": []},
            "pcblib": {"classified": [], "flat": []},
            "step": {"classified": [], "flat": []},
            "reports": {"classified": [], "flat": []},
        },
    }
    review = {
        "case_dir": str(case_dir),
        "schlibs": [str(sch)],
        "pcblibs": [str(pcb)],
        "step_files": [str(step)],
        "quality": {"has_schlib": True, "has_pcblib": True, "concerns": []},
    }
    entry = part_entry(_part(), review=review, archive=archive)
    index = empty_index()
    index["parts"]["C5291487"] = entry
    rebuild_indexes(index)
    save_index(root, index)
    return root, {"pdf": pdf, "flat_pdf": flat_pdf, "sch": sch, "pcb": pcb, "step": step}


def test_migration_plan_detects_v1_archive_and_case_assets(tmp_path):
    root, _ = _legacy_library(tmp_path)
    result = plan_library_migration(root)
    assert result["ok"] is True
    assert result["safe_to_apply"] is True
    assert result["target_layout_version"] == ARCHIVE_LAYOUT_VERSION == 2
    assert result["needs_migration_count"] == 1
    assert result["blocker_count"] == 0
    assert result["copy_count"] > 0
    row = result["parts"][0]
    assert set(row["case_sources_used"]) == {"schlib", "pcblib", "step"}
    assert row["target_basename"] == "电压基准芯片-TPR3333-S3TR-C5291487"


def test_migration_dry_run_does_not_write_index_or_copy_files(tmp_path):
    root, _ = _legacy_library(tmp_path)
    index_path = _index_path(root)
    before = _sha256(index_path)
    target = root / "classified" / "datasheets" / "电压基准芯片" / "电压基准芯片-TPR3333-S3TR-C5291487.pdf"

    result = migrate_library(root, dry_run=True)

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["index_write_performed"] is False
    assert _sha256(index_path) == before
    assert not target.exists()


def test_migration_apply_creates_backup_canonical_views_and_keeps_legacy_sources(tmp_path):
    root, sources = _legacy_library(tmp_path)
    result = migrate_library(root, dry_run=False)

    assert result["applied"] is True
    assert result["index_write_performed"] is True
    assert Path(result["backup_path"]).is_file()
    assert result["source_files_deleted"] is False
    for path in sources.values():
        assert path.is_file()

    index = load_index(root)
    archive = index["parts"]["C5291487"]["archive"]
    assert archive["layout_version"] == 2
    assert archive["naming_policy"] == "category-mpn-c-code"
    assert archive["basename"] == "电压基准芯片-TPR3333-S3TR-C5291487"
    for kind in ("datasheet", "schlib", "pcblib", "step"):
        classified = [Path(p) for p in archive["assets"][kind]["classified"]]
        flat = [Path(p) for p in archive["assets"][kind]["flat"]]
        assert classified and all(path.is_file() for path in classified)
        assert flat and all(path.is_file() for path in flat)
    assert result["post_audit"]["ok"] is True


def test_migration_is_idempotent_after_apply(tmp_path):
    root, _ = _legacy_library(tmp_path)
    first = migrate_library(root, dry_run=False)
    assert first["applied"] is True

    second = plan_library_migration(root)
    assert second["ok"] is True
    assert second["needs_migration_count"] == 0
    assert second["copy_count"] == 0


def test_migration_conflicting_target_blocks_apply_without_index_write(tmp_path):
    root, _ = _legacy_library(tmp_path)
    target = root / "classified" / "datasheets" / "电压基准芯片" / "电压基准芯片-TPR3333-S3TR-C5291487.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-conflicting-content")
    before = _sha256(_index_path(root))

    plan = plan_library_migration(root)
    assert plan["safe_to_apply"] is False
    assert plan["blocker_count"] >= 1
    assert any(item["kind"] == "target_conflict" for item in plan["blockers"])

    result = migrate_library(root, dry_run=False)
    assert result["applied"] is False
    assert result["index_write_performed"] is False
    assert _sha256(_index_path(root)) == before


def test_new_archives_write_layout_v2_metadata(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sch = source / "part.SchLib"
    sch.write_bytes(b"sch")

    archived = archive_existing_files(_part(), tmp_path / "library", schlibs=[sch])
    assert archived["layout_version"] == 2
    assert archived["naming_policy"] == "category-mpn-c-code"
