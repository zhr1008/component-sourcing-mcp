import importlib
import json

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
