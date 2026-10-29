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
