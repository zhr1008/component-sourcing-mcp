from __future__ import annotations

from pathlib import Path

import pytest

from szlcsc_mcp.ad_catalog import empty_catalog, load_ad_catalog, save_ad_catalog
from szlcsc_mcp.ad_enrichment import enrich_ad_catalog, record_datasheet_verification, resolve_symbol_part
from szlcsc_mcp.library_base import LibraryError
from szlcsc_mcp.models import Part, PriceTier


class FakeClient:
    def __init__(self, parts: list[Part]):
        self.parts = parts

    def get_part(self, identifier: str) -> Part:
        ident = identifier.casefold()
        exact = [p for p in self.parts if ident in {(p.lcsc_code or "").casefold(), (p.mpn or "").casefold()}]
        if len(exact) != 1:
            raise RuntimeError(f"unambiguous part not found: {identifier}")
        return exact[0]

    def search(self, query: str, *, max_results: int = 12, enrich: bool = True):
        q = query.casefold()
        # Include one broad false-positive to prove exact MPN is required.
        broad = Part(lcsc_code="C999999", mpn="OTHER123", brand="OTHER", package="SOT-23")
        return ([p for p in self.parts if q in (p.mpn or "").casefold()] + [broad])[:max_results]


def _part(code="C5291487", mpn="TPR3333-S3TR", package="SOT-23G-3", stock=100) -> Part:
    return Part(
        lcsc_code=code,
        mpn=mpn,
        name="3.3V voltage reference",
        brand="3PEAK",
        package=package,
        category="电压基准芯片",
        stock=stock,
        total_stock=stock,
        small_batch_price_cny=1.23,
        min_order_qty=1,
        packaging="编带",
        datasheet_url="https://atta.szlcsc.com/example.pdf",
        product_url="https://item.szlcsc.com/example.html",
        parameters={"输出电压": "3.3V", "温漂": "10ppm/℃"},
        price_tiers=[PriceTier(min_qty=1, unit_price_cny=1.23)],
    )


def _symbol(*, c_code=None, mpn=None, package="SOT-23G-3", name="REF_3V3") -> dict:
    return {
        "asset_id": "symbol:1",
        "name": name,
        "library_path": "D:/Lib/Analog.SchLib",
        "identity_hints": {"lcsc_code": c_code, "mpn": mpn, "package": package, "sources": {}},
        "pins": [
            {"number": "1", "name": "GND"},
            {"number": "2", "name": "VIN"},
            {"number": "3", "name": "VOUT"},
        ],
        "resolved_footprint_links": [{"name": "SOT23_TEST", "status": "resolved", "candidates": ["footprint:1"]}],
        "parameters": {},
        "issues": [],
    }


def _catalog(root: Path, symbol: dict) -> None:
    data = empty_catalog()
    data["symbols"][symbol["asset_id"]] = symbol
    data["footprints"]["footprint:1"] = {
        "asset_id": "footprint:1",
        "name": "SOT23_TEST",
        "library_path": "D:/Lib/Footprints.PcbLib",
        "pads": [{"number": "1"}, {"number": "2"}, {"number": "3"}],
        "geometry": {"pad_count": 3, "minimum_pad_center_distance_mm": 0.95},
    }
    save_ad_catalog(root, data)


def test_exact_c_code_resolves_and_preserves_full_parameters():
    client = FakeClient([_part()])
    result = resolve_symbol_part(client, _symbol(c_code="C5291487"))
    assert result["status"] == "resolved"
    assert result["method"] == "exact_c_code"
    assert result["mpn"] == "TPR3333-S3TR"
    assert result["parameters"]["输出电压"] == "3.3V"
    assert result["small_batch_price_cny"] == 1.23


def test_exact_mpn_resolves_but_broad_false_positive_never_auto_binds():
    client = FakeClient([_part()])
    result = resolve_symbol_part(client, _symbol(mpn="TPR3333-S3TR"))
    assert result["status"] == "resolved"
    assert result["lcsc_code"] == "C5291487"

    unresolved = resolve_symbol_part(client, _symbol(mpn="NOT-AN-EXACT-PART"))
    assert unresolved["status"] == "unresolved"


def test_duplicate_exact_mpn_without_unique_package_stays_ambiguous():
    client = FakeClient([
        _part(code="C100001", package="SOT-23"),
        _part(code="C100002", package="SOT-23"),
    ])
    result = resolve_symbol_part(client, _symbol(mpn="TPR3333-S3TR", package="SOT-23"))
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 2


def test_generic_symbol_name_is_not_fuzzy_auto_bound():
    client = FakeClient([_part()])
    result = resolve_symbol_part(client, _symbol(name="Voltage Reference 3V3", mpn=None, c_code=None))
    assert result["status"] == "unresolved"
    assert result["method"] == "insufficient_identity"


def test_enrichment_price_stock_refresh_keeps_verified_status_when_identity_unchanged(tmp_path):
    root = tmp_path / "catalog"
    symbol = _symbol(c_code="C5291487")
    symbol["enrichment"] = {"status": "resolved", "lcsc_code": "C5291487", "mpn": "TPR3333-S3TR", "package": "SOT-23G-3", "stock": 100}
    symbol["verification"] = {"status": "verified", "authority": "datasheet"}
    _catalog(root, symbol)

    result = enrich_ad_catalog(FakeClient([_part(stock=250)]), root, force=True)
    assert result["resolved"] == 1
    updated = load_ad_catalog(root)["symbols"]["symbol:1"]
    assert updated["enrichment"]["stock"] == 250
    assert updated["verification"]["status"] == "verified"


