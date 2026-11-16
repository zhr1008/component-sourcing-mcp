"""Exercise the Skill's saved-file inspection against real Altium containers."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "szlcsc" / "scripts" / "inspect_saved.py"


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
