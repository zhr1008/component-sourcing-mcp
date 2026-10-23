import importlib
import asyncio


def test_server_registers_tools_without_running_transport(monkeypatch):
    from fastmcp import FastMCP
    def unexpected_run(*args, **kwargs):
        raise AssertionError("transport must not start during import")
    monkeypatch.setattr(FastMCP, "run", unexpected_run)
    server = importlib.import_module("szlcsc_mcp.server")
    registered = [t.name for t in asyncio.run(server.mcp.list_tools())]
    assert {
        "szlcsc_search",
        "szlcsc_parametric_search",
        "szlcsc_get_part",
        "szlcsc_compare_parts",
        "szlcsc_find_alternatives",
        "szlcsc_download_part",
        "szlcsc_download_parts",
        "szlcsc_sync_datasheets",
        "szlcsc_eda_assets",
        "szlcsc_altium_status",
        "szlcsc_altium_review",
        "szlcsc_altium_import",
        "szlcsc_altium_import_batch",
        "szlcsc_library_status",
        "szlcsc_library_search",
        "szlcsc_library_audit",
        "szlcsc_library_repair",
        "szlcsc_library_add",
        "szlcsc_library_sync",
        "szlcsc_library_merge",
        "szlcsc_parse_barcode",
        "szlcsc_healthcheck",
        "szlcsc_cache_status",
    }.issubset(set(registered))
