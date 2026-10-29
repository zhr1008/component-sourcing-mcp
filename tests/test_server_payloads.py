import asyncio
import importlib
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from szlcsc_mcp.models import Part


def _import_server(monkeypatch):
    return importlib.import_module("szlcsc_mcp.server")


class DummyAlternativesClient:
    def __init__(self):
        self.source = Part(
            lcsc_code="C1",
            mpn="OPA333AIDBVR",
            package="SOT-23-5",
            stock=10,
            parameters={"通道数": "1"},
        )
        self.candidate = Part(
            lcsc_code="C2",
            mpn="TLV333IDBVR",
            package="SOT-23-5",
            stock=20,
            parameters={"通道数": "1"},
        )

    def get_part(self, identifier):
        return self.source

    def alternatives(self, identifier, max_results=10, in_stock_only=True):
        return [self.candidate]

    def alternative_score(self, source, candidate):
        return {
            "score": 8.4,
            "reasons": ["封装相同", "国内有货", "1 个同名参数完全一致"],
            "package_match": True,
            "in_stock": True,
            "exact_parameter_matches": 1,
            "warning": "test",
        }


def test_alternative_score_is_exposed_top_level(monkeypatch):
    server = _import_server(monkeypatch)
    monkeypatch.setattr(importlib.import_module("szlcsc_mcp.server_base"), "client", DummyAlternativesClient())
    payload = json.loads(server.szlcsc_find_alternatives("C1"))
    row = payload["candidates"][0]
    assert row["score"] == 8.4
    assert row["score_reasons"][0] == "封装相同"
    assert row["ranking"]["score"] == 8.4


def test_requirement_discovery_mcp_schema_and_structured_candidate_evidence(monkeypatch):
    server = _import_server(monkeypatch)
    base = importlib.import_module("szlcsc_mcp.server_base")
    from unittest.mock import Mock
    discover = Mock(return_value={
        "results": [{"part": Part(lcsc_code="C2671569", mpn="ISO1042QDWRQ1"),
                     "exact_identity": False, "requirements": [{"requirement": "AEC-Q100", "status": "unknown"}]}],
        "count": 1, "coverage": {"complete": False, "query_errors": [], "remaining_candidates": 2},
    })
    monkeypatch.setattr(base.client, "discover", discover)

    async def check():
        async with Client(server.mcp) as client:
            tool = next(t for t in await client.list_tools() if t.name == "szlcsc_discover")
            assert tool.input_schema["properties"]["max_queries"]["maximum"] == 8
            result = await client.call_tool("szlcsc_discover", {"query": "isolated CAN"})
            assert not result.is_error
            assert result.structured_content["results"][0]["part"]["mpn"] == "ISO1042QDWRQ1"
            assert result.structured_content["coverage"]["complete"] is False
            assert result.structured_content["results"][0]["requirements"][0]["status"] == "unknown"
            for args in ({"max_queries": 9}, {"pages_per_query": 0}, {"family_queries": ["A"] * 5}, {"family_queries": [""]}):
                bad = await client.call_tool("szlcsc_discover", {"query": "CAN", **args}, raise_on_error=False)
                assert bad.is_error
                assert bad.structured_content["error"]["code"] == "INVALID_ARGUMENT"
    asyncio.run(check())


def test_search_array_contract_is_preserved_and_coverage_is_opt_in(monkeypatch):
    server = _import_server(monkeypatch)
    base = importlib.import_module("szlcsc_mcp.server_base")
    from unittest.mock import Mock
    from szlcsc_mcp.client import SearchResults
    monkeypatch.setattr(base.client, "search", Mock(return_value=SearchResults(
        [Part(lcsc_code="C1", mpn="PART")], coverage={"complete": False, "next_cursor": {"page": 1, "result_offset": 1}},
    )))
    legacy = json.loads(server.szlcsc_search("PART"))
    assert isinstance(legacy, list) and legacy[0]["lcsc_code"] == "C1"
    bounded = json.loads(server.szlcsc_search("PART", include_coverage=True))
    assert bounded["coverage"]["next_cursor"] == {"page": 1, "result_offset": 1}
    assert bounded["results"] == legacy


def test_library_sync_pagination_public_schema_and_validation(monkeypatch):
    server = _import_server(monkeypatch)

    async def check():
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            for name in ("szlcsc_library_sync", "szlcsc_library_sync_preview"):
                tool = next(tool for tool in tools if tool.name == name)
                props = tool.input_schema["properties"]
                assert props["offset"]["minimum"] == 0
                assert props["offset"]["default"] == 0
                assert props["limit"]["minimum"] == 1
                assert props["limit"]["maximum"] == 500
                assert props["limit"]["default"] == 500
                for values in ({"offset": -1}, {"limit": 0}, {"limit": 501},
                               {"offset": 1.5}, {"limit": True}, {"limit": "3"}):
                    with pytest.raises(ToolError):
                        await client.call_tool(name, values)

    asyncio.run(check())


def test_library_sync_registered_tools_accept_and_return_pages(monkeypatch, tmp_path):
    from szlcsc_mcp import server, server_base

    index = {"parts": {"C1": {"mpn": "PART"}, "C2": {"mpn": "PART"}}}

    class ClientStub:
        def get_part(self, identifier, *, refresh=False):
            assert refresh is True
            return Part(lcsc_code=identifier, mpn="PART")

        def close(self):
            pass

    monkeypatch.setattr(server, "client", ClientStub())
    monkeypatch.setattr(server_base, "client", server.client)
    monkeypatch.setattr(server, "load_index", lambda root: index)
    monkeypatch.setattr(server_base, "load_index", lambda root: index)
    monkeypatch.setattr(server_base, "upsert_part", lambda *args, **kwargs: {"entry": {}})

    async def check():
        async with Client(server.mcp) as client:
            for name in ("szlcsc_library_sync", "szlcsc_library_sync_preview"):
                result = await client.call_tool(name, {
                    "library_root": str(tmp_path), "offset": 1, "limit": 1,
                })
                assert not result.is_error
                assert result.structured_content["results"][0]["identifier"] == "C2"
                assert result.structured_content["has_more"] is False

    asyncio.run(check())
