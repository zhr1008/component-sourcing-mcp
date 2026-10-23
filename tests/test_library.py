from pathlib import Path

from szlcsc_mcp.library import (
    cad_key,
    load_index,
    stage_for_merge,
    summarize_index,
    upsert_part,
)
from szlcsc_mcp.models import Part


def make_part(code: str, mpn: str = "ABC123", package: str = "SOT-23", category: str = "电源管理") -> Part:
    return Part(
        lcsc_code=code,
        mpn=mpn,
        package=package,
        category=category,
        brand="TEST",
        stock=10,
        small_batch_price_cny=1.2,
    )


def test_cad_key_uses_mpn_and_package():
    assert cad_key(make_part("C1")) == "abc123|sot-23"
    assert cad_key(make_part("C2", package="SOP-8")) != cad_key(make_part("C1"))


def test_upsert_deduplicates_same_mpn_package(tmp_path):
    review = {
        "case_dir": str(tmp_path / "altium" / "C1"),
        "quality": {"has_schlib": True, "has_pcblib": True, "concerns": []},
        "schlibs": [],
        "pcblibs": [],
        "step_files": [],
    }
    one = upsert_part(tmp_path / "lib", make_part("C1"), review, dedupe_cad=True)
    two = upsert_part(tmp_path / "lib", make_part("C2"), None, dedupe_cad=True)
    assert one["entry"]["cad_owner"] == "C1"
    assert two["entry"]["cad_owner"] == "C1"
    assert two["entry"]["cad_shared"] is True
    summary = summarize_index(load_index(tmp_path / "lib"))
    assert summary["part_count"] == 2
    assert summary["cad_owner_count"] == 1
    assert summary["cad_shared_count"] == 1


def test_same_mpn_different_package_not_cad_deduped(tmp_path):
    upsert_part(tmp_path / "lib", make_part("C1", package="SOT-23"), None)
    two = upsert_part(tmp_path / "lib", make_part("C2", package="SOP-8"), None)
    assert two["entry"]["cad_owner"] == "C2"
    assert two["entry"]["cad_shared"] is False


def test_metadata_update_flags_identity_change_but_preserves_altium(tmp_path):
    case = tmp_path / "altium" / "C1"
    case.mkdir(parents=True)
    (case / "C1.SchLib").write_bytes(b"s")
    review = {
        "case_dir": str(case),
        "quality": {"has_schlib": True, "has_pcblib": False, "concerns": []},
        "schlibs": [str(case / "C1.SchLib")],
        "pcblibs": [],
        "step_files": [],
    }
    upsert_part(tmp_path / "lib", make_part("C1", package="SOT-23"), review)
    changed = upsert_part(tmp_path / "lib", make_part("C1", package="SOT-223"), None)
    assert changed["entry"]["cad_review_required"] is True
    assert changed["entry"]["altium"]["has_schlib"] is True


def test_stage_for_merge_uses_cad_owner_once(tmp_path):
    case = tmp_path / "altium" / "C1"
    case.mkdir(parents=True)
    (case / "C1.SchLib").write_bytes(b"s")
    (case / "C1.PcbLib").write_bytes(b"p")
    review = {
        "case_dir": str(case),
        "quality": {"has_schlib": True, "has_pcblib": True, "concerns": []},
        "schlibs": [str(case / "C1.SchLib")],
        "pcblibs": [str(case / "C1.PcbLib")],
        "step_files": [],
    }
    upsert_part(tmp_path / "lib", make_part("C1"), review)
    upsert_part(tmp_path / "lib", make_part("C2"), None)
    staged = stage_for_merge(load_index(tmp_path / "lib"), tmp_path / "lib")
    group = next(iter(staged["groups"].values()))
    assert group["owners"] == ["C1"]
    assert len(list(Path(group["schlib_dir"]).glob("*.SchLib"))) == 1
    assert len(list(Path(group["pcblib_dir"]).glob("*.PcbLib"))) == 1


def test_category_slug_preserves_chinese(tmp_path):
    result = upsert_part(tmp_path / "lib", make_part("C8", category="电源管理 / DC-DC"), None)
    slug = result["entry"]["category_slug"]
    assert "电源管理" in slug
    assert "DC-DC" in slug


def test_stale_cad_owner_is_not_reused(tmp_path):
    stale_review = {
        "case_dir": str(tmp_path / "missing" / "C1"),
        "quality": {"has_schlib": True, "has_pcblib": True, "concerns": []},
        "schlibs": [], "pcblibs": [], "step_files": [],
    }
    upsert_part(tmp_path / "lib", make_part("C1"), stale_review)
    fresh_case = tmp_path / "altium" / "C2"
    fresh_case.mkdir(parents=True)
    (fresh_case / "C2.SchLib").write_bytes(b"s")
    fresh_review = {
        "case_dir": str(fresh_case),
        "quality": {"has_schlib": True, "has_pcblib": False, "concerns": []},
        "schlibs": [str(fresh_case / "C2.SchLib")], "pcblibs": [], "step_files": [],
    }
    result = upsert_part(tmp_path / "lib", make_part("C2"), fresh_review)
    assert result["entry"]["cad_owner"] == "C2"


