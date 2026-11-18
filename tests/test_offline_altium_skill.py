"""Exercise the Skill's saved-file inspection against real Altium containers."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "component-sourcing" / "scripts" / "inspect_saved.py"


def read_saved(path, *args):
    before = path.read_bytes()
    run = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), str(path), *args],
        capture_output=True, encoding="utf-8", timeout=30, check=True,
    )
    assert path.read_bytes() == before
    result = json.loads(run.stdout)
    assert result["source"] == "saved_file"
    return result


def test_lists_project_documents_even_if_the_members_are_missing(tmp_path):
    pytest.importorskip("altium_monkey")
    project = tmp_path / "Fixture.PrjPcb"
    project.write_text(
        "[Document1]\nDocumentPath=Missing.SchDoc\n\n[Document2]\nDocumentPath=Board.PcbDoc\n",
        encoding="ascii",
    )
    result = read_saved(project, "--limit", "1")
    assert result["document_count"] == 2
    assert result["documents"] == ["Missing.SchDoc"]
    assert result["truncated"] is True


def test_intlib_lookup_preserves_exact_fields_and_footprint_reference(tmp_path):
    altium = pytest.importorskip("altium_monkey")
    sch, pcb, packed = (tmp_path / f"Fixture.{ext}" for ext in ("SchLib", "PcbLib", "IntLib"))
    symbols = altium.AltiumSchLib()
    for name in ("R_10K", "R_100K"):
        row = symbols.add_symbol(name)
        row.add_parameter("Resistance", name[2:])
        row.add_footprint("TEST0603", library_name=pcb.name)
    symbols.save(sch)
    footprints = altium.AltiumPcbLib()
    fp = footprints.add_footprint("TEST0603")
    fp.add_pad(designator="1", position_mils=(0, 0), width_mils=15, height_mils=15)
    footprints.save(pcb)
    altium.AltiumIntLib.create_from_libraries(packed, schlib_paths=[sch], pcblib_paths=[pcb])
    assert read_saved(packed, "--limit", "1")["truncated"]
    result = read_saved(packed, "--component", "r_100k")
    assert result["component_count"] == 2 and result["matched_count"] == 1
    item, = result["components"]
    assert item["footprints"] == ["TEST0603"] and {"name": "Resistance", "value": "100K"} in item["parameters"]


@pytest.mark.parametrize("kind", ["schdoc", "pcbdoc"])
def test_reads_saved_component_by_designator(tmp_path, kind):
    altium = pytest.importorskip("altium_monkey")
    fixture = tmp_path / f"Fixture.{kind}"
    if kind == "schdoc":
        doc = altium.AltiumSchDoc()
        doc.add_component("OFFLINE_FIXTURE", designator="U1", x=150, y=200)
    else:
        doc = altium.AltiumPcbDoc()
        doc.add_component(designator="U1", footprint="FIXTURE_PAD", position_mils=(12.5, 20.0))
    doc.save(fixture)
    result = read_saved(fixture, "--designator", "u1")
    assert result["component_count"] == result["matched_count"] == 1
    assert result["components"][0]["designator"] == "U1"
    if kind == "schdoc":
        assert result["components"][0]["pin_count"] == 0
    else:
        assert result["components"][0]["footprint"] == "FIXTURE_PAD"