@pytest.mark.parametrize("pass_value", [None, "true", 1])
def test_incomplete_mechanical_check_cannot_verify_a_part(tmp_path, pass_value):
    root = tmp_path / "catalog"
    symbol = _symbol(c_code="C5291487")
    symbol["enrichment"] = {"status": "resolved", "lcsc_code": "C5291487", "mpn": "TPR3333-S3TR"}
    _catalog(root, symbol)
    result = record_datasheet_verification(root, asset_id="symbol:1", evidence={
        "source": "datasheet", "pages": [4], "mpn": "TPR3333-S3TR",
        "pins": symbol["pins"], "terminal_numbers": ["1", "2", "3"],
        "mechanical_checks": [{"name": "pitch", "expected": "0.95mm", "actual": "0.95mm", "pass": pass_value, "page": 4}],
    })
    assert result["status"] != "verified"
    assert any(x["kind"] == "mechanical_evidence_incomplete" for x in result["issues"])


def test_verification_rejects_non_datasheet_source_and_missing_pages(tmp_path):
    root = tmp_path / "catalog"
    symbol = _symbol(c_code="C5291487")
    symbol["enrichment"] = {"status": "resolved", "lcsc_code": "C5291487", "mpn": "TPR3333-S3TR", "package": "SOT-23G-3"}
    _catalog(root, symbol)

    with pytest.raises(LibraryError):
        record_datasheet_verification(root, asset_id="symbol:1", evidence={"source": "easyeda", "pages": [1]})
    with pytest.raises(LibraryError):
        record_datasheet_verification(root, asset_id="symbol:1", evidence={"source": "datasheet", "pages": []})


def test_pin_number_mismatch_is_failed(tmp_path):
    root = tmp_path / "catalog"
    symbol = _symbol(c_code="C5291487")
    symbol["enrichment"] = {"status": "resolved", "lcsc_code": "C5291487", "mpn": "TPR3333-S3TR", "package": "SOT-23G-3"}
    _catalog(root, symbol)

    result = record_datasheet_verification(root, asset_id="symbol:1", evidence={
        "source": "datasheet",
        "pages": [3, 18],
        "mpn": "TPR3333-S3TR",
        "package": "SOT-23G-3",
        "pins": [{"number": "1", "name": "GND"}, {"number": "2", "name": "VIN"}, {"number": "4", "name": "VOUT"}],
        "terminal_numbers": ["1", "2", "4"],
        "mechanical_checks": [{"name": "pitch", "expected": "0.95mm", "actual": "0.95mm", "pass": True, "page": 18}],
    })
    assert result["status"] == "failed"
    assert any(issue["kind"] == "symbol_pin_number_mismatch" for issue in result["issues"])


def test_correct_pin_pad_numbers_without_mechanical_evidence_is_not_verified(tmp_path):
    root = tmp_path / "catalog"
    symbol = _symbol(c_code="C5291487")
    symbol["enrichment"] = {"status": "resolved", "lcsc_code": "C5291487", "mpn": "TPR3333-S3TR", "package": "SOT-23G-3"}
    _catalog(root, symbol)

    result = record_datasheet_verification(root, asset_id="symbol:1", evidence={
        "source": "datasheet",
        "pages": [3],
        "mpn": "TPR3333-S3TR",
        "package": "SOT-23G-3",
        "pins": [{"number": "1", "name": "GND"}, {"number": "2", "name": "VIN"}, {"number": "3", "name": "VOUT"}],
        "terminal_numbers": ["1", "2", "3"],
        "mechanical_checks": [],
    })
    assert result["status"] == "mechanical_review_required"


def test_datasheet_pin_pad_and_mechanical_evidence_can_reach_verified(tmp_path):
    root = tmp_path / "catalog"
    symbol = _symbol(c_code="C5291487")
    symbol["enrichment"] = {"status": "resolved", "lcsc_code": "C5291487", "mpn": "TPR3333-S3TR", "package": "SOT-23G-3"}
    _catalog(root, symbol)

    result = record_datasheet_verification(root, asset_id="symbol:1", evidence={
        "source": "datasheet",
        "pages": [3, 18, 19],
        "mpn": "TPR3333-S3TR",
        "package": "SOT-23G-3",
        "pins": [{"number": "1", "name": "GND"}, {"number": "2", "name": "VIN"}, {"number": "3", "name": "VOUT"}],
        "terminal_numbers": ["1", "2", "3"],
        "mechanical_checks": [
            {"name": "pad pitch", "expected": "0.95mm", "actual": "0.95mm", "pass": True, "page": 18},
            {"name": "body width", "expected": "datasheet range", "actual": "local footprint compatible", "pass": True, "page": 18},
            {"name": "recommended land pattern", "expected": "datasheet figure", "actual": "local pads checked", "pass": True, "page": 19},
        ],
    })
    assert result["status"] == "verified"
    assert result["authority"] == "datasheet"