def test_reconcile_group_repoints_stale_member_to_real_owner(tmp_path):
    upsert_part(tmp_path / "lib", make_part("C1"), None)
    case = tmp_path / "altium" / "C2"
    case.mkdir(parents=True)
    (case / "C2.SchLib").write_bytes(b"s")
    review = {
        "case_dir": str(case),
        "quality": {"has_schlib": True, "has_pcblib": False, "concerns": []},
        "schlibs": [str(case / "C2.SchLib")], "pcblibs": [], "step_files": [],
    }
    upsert_part(tmp_path / "lib", make_part("C2"), review)
    index = load_index(tmp_path / "lib")
    assert index["parts"]["C1"]["cad_owner"] == "C2"
    assert index["parts"]["C1"]["cad_shared"] is True
    assert index["parts"]["C1"]["altium"]["has_schlib"] is True


def test_library_revision_increments_on_mutation(tmp_path):
    root = tmp_path / "lib"
    assert load_index(root)["revision"] == 0
    upsert_part(root, make_part("C1"), None)
    first = load_index(root)["revision"]
    upsert_part(root, make_part("C1"), None)
    second = load_index(root)["revision"]
    assert first >= 1
    assert second == first + 1


def test_stage_for_merge_prefers_archived_library_after_source_case_deleted(tmp_path):
    part = make_part("C77", mpn="TPR3333-S3TR", category="电压基准芯片")
    archive_sch = tmp_path / "lib" / "classified" / "schlib" / "电压基准芯片" / "电压基准芯片-TPR3333-S3TR-C77.SchLib"
    archive_pcb = tmp_path / "lib" / "classified" / "pcblib" / "电压基准芯片" / "电压基准芯片-TPR3333-S3TR-C77.PcbLib"
    archive_sch.parent.mkdir(parents=True)
    archive_pcb.parent.mkdir(parents=True)
    archive_sch.write_bytes(b"s")
    archive_pcb.write_bytes(b"p")
    archive = {
        "assets": {
            "schlib": {"classified": [str(archive_sch)], "flat": []},
            "pcblib": {"classified": [str(archive_pcb)], "flat": []},
        }
    }
    review = {
        "case_dir": str(tmp_path / "deleted-source" / "C77"),
        "quality": {"has_schlib": True, "has_pcblib": True, "concerns": []},
        "schlibs": [],
        "pcblibs": [],
        "step_files": [],
    }
    upsert_part(tmp_path / "lib", part, review, archive=archive)
    staged = stage_for_merge(load_index(tmp_path / "lib"), tmp_path / "lib")
    group = next(iter(staged["groups"].values()))
    assert len(list(Path(group["schlib_dir"]).glob("*.SchLib"))) == 1
    assert len(list(Path(group["pcblib_dir"]).glob("*.PcbLib"))) == 1


def test_metadata_sync_style_upsert_preserves_archive(tmp_path):
    part = make_part("C88")
    archive = {"basename": "电源管理-ABC123-C88", "assets": {"datasheet": {"classified": ["x.pdf"], "flat": []}}}
    upsert_part(tmp_path / "lib", part, None, archive=archive)
    updated = upsert_part(tmp_path / "lib", part, None)
    assert updated["entry"]["archive"]["basename"] == "电源管理-ABC123-C88"


def test_archived_cad_review_survives_missing_temp_case(tmp_path):
    from szlcsc_mcp.library import archived_cad_review

    archive_sch = tmp_path / "classified" / "X.SchLib"
    archive_pcb = tmp_path / "classified" / "X.PcbLib"
    archive_sch.parent.mkdir(parents=True)
    archive_sch.write_bytes(b"s")
    archive_pcb.write_bytes(b"p")
    entry = {
        "lcsc_code": "C99",
        "archive": {
            "assets": {
                "schlib": {"classified": [str(archive_sch)]},
                "pcblib": {"classified": [str(archive_pcb)]},
                "step": {"classified": []},
            }
        },
        "altium": {"case_dir": str(tmp_path / "gone"), "concerns": ["source warning"]},
    }
    review = archived_cad_review(entry)
    assert review is not None
    assert review["source"] == "classified_archive"
    assert review["quality"]["has_schlib"] is True
    assert review["quality"]["has_pcblib"] is True
