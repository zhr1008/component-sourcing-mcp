from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from szlcsc_mcp.ad_catalog import load_ad_catalog
from szlcsc_mcp.ad_scan import scan_altium_libraries_resilient


class Impl:
    model_name = "COMMON_SOT23"
    model_type = "PCBLIB"
    datafile_entity = "Footprints_B"
    datafile_kind = "PCBLib"
    is_current = True


class Symbol:
    name = "TEST123"
    description = ""
    parameters = []
    pins = []
    implementations = [Impl()]


class SchLib:
    def __init__(self, path):
        self.symbols = [Symbol()]


class Footprint:
    name = "COMMON_SOT23"
    description = ""
    parameters = []
    pads = []
    component_bodies = []


class PcbLib:
    def __init__(self, path):
        self.footprints = [Footprint()]

    @classmethod
    def from_file(cls, path):
        return cls(path)


def test_datafile_entity_disambiguates_duplicate_footprint_names(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "altium_monkey", SimpleNamespace(AltiumSchLib=SchLib, AltiumPcbLib=PcbLib))
    (tmp_path / "Symbols.SchLib").write_bytes(b"sch")
    (tmp_path / "Footprints_A.PcbLib").write_bytes(b"a")
    (tmp_path / "Footprints_B.PcbLib").write_bytes(b"b")
    root = tmp_path / "catalog"

    result = scan_altium_libraries_resilient([tmp_path], catalog_root=root)
    assert result["implementation_datafile_hints_added"] >= 1

    catalog = load_ad_catalog(root)
    symbol = next(iter(catalog["symbols"].values()))
    link = symbol["resolved_footprint_links"][0]
    assert link["status"] == "resolved"
    assert link["library"] == "Footprints_B"
    footprint = catalog["footprints"][link["candidates"][0]]
    assert Path(footprint["library_path"]).stem == "Footprints_B"
