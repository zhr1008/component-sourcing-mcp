"""Missing optional CAD packages must give actionable errors without startup failure."""
from pathlib import Path
import sys

import pytest

from szlcsc_mcp.ad_catalog import _parse_pcblib, _parse_schlib
from szlcsc_mcp.library_base import LibraryError


@pytest.mark.parametrize("parser", [_parse_schlib, _parse_pcblib])
def test_optional_parser_absence_gives_install_hint(monkeypatch, parser):
    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    with pytest.raises(LibraryError, match=r"component-sourcing-mcp\[altium\]"):
        parser(Path("unopened-fixture.SchLib"))
