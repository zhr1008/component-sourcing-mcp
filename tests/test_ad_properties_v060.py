from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from szlcsc_mcp.ad_catalog import empty_catalog, load_ad_catalog, save_ad_catalog
from szlcsc_mcp.ad_properties import apply_property_patch, build_property_patch_plan


class FakeParameter:
    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text


class FakeSymbol:
    def __init__(self, name: str, parameters: list[FakeParameter]):
        self.name = name
        self.parameters = parameters

    def add_parameter(self, name: str, text: str):
        param = FakeParameter(name, text)
        self.parameters.append(param)
        return param


class FakeSchLib:
    registry: dict[str, list[FakeSymbol]] = {}

    def __init__(self, path):
        self.path = Path(path).resolve()
        source = self.registry.setdefault(str(self.path), [FakeSymbol("TPR3333-S3TR", [])])
        # Keep the same in-memory objects so tests can inspect applied values.
        self.symbols = source

    def get_symbol(self, name: str):
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol
        return None

    def save(self, path):
        Path(path).write_bytes(b"fake-schlib-saved")


def _seed_catalog(root: Path, schlib: Path, *, existing_parameters: dict[str, str] | None = None):
    data = empty_catalog()
    data["symbols"]["symbol:1"] = {
        "asset_id": "symbol:1",
        "name": "TPR3333-S3TR",
        "library_path": str(schlib.resolve()),
        "parameters": dict(existing_parameters or {}),
        "pins": [{"number": "1"}, {"number": "2"}, {"number": "3"}],
        "footprint_links": [],
        "resolved_footprint_links": [],
        "identity_hints": {},
        "issues": [],
        "enrichment": {
            "status": "resolved",
            "lcsc_code": "C5291487",
            "mpn": "TPR3333-S3TR",
            "brand": "3PEAK",
            "category": "电压基准芯片",
            "package": "SOT-23G-3",
            "name": "3.3V voltage reference",
            "datasheet_url": "https://atta.szlcsc.com/example.pdf",
            "product_url": "https://item.szlcsc.com/example.html",
            "small_batch_price_cny": 1.23,
            "stock": 100,
            "min_order_qty": 1,
            "packaging": "编带",
            "parameters": {"输出电压": "3.3V", "温漂": "10ppm/℃"},
        },
    }
    save_ad_catalog(root, data)


def test_property_plan_adds_missing_fields_without_touching_graphics(tmp_path):
    schlib = tmp_path / "Analog.SchLib"
    schlib.write_bytes(b"original")
    root = tmp_path / "catalog"
    _seed_catalog(root, schlib)

    plan = build_property_patch_plan(root)
    assert plan["add_count"] > 0
    assert plan["update_count"] == 0
    assert plan["conflict_count"] == 0
    assert plan["library_write_performed"] is False
    assert plan["symbol_graphics_modified"] is False
    assert plan["footprint_links_modified"] is False


def test_existing_different_identity_field_is_conflict_by_default(tmp_path):
    schlib = tmp_path / "Analog.SchLib"
    schlib.write_bytes(b"original")
    root = tmp_path / "catalog"
    _seed_catalog(root, schlib, existing_parameters={"Manufacturer Part Number": "WRONG-MPN"})

    plan = build_property_patch_plan(root, overwrite_existing=False)
    assert plan["conflict_count"] == 1
    conflict = plan["conflicts"][0]
    assert conflict["parameter"] == "Manufacturer Part Number"
    assert conflict["old"] == "WRONG-MPN"
    assert conflict["new"] == "TPR3333-S3TR"


def test_explicit_overwrite_converts_conflict_to_update(tmp_path):
    schlib = tmp_path / "Analog.SchLib"
    schlib.write_bytes(b"original")
    root = tmp_path / "catalog"
    _seed_catalog(root, schlib, existing_parameters={"Manufacturer Part Number": "WRONG-MPN"})
    plan = build_property_patch_plan(root, overwrite_existing=True)
    assert plan["conflict_count"] == 0
    assert plan["update_count"] == 1


def test_property_apply_dry_run_does_not_modify_schlib(tmp_path):
    schlib = tmp_path / "Analog.SchLib"
    schlib.write_bytes(b"original")
    root = tmp_path / "catalog"
    _seed_catalog(root, schlib)
    before = schlib.read_bytes()

    result = apply_property_patch(root, dry_run=True)

    assert result["applied"] is False
    assert result["library_write_performed"] is False
    assert result["backup_paths"] == []
    assert schlib.read_bytes() == before


def test_real_property_apply_creates_backup_and_updates_parameters_only(monkeypatch, tmp_path):
    schlib = tmp_path / "Analog.SchLib"
    schlib.write_bytes(b"original")
    root = tmp_path / "catalog"
    _seed_catalog(root, schlib)

    FakeSchLib.registry = {str(schlib.resolve()): [FakeSymbol("TPR3333-S3TR", [])]}
    monkeypatch.setitem(sys.modules, "altium_monkey", SimpleNamespace(AltiumSchLib=FakeSchLib))

    result = apply_property_patch(root, dry_run=False, backup=True)

    assert result["failures"] == []
    assert result["applied"] is True
    assert result["library_write_performed"] is True
    assert result["ok"] is True
    assert len(result["backup_paths"]) == 1
    backup = Path(result["backup_paths"][0])
    assert backup.is_file()
    assert backup.read_bytes() == b"original"
    symbol = FakeSchLib.registry[str(schlib.resolve())][0]
    values = {param.name: param.text for param in symbol.parameters}
    assert values["Manufacturer Part Number"] == "TPR3333-S3TR"
    assert values["LCSC Part #"] == "C5291487"
    assert values["Datasheet"] == "https://atta.szlcsc.com/example.pdf"
    catalog = load_ad_catalog(root)
    assert catalog["symbols"]["symbol:1"]["property_patch"]["applied_count"] > 0
    assert result["symbol_graphics_modified"] is False
    assert result["footprint_links_modified"] is False
