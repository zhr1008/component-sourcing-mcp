from __future__ import annotations

import csv
import json
from pathlib import Path
import pytest

from szlcsc_mcp.ad_catalog import empty_catalog, save_ad_catalog, export_ad_catalog
from szlcsc_mcp.ad_export import export_ad_catalog_complete


@pytest.mark.parametrize("value", ["=1+1", "+1+1", "-1+1", "@SUM(1)", " \t=1+1", "\r=1+1", "\n=1+1", "safe-symbol"])
@pytest.mark.parametrize("exporter", [export_ad_catalog_complete, export_ad_catalog])
def test_catalog_csv_treats_untrusted_fields_as_text_and_keeps_json(tmp_path, value, exporter):
    root = tmp_path / "catalog"
    catalog = empty_catalog()
    catalog["symbols"]["symbol:1"] = {"asset_id":"symbol:1", "name":value}
    save_ad_catalog(root, catalog)
    result = exporter(root, output_dir=tmp_path / "exports")
    with Path(result["files"]["csv"]).open(encoding="utf-8-sig", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["symbol"] == (value if value == "safe-symbol" else "'" + value)
    original = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    assert original["components"][0]["symbol"] == value


def test_complete_export_keeps_full_szlcsc_parameters_and_locations(tmp_path):
    root = tmp_path / "catalog"
    data = empty_catalog()
    data["symbols"]["symbol:1"] = {
        "asset_id": "symbol:1",
        "name": "TPR3333-S3TR",
        "library_path": "D:/Lib/Analog.SchLib",
        "parameters": {"Comment": "REF", "LegacyField": "keep-me"},
        "pins": [{"number": "1"}, {"number": "2"}, {"number": "3"}],
        "identity_hints": {"mpn": "TPR3333-S3TR", "lcsc_code": "C5291487"},
        "resolved_footprint_links": [{"name": "SOT23_TEST", "status": "resolved", "candidates": ["footprint:1"]}],
        "enrichment": {
            "status": "resolved",
            "method": "exact_c_code",
            "lcsc_code": "C5291487",
            "mpn": "TPR3333-S3TR",
            "brand": "3PEAK",
            "category": "电压基准芯片",
            "package": "SOT-23G-3",
            "small_batch_price_cny": 1.23,
            "stock": 100,
            "parameters": {"输出电压": "3.3V", "温漂": "10ppm/℃", "精度": "0.15%"},
        },
        "verification": {"status": "verified", "authority": "datasheet", "datasheet_pages": [3, 18]},
        "issues": [],
    }
    data["footprints"]["footprint:1"] = {
        "name": "SOT23_TEST",
        "library_path": "D:/Lib/Footprints.PcbLib",
        "pads": [{"number": "1"}, {"number": "2"}, {"number": "3"}],
        "geometry": {"pad_count": 3, "minimum_pad_center_distance_mm": 0.95},
    }
    save_ad_catalog(root, data)

    result = export_ad_catalog_complete(root, output_dir=tmp_path / "exports", export_format="both")
    assert result["contains_full_szlcsc_parameters"] is True
    csv_path = Path(result["files"]["csv"])
    json_path = Path(result["files"]["json"])
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        row = next(csv.DictReader(fh))
    params = json.loads(row["szlcsc_parameters_json"])
    assert params["输出电压"] == "3.3V"
    assert params["温漂"] == "10ppm/℃"
    assert row["schlib"] == "D:/Lib/Analog.SchLib"
    assert row["resolved_pcblib"] == "D:/Lib/Footprints.PcbLib"
    assert row["verification_status"] == "verified"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["components"][0]["lcsc_code"] == "C5291487"
    assert json.loads(payload["components"][0]["szlcsc_parameters_json"])["精度"] == "0.15%"
