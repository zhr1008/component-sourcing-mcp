from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from szlcsc_mcp import ad_catalog
from szlcsc_mcp.ad_catalog import export_ad_catalog, load_ad_catalog
from szlcsc_mcp.ad_enrichment import build_verification_queue
from szlcsc_mcp.ad_scan import scan_altium_libraries_resilient


def test_actual_altium_raw_height_uses_internal_units():
    from szlcsc_mcp.ad_catalog import _pad_row

    row = _pad_row(SimpleNamespace(designator="1", width_mils=40.846, width=408460, height=209450, hole_size=0, x=10000, y=-20000))
    assert row["height_mils"] == 20.945
    assert row["width_mils"] == 40.846
    assert row["x_mils"] == 1 and row["y_mils"] == -2
    assert row["hole_size_mils"] == 0
    row = _pad_row(SimpleNamespace(height=209450, size_y_mils=21.0))
    assert row["height_mils"] == 21.0


class FakeParam:
    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text


class FakePin:
    def __init__(self, number: str, name: str):
        self.designator = number
        self.name = name
        self.electrical_type = SimpleNamespace(name="PASSIVE")
        self.x_mils = float(int(number) * 100) if number.isdigit() else 0.0
        self.y_mils = 0.0
        self.length_mils = 100.0


class FakeImplementation:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.model_type = "PCBLIB"
        self.is_current = True


class FakeSymbol:
    def __init__(self):
        self.name = "TPR3333-S3TR"
        self.description = "3.3V voltage reference"
        self.parameters = [
            FakeParam("LCSC Part #", "C5291487"),
            FakeParam("Manufacturer Part Number", "TPR3333-S3TR"),
            FakeParam("Manufacturer", "3PEAK"),
            FakeParam("Package", "SOT-23G-3"),
        ]
        self.pins = [FakePin("1", "GND"), FakePin("2", "VIN"), FakePin("3", "VOUT")]
        self.implementations = [FakeImplementation("SOT23_TEST")]


class FakeSchLib:
    fail = False

    def __init__(self, path):
        if self.fail:
            raise RuntimeError("legacy parser failure")
        self.path = Path(path)
        self.symbols = [FakeSymbol()]


class FakePad:
    def __init__(self, number: str, x: float, y: float):
        self.designator = number
        self.x_mils = x
        self.y_mils = y
        self.width_mils = 40.0
        self.height_mils = 60.0


class FakeFootprint:
    def __init__(self):
        self.name = "SOT23_TEST"
        self.description = "SOT-23 test footprint"
        self.parameters = []
        self.pads = [
            FakePad("1", -37.4, -37.4),
            FakePad("2", -37.4, 37.4),
            FakePad("3", 37.4, 0.0),
        ]
        self.component_bodies = []


class FakePcbLib:
    def __init__(self, path):
        self.path = Path(path)
        self.footprints = [FakeFootprint()]

    @classmethod
    def from_file(cls, path):
        return cls(path)


def _install_fake_monkey(monkeypatch):
    FakeSchLib.fail = False
    monkeypatch.setitem(
        sys.modules,
        "altium_monkey",
        SimpleNamespace(AltiumSchLib=FakeSchLib, AltiumPcbLib=FakePcbLib),
    )


def _library_files(tmp_path: Path) -> tuple[Path, Path]:
    sch = tmp_path / "Analog.SchLib"
    pcb = tmp_path / "Footprints.PcbLib"
    sch.write_bytes(b"fake-schlib-v1")
    pcb.write_bytes(b"fake-pcblib-v1")
    return sch, pcb


def test_scan_indexes_existing_schlib_pcblib_and_resolves_link(monkeypatch, tmp_path):
    _install_fake_monkey(monkeypatch)
    sch, pcb = _library_files(tmp_path)
    root = tmp_path / "catalog"

    result = scan_altium_libraries_resilient([tmp_path], catalog_root=root)

    assert result["ok"] is True
    assert result["symbol_count"] == 1
    assert result["footprint_count"] == 1
    catalog = load_ad_catalog(root)
    symbol = next(iter(catalog["symbols"].values()))
    assert symbol["library_path"] == str(sch.resolve())
    assert len(symbol["pins"]) == 3
    assert symbol["identity_hints"]["lcsc_code"] == "C5291487"
    assert symbol["identity_hints"]["mpn"] == "TPR3333-S3TR"
    link = symbol["resolved_footprint_links"][0]
    assert link["name"] == "SOT23_TEST"
    assert link["status"] == "resolved"
    footprint = catalog["footprints"][link["candidates"][0]]
    assert footprint["library_path"] == str(pcb.resolve())
    assert footprint["geometry"]["pad_count"] == 3


