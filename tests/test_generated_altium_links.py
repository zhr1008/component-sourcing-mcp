from pathlib import Path

import pytest
from altium_monkey import AltiumPcbLib, AltiumSchLib

from szlcsc_mcp.ad_catalog import _parse_pcblib, _parse_schlib
from szlcsc_mcp.altium_links import LibraryLinkError, link_generated_libraries


def libraries(tmp_path, names=("SOT23",)):
    sch, pcb = tmp_path / "Test.SchLib", tmp_path / "Test.PcbLib"
    library = AltiumSchLib()
    library.add_symbol("TPR3333-S3TR")
    library.save(sch)
    footprints = AltiumPcbLib()
    for name in names:
        fp = footprints.add_footprint(name)
        fp.add_pad(designator="1", position_mils=(40.8015, -37.3285), width_mils=40.846, height_mils=20.945)
    footprints.save(pcb)
    return sch, pcb


def test_real_binary_pad_dimensions_are_read_in_mils(tmp_path):
    _, pcb = libraries(tmp_path)
    pad = _parse_pcblib(pcb)[0]["pads"][0]
    assert pad["height_mils"] == pytest.approx(20.945)
    assert pad["width_mils"] == pytest.approx(40.846)
    assert pad["x_mils"] == pytest.approx(40.8015)


def test_link_is_present_in_saved_schlib_and_second_run_is_idempotent(tmp_path):
    sch, pcb = libraries(tmp_path)
    result = link_generated_libraries([str(sch)], [str(pcb)])
    assert result["ok"] and result["created_count"] == 1
    assert result["verified_from_saved_libraries"]
    parsed = _parse_schlib(sch)[0]
    assert parsed["footprint_links"][0]["name"] == "SOT23"
    before = sch.read_bytes()
    second = link_generated_libraries([str(sch)], [str(pcb)])
    assert second["created_count"] == 0 and sch.read_bytes() == before


def test_ambiguous_footprints_do_not_modify_output(tmp_path):
    sch, pcb = libraries(tmp_path, ("SOT23_A", "SOT23_B"))
    before = sch.read_bytes()
    with pytest.raises(LibraryLinkError, match="uniquely"):
        link_generated_libraries([str(sch)], [str(pcb)])
    assert sch.read_bytes() == before


def test_existing_conflicting_link_is_not_replaced(tmp_path):
    sch, pcb = libraries(tmp_path)
    library = AltiumSchLib(sch)
    library.get_symbol("TPR3333-S3TR").add_footprint("OTHER")
    library.save(sch)
    before = sch.read_bytes()
    with pytest.raises(LibraryLinkError, match="unresolved"):
        link_generated_libraries([str(sch)], [str(pcb)])
    assert sch.read_bytes() == before


def test_corrupt_converter_output_is_not_marked_success(tmp_path):
    sch, pcb = libraries(tmp_path)
    Path(sch).write_bytes(b"not a SchLib")
    with pytest.raises(LibraryLinkError, match="validation failed"):
        link_generated_libraries([str(sch)], [str(pcb)])
