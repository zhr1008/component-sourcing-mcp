from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from szlcsc_mcp import ad_scan
from szlcsc_mcp.ad_catalog import empty_catalog, load_ad_catalog, save_ad_catalog
from szlcsc_mcp.ad_properties import apply_property_patch
from szlcsc_mcp.ad_utils import normalize_text, selected_ids


def test_shared_ad_utils_normalize_and_filter_ids() -> None:
    assert normalize_text("  SOT-23   G  ") == "sot-23 g"
    assert selected_ids([" a ", "", "b", "a"]) == {"a", "b"}


def test_resilient_scan_avoids_second_catalog_write_without_recovery_or_ambiguity(tmp_path, monkeypatch) -> None:
    root = tmp_path / "catalog"

    def fake_scan(paths, *, catalog_root, recursive, hash_files, reuse_unchanged):
        catalog = empty_catalog()
        catalog["files"] = {}
        catalog["symbols"] = {}
        catalog["footprints"] = {}
        catalog["footprint_name_index"] = {}
        save_ad_catalog(catalog_root, catalog)
        return {"ok": True, "parse_errors": [], "symbol_count": 0, "footprint_count": 0}

    monkeypatch.setattr(ad_scan, "scan_altium_libraries", fake_scan)
    result = ad_scan.scan_altium_libraries_resilient([tmp_path], catalog_root=root)

    assert result["second_catalog_write_performed"] is False
    assert result["implementation_datafile_hints_added"] == 0
    assert result["ambiguous_footprint_name_count"] == 0
    assert load_ad_catalog(root)["revision"] == 1


def test_property_apply_does_not_claim_actions_when_schlib_save_fails(tmp_path, monkeypatch) -> None:
    catalog_root = tmp_path / "catalog"
    schlib = tmp_path / "legacy.SchLib"
    schlib.write_bytes(b"fixture")

    catalog = empty_catalog()
    catalog["symbols"] = {
        "symbol:1": {
            "asset_id": "symbol:1",
            "name": "TPR3333-S3TR",
            "library_path": str(schlib),
            "parameters": {},
            "enrichment": {
                "status": "resolved",
                "lcsc_code": "C5291487",
                "mpn": "TPR3333-S3TR",
                "brand": "3PEAK",
                "package": "SOT-23",
            },
        }
    }
    save_ad_catalog(catalog_root, catalog)
    revision_before = load_ad_catalog(catalog_root)["revision"]

    class FakeSymbol:
        name = "TPR3333-S3TR"
        parameters = []

        def add_parameter(self, name, value):
            parameter = SimpleNamespace(name=name, text=value)
            self.parameters.append(parameter)
            return parameter

    class FakeSchLib:
        def __init__(self, path):
            self.symbols = [FakeSymbol()]

        def get_symbol(self, name):
            return self.symbols[0] if name == "TPR3333-S3TR" else None

        def save(self, path):
            raise OSError("simulated write failure")

    monkeypatch.setitem(sys.modules, "altium_monkey", SimpleNamespace(AltiumSchLib=FakeSchLib))
    result = apply_property_patch(catalog_root, dry_run=False, backup=False)

    assert result["applied"] is False
    assert result["applied_action_count"] == 0
    assert result["library_write_performed"] is False
    assert result["ok"] is False
    assert result["failures"]
    after = load_ad_catalog(catalog_root)
    assert after["revision"] == revision_before
    assert after["symbols"]["symbol:1"]["parameters"] == {}


def test_automation_harness_never_uses_dangerous_permission_bypass() -> None:
    workflow = Path("examples/maintainer-private-validation.yml").read_text(encoding="utf-8")
    harness = Path("scripts/run-antigravity-validation.ps1").read_text(encoding="utf-8")
    combined = workflow + "\n" + harness

    assert "dangerously-skip-permissions" not in combined
    assert "--output-format json" in harness
    assert "git reset --hard" in workflow
    assert "git clean -fdx" in workflow
    assert "actions/workflows/release.yml/dispatches" in workflow
    assert "permissions" in harness
    assert "command(git (push|commit|reset|clean|checkout|switch|merge|rebase))" in harness
    assert "write_file(.git/)" in harness
    assert "Original Antigravity settings restored" in harness