def test_second_scan_reuses_unchanged_files(monkeypatch, tmp_path):
    _install_fake_monkey(monkeypatch)
    _library_files(tmp_path)
    root = tmp_path / "catalog"
    first = scan_altium_libraries_resilient([tmp_path], catalog_root=root)
    second = scan_altium_libraries_resilient([tmp_path], catalog_root=root)
    assert first["parsed_files"] == 2
    assert second["parsed_files"] == 0
    assert second["reused_files"] == 2
    assert second["symbol_count"] == 1
    assert second["footprint_count"] == 1


def test_parse_failure_retains_last_good_catalog_record_as_stale(monkeypatch, tmp_path):
    _install_fake_monkey(monkeypatch)
    sch, _ = _library_files(tmp_path)
    root = tmp_path / "catalog"
    first = scan_altium_libraries_resilient([tmp_path], catalog_root=root)
    assert first["ok"] is True

    sch.write_bytes(b"changed-file-that-now-fails")
    FakeSchLib.fail = True
    second = scan_altium_libraries_resilient([tmp_path], catalog_root=root)

    assert second["ok"] is False
    assert second["stale_files_retained"] == 1
    catalog = load_ad_catalog(root)
    symbol = next(iter(catalog["symbols"].values()))
    assert symbol["catalog_stale"] is True
    assert catalog["files"][str(sch.resolve())]["scan_status"] == "stale_due_to_parse_error"


def test_export_records_component_and_library_locations(monkeypatch, tmp_path):
    _install_fake_monkey(monkeypatch)
    _library_files(tmp_path)
    root = tmp_path / "catalog"
    scan_altium_libraries_resilient([tmp_path], catalog_root=root)

    result = export_ad_catalog(root, output_dir=tmp_path / "exports", export_format="both")

    assert result["count"] == 1
    csv_path = Path(result["files"]["csv"])
    json_path = Path(result["files"]["json"])
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "TPR3333-S3TR" in text
    assert "Analog.SchLib" in text
    assert "Footprints.PcbLib" in text
    assert json_path.is_file()


@pytest.mark.parametrize("change", ["pins", "binding", "pads", "geometry", "resolution"])
def test_scan_invalidates_verification_when_cad_evidence_changes(monkeypatch, tmp_path, change):
    sch, pcb = _library_files(tmp_path)
    root = tmp_path / "catalog"
    symbol = {"name": "U1", "pins": [{"number": "1"}], "footprint_links": [{"name": "QFN", "library": None}]}
    footprint = {"name": "QFN", "pads": [{"number": "1"}], "geometry": {"pad_count": 1}}
    monkeypatch.setattr(ad_catalog, "_parse_schlib", lambda path: [symbol])
    monkeypatch.setattr(ad_catalog, "_parse_pcblib", lambda path: [footprint])
    ad_catalog.scan_altium_libraries([sch, pcb], catalog_root=root)
    catalog = load_ad_catalog(root)
    entry = next(iter(catalog["symbols"].values()))
    entry["enrichment"] = {"status": "resolved"}
    entry["verification"] = {"status": "verified", "authority": "datasheet", "datasheet_pages": [4]}
    ad_catalog.save_ad_catalog(root, catalog)

    # A reparse of equivalent CAD data must preserve the verification.
    ad_catalog.scan_altium_libraries([sch, pcb], catalog_root=root, reuse_unchanged=False)
    assert next(iter(load_ad_catalog(root)["symbols"].values()))["verification"]["status"] == "verified"
    assert not build_verification_queue(root)["queue"]

    if change == "pins":
        symbol["pins"] = [{"number": "2"}]
        sch.write_bytes(b"changed-pins")
    elif change == "binding":
        symbol["footprint_links"] = [{"name": "OTHER", "library": None}]
        sch.write_bytes(b"changed-binding")
    elif change == "pads":
        footprint["pads"] = [{"number": "2"}]
        pcb.write_bytes(b"changed-pads")
    elif change == "geometry":
        footprint["geometry"] = {"pad_count": 1, "center_span_x_mm": 2}
        pcb.write_bytes(b"changed-geometry")
    else:
        pcb.unlink()
    ad_catalog.scan_altium_libraries([sch, pcb], catalog_root=root)
    entry = next(iter(load_ad_catalog(root)["symbols"].values()))
    assert entry["verification"]["status"] != "verified"
    assert entry["verification"]["datasheet_pages"] == [4]
    assert len(build_verification_queue(root)["queue"]) == 1


