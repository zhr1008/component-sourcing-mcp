import json
from pathlib import Path

from szlcsc_mcp.eda import EasyEDAAssetClient, extract_3d_models, split_svg_payload


def sample_cad():
    return {
        "packageDetail": {
            "dataStr": {
                "shape": [
                    "PAD~1~2~3",
                    'SVGNODE~{"gId":"g1","attrs":{"uuid":"3d-uuid-123","title":"SOT23_Model","c_etype":"outline3d","c_origin":"1,2","z":"0","c_rotation":"0,0,90"}}~0~0',
                ]
            }
        }
    }


def test_extract_3d_models_from_svgnode():
    refs = extract_3d_models(sample_cad())
    assert len(refs) == 1
    assert refs[0].uuid == "3d-uuid-123"
    assert refs[0].title == "SOT23_Model"
    assert refs[0].rotation == "0,0,90"


def test_extract_3d_models_deduplicates_nested_attrs():
    data = sample_cad()
    data["other"] = {"attrs": {"uuid": "3d-uuid-123", "c_etype": "outline3d", "title": "same"}}
    assert [x.uuid for x in extract_3d_models(data)] == ["3d-uuid-123"]



def test_extract_3d_models_without_trailing_tilde():
    shape = 'SVGNODE~{"attrs":{"uuid":"u-no-tail","c_etype":"outline3d","c_origin":"0,0","z":"0","c_rotation":"0,0,0"}}'
    refs = extract_3d_models({"shape": [shape]})
    assert [x.uuid for x in refs] == ["u-no-tail"]


def test_split_svg_payload_symbol_then_footprint():
    payload = {
        "result": [
            {"svg": '<svg id="sym1"></svg>'},
            {"svg": '<svg id="sym2"></svg>'},
            {"svg": '<svg id="fp"></svg>'},
        ]
    }
    symbols, footprint = split_svg_payload(payload)
    assert len(symbols) == 2
    assert 'sym1' in symbols[0]
    assert 'fp' in footprint


class FakeEasyEDA(EasyEDAAssetClient):
    def __init__(self):
        # Deliberately avoid creating network/cache state for this fixture.
        self.max_download_bytes = 1024 * 1024

    def get_cad_data(self, c_code):
        return sample_cad()

    def get_svgs(self, c_code):
        return ['<svg id="sym"></svg>'], '<svg id="fp"></svg>'

    def _download_binary(self, url):
        if "/3dmodel/" in url:
            return b"o model\nv 0 0 0\n"
        return b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"


def test_download_bundle_writes_manifest_and_assets(tmp_path):
    client = FakeEasyEDA()
    result = client.download_bundle("C2040", tmp_path)
    root = tmp_path / "C2040"
    assert Path(result["manifest"]).is_file()
    assert (root / "easyeda_raw.json").is_file()
    assert (root / "symbol.svg").is_file()
    assert (root / "footprint.svg").is_file()
    assert len(result["files"]["models"]) == 1
    assert Path(result["files"]["models"][0]["obj"]).is_file()
    assert Path(result["files"]["models"][0]["step"]).is_file()
    raw = json.loads((root / "easyeda_raw.json").read_text(encoding="utf-8"))
    assert raw["success"] is True
    assert "result" in raw
    parsed = json.loads((root / "eda_manifest.json").read_text(encoding="utf-8"))
    assert parsed["verification_required"] is True
    assert parsed["lcsc_code"] == "C2040"
    assert parsed["schema"] == 2
    assert parsed["asset_summary"]["model_ref_count"] == 1
    assert parsed["asset_summary"]["obj_downloaded"] == 1
    assert parsed["asset_summary"]["step_downloaded"] == 1
    assert parsed["asset_summary"]["step_available"] is True


def test_write_altium_source_keeps_result_wrapper(tmp_path):
    client = FakeEasyEDA()
    path = client.write_altium_source("C2040", tmp_path / "source.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["success"] is True
    assert data["result"] == sample_cad()
