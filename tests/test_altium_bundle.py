from hashlib import sha256
import json

import pytest

from szlcsc_mcp.altium_bundle import Expected, _review, _stem, review_bundle
from szlcsc_mcp.errors import SourcingError


def evidence(group, field, value, **kwargs):
    return Expected(group=group, field=field, value=value, source="datasheet",
                    reference="Manufacturer package drawing", page=2, **kwargs)


def report():
    return {"target_board": "design.PcbDoc", "observations": {
        "pins": {"numbers": ["1", "2"], "pin.1[1].name": "VIN"},
        "pads": {"pad.1[1].width_mm": 1.02},
        "paste_mask": {"pad.1[1].mode": "rule", "pad.1[1].expansion_mm": None},
        "orientation_3d": {"body.1.rotation_z": 90.0},
        "board_rules": {"rule.1": ["Clearance", "Clearance", "True", "1", "All", "All"]},
    }}


def test_evidence_compares_dimensions_and_cannot_turn_unknown_into_zero():
    checks = _review(report(), [
        evidence("pins", "numbers", ["1", "2"]),
        evidence("pins", "pin.1[1].name", "VIN"),
        evidence("pads", "pad.1[1].width_mm", 1.0, tolerance=0.03),
        evidence("paste_mask", "pad.1[1].mode", "rule"),
        evidence("paste_mask", "pad.1[1].expansion_mm", 0),
        evidence("orientation_3d", "body.1.rotation_z", 90),
    ], None)["review"]
    assert checks["pins"]["status"] == "pass"
    assert checks["pads"]["status"] == "pass"
    assert checks["paste_mask"]["checks"]["pad.1[1].expansion_mm"]["status"] == "pending"
    assert checks["orientation_3d"]["checks"]["pin1_alignment"]["status"] == "pending"
    assert checks["board_rules"]["checks"]["native_drc"]["status"] == "pending"
    mismatch = _review(report(), [evidence("pins", "pin.1[1].name", "GND")], None)
    assert mismatch["review_status"] == "fail"


def test_evidence_needs_a_real_field_and_datasheet_locator():
    with pytest.raises(SourcingError, match="字段"):
        _review(report(), [evidence("pins", "fake", "X")], None)
    unsupported = Expected(group="pins", field="numbers", value=["1", "2"],
                           source="datasheet", reference="Vendor page")
    with pytest.raises(SourcingError, match="页码"):
        _review(report(), [unsupported], None)


def test_changes_invalidate_observations(tmp_path):
    library = tmp_path / "TPR3333-S3TR_C5291487.PcbLib"
    library.write_bytes(b"original")
    payload = report() | {"mode": "offline_library", "sha256": {library.name: sha256(b"original").hexdigest()}}
    (tmp_path / "verification.json").write_text(json.dumps(payload), encoding="utf-8")
    library.write_bytes(b"changed")
    with pytest.raises(SourcingError) as exc:
        review_bundle(tmp_path, [])
    assert exc.value.code == "BUNDLE_CHANGED"
    assert _stem("LM4132AMF-3.3/NOPB", "C123") == "LM4132AMF-3.3_NOPB_C123"