@pytest.mark.parametrize("failed_kind", ["symbol", "footprint"])
def test_retained_stale_cad_invalidates_verification(monkeypatch, tmp_path, failed_kind):
    sch, pcb = _library_files(tmp_path)
    root = tmp_path / "catalog"
    symbol = {"name": "U1", "pins": [{"number": "1"}], "footprint_links": [{"name": "QFN"}]}
    footprint = {"name": "QFN", "pads": [{"number": "1"}], "geometry": {"pad_count": 1}}
    monkeypatch.setattr(ad_catalog, "_parse_schlib", lambda path: [symbol])
    monkeypatch.setattr(ad_catalog, "_parse_pcblib", lambda path: [footprint])
    scan_altium_libraries_resilient([sch, pcb], catalog_root=root)
    catalog = load_ad_catalog(root)
    entry = next(iter(catalog["symbols"].values()))
    entry["enrichment"] = {"status": "resolved"}
    entry["verification"] = {"status": "verified", "datasheet_pages": [4]}
    ad_catalog.save_ad_catalog(root, catalog)

    def fail(path):
        raise ValueError("parser failed")

    changed = sch if failed_kind == "symbol" else pcb
    changed.write_bytes(b"changed-but-unparseable")
    monkeypatch.setattr(ad_catalog, "_parse_schlib" if failed_kind == "symbol" else "_parse_pcblib", fail)
    assert scan_altium_libraries_resilient([sch, pcb], catalog_root=root)["ok"] is False
    entry = next(iter(load_ad_catalog(root)["symbols"].values()))
    assert entry["verification"]["status"] == "stale_cad_changed"
    assert entry["verification"]["datasheet_pages"] == [4]
    assert len(build_verification_queue(root)["queue"]) == 1


def test_equivalent_rescan_preserves_verification_with_datafile_hint(monkeypatch, tmp_path):
    class HintedImplementation(FakeImplementation):
        datafile_entity = "Footprints"
        datafile_kind = "PCBLib"

    class HintedSymbol(FakeSymbol):
        def __init__(self):
            super().__init__()
            self.implementations = [HintedImplementation("SOT23_TEST")]

    class HintedSchLib(FakeSchLib):
        def __init__(self, path):
            super().__init__(path)
            self.symbols = [HintedSymbol()]

    monkeypatch.setitem(sys.modules, "altium_monkey", SimpleNamespace(AltiumSchLib=HintedSchLib, AltiumPcbLib=FakePcbLib))
    sch, pcb = _library_files(tmp_path)
    other = tmp_path / "Other.PcbLib"
    other.write_bytes(b"other")
    root = tmp_path / "catalog"
    scan_altium_libraries_resilient([sch, pcb, other], catalog_root=root)
    catalog = load_ad_catalog(root)
    entry = next(iter(catalog["symbols"].values()))
    entry["verification"] = {"status": "verified", "datasheet_pages": [4]}
    ad_catalog.save_ad_catalog(root, catalog)
    scan_altium_libraries_resilient([sch, pcb, other], catalog_root=root, reuse_unchanged=False)
    entry = next(iter(load_ad_catalog(root)["symbols"].values()))
    assert entry["verification"]["status"] == "verified"
    HintedImplementation.datafile_entity = "Other"
    scan_altium_libraries_resilient([sch, pcb, other], catalog_root=root, reuse_unchanged=False)
    entry = next(iter(load_ad_catalog(root)["symbols"].values()))
    assert entry["resolved_footprint_links"][0]["library"] == "Other"
    assert entry["verification"]["status"] == "stale_cad_changed"


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("resilient", [False, True])
def test_scan_respects_implementation_datafile_entity(monkeypatch, tmp_path, duplicate, resilient):
    class HintedImplementation(FakeImplementation):
        datafile_entity = "AnotherLibrary"

    class HintedSymbol(FakeSymbol):
        def __init__(self):
            super().__init__()
            self.implementations = [HintedImplementation("SOT23_TEST")]

    class HintedSchLib(FakeSchLib):
        def __init__(self, path):
            super().__init__(path)
            self.symbols = [HintedSymbol()]

    monkeypatch.setitem(sys.modules, "altium_monkey", SimpleNamespace(AltiumSchLib=HintedSchLib, AltiumPcbLib=FakePcbLib))
    sch, pcb = _library_files(tmp_path)
    root = tmp_path / "catalog"
    paths = [sch, pcb]
    if duplicate:
        other = tmp_path / "Other.PcbLib"
        other.write_bytes(b"other")
        paths.append(other)
    scan = scan_altium_libraries_resilient if resilient else ad_catalog.scan_altium_libraries
    for _ in range(2):
        scan(paths, catalog_root=root)
        link = next(iter(load_ad_catalog(root)["symbols"].values()))["resolved_footprint_links"][0]
        assert link["library"] == "AnotherLibrary"
        assert link["status"] == "missing" and not link["candidates"]


