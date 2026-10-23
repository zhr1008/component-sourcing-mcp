"""Missing optional CAD packages must give actionable errors without startup failure."""
from pathlib import Path
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client

import szlcsc_mcp.altium as altium
from szlcsc_mcp.ad_catalog import _parse_pcblib, _parse_schlib
from szlcsc_mcp.altium_links import LibraryLinkError, link_generated_libraries
from szlcsc_mcp.library_base import LibraryError


@pytest.mark.parametrize("parser", [_parse_schlib, _parse_pcblib])
def test_optional_parser_absence_gives_install_hint(monkeypatch, parser):
    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    with pytest.raises(LibraryError, match=r"component-sourcing-mcp\[altium\]"):
        parser(Path("unopened-fixture.SchLib"))


def test_converter_status_is_not_ready_without_local_parser(monkeypatch):
    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "fake-cruncher")
    monkeypatch.setattr(altium, "_run_probe", lambda *args: SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    result = altium.altium_status()
    assert result["easyeda_import_available"] and result["merge_available"]
    assert result["ready"] is False
    assert result["parser_ready"] is False
    assert result["blocked_by"] == "altium_parser_not_installed"


def test_full_import_checks_parser_before_creating_output_or_launching(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "fake-cruncher")
    launch = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(altium.subprocess, "run", launch)
    output = tmp_path / "untouched"
    with pytest.raises(altium.AltiumImportError, match=r"component-sourcing-mcp\[altium\]") as caught:
        altium.run_altium_import("C1", output)
    assert caught.value.code == "DEPENDENCY_MISSING"
    assert not output.exists()
    launch.assert_not_called()


def test_missing_link_parser_is_a_domain_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    with pytest.raises(LibraryLinkError, match=r"component-sourcing-mcp\[altium\]"):
        link_generated_libraries(["unopened.SchLib"], ["unopened.PcbLib"])


def test_catalog_status_checks_parser_import_even_when_metadata_exists(monkeypatch, tmp_path):
    from szlcsc_mcp import server_ad
    import json

    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    monkeypatch.setattr(server_ad, "_package_version", lambda _: "2026.9.22")
    status = json.loads(server_ad.szlcsc_ad_library_status(str(tmp_path)))
    assert status["altium_monkey_version"] == "2026.9.22"
    assert status["parser_ready"] is False
    assert status["parser_install_extra"] == "component-sourcing-mcp[altium]"


@pytest.mark.parametrize("tool,arguments", [
    ("szlcsc_altium_import", {"identifier": "ABC-123"}),
    ("szlcsc_altium_import_batch", {"identifiers": ["ABC-123"]}),
])
def test_mcp_dependency_error_precedes_network_and_preserves_server(monkeypatch, tmp_path, tool, arguments):
    from szlcsc_mcp import server_base
    from szlcsc_mcp.server import mcp

    monkeypatch.setitem(sys.modules, "altium_monkey", None)
    lookup, download = Mock(), Mock()
    monkeypatch.setattr(server_base.client, "get_part", lookup)
    monkeypatch.setattr(server_base.eda_client, "write_altium_source", download)

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(tool, {**arguments, "output_root": str(tmp_path / "untouched")}, raise_on_error=False)
            assert result.is_error
            assert result.structured_content["error"]["code"] == "DEPENDENCY_MISSING"
            assert not (await client.call_tool("szlcsc_cache_status")).is_error

    asyncio.run(run())
    lookup.assert_not_called()
    download.assert_not_called()
    assert not (tmp_path / "untouched").exists()