@pytest.mark.parametrize("last_good", [False, True])
def test_scan_retries_failed_files_until_recovery(monkeypatch, tmp_path, last_good):
    sch = tmp_path / "test.SchLib"
    sch.write_text("initial", encoding="utf-8")
    root = tmp_path / "catalog"
    calls = []
    fail = False

    def parse(path):
        calls.append(path)
        if fail:
            raise ValueError("parser failed")
        return [{"name": "U1", "pins": [], "footprint_links": []}]

    monkeypatch.setattr(ad_catalog, "_parse_schlib", parse)
    if last_good:
        assert scan_altium_libraries_resilient([sch], catalog_root=root)["ok"]
        sch.write_text("changed", encoding="utf-8")
    fail = True
    for _ in range(2):
        result = scan_altium_libraries_resilient([sch], catalog_root=root)
        assert result["ok"] is False
        assert result["reused_files"] == 0
        catalog = load_ad_catalog(root)
        assert catalog["files"][str(sch.resolve())]["scan_status"] in {"error", "stale_due_to_parse_error"}
        assert len(catalog["symbols"]) == int(last_good)
        if last_good:
            assert next(iter(catalog["symbols"].values()))["catalog_stale"] is True
    fail = False
    result = scan_altium_libraries_resilient([sch], catalog_root=root)
    assert result["ok"] is True and result["parsed_files"] == 1
    assert len(calls) == 3 + int(last_good)
    assert "catalog_stale" not in next(iter(load_ad_catalog(root)["symbols"].values()))
    assert scan_altium_libraries_resilient([sch], catalog_root=root)["reused_files"] == 1


@pytest.mark.parametrize(
    ("hint", "paths", "expected"),
    [
        ("right.PcbLib", ["/lib/wrong.PcbLib"], []),
        ("right.PcbLib", ["/lib/wrong.PcbLib", "/lib/other.PcbLib"], []),
        ("right", ["/lib/right.PcbLib", "/lib/right-extra.PcbLib"], ["0"]),
        (r"C:\\lib\\right.PcbLib", ["C:/lib/right.PcbLib", "C:/other/right.PcbLib"], ["0"]),
        ("lib/right.PcbLib", ["/project/lib/right.PcbLib", "/project/other/right.PcbLib"], ["0"]),
        (None, ["/lib/wrong.PcbLib"], ["0"]),
        (None, ["/lib/a.PcbLib", "/lib/b.PcbLib"], ["0", "1"]),
    ],
)
def test_footprint_library_hint_restricts_candidates(hint, paths, expected):
    catalog = {
        "symbols": {"s": {"footprint_links": [{"name": "QFN", "library": hint}]}},
        "footprints": {str(i): {"name": "QFN", "library_path": path} for i, path in enumerate(paths)},
    }
    ad_catalog._resolve_footprint_links(catalog)
    link = catalog["symbols"]["s"]["resolved_footprint_links"][0]
    assert link["candidates"] == expected
    assert link["status"] == ("resolved" if len(expected) == 1 else "ambiguous" if expected else "missing")
