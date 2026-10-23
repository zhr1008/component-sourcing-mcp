from pathlib import Path

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient
from szlcsc_mcp.models import Part


class RelevanceClient(SZLCSCClient):
    def __init__(self, tmp_path: Path):
        super().__init__(cache=SqliteCache(tmp_path / "cache.sqlite3"), request_delay=0)

    def _get_json(self, url, params=None, ttl=600, **kwargs):
        if url.endswith("/phone/p/product/search"):
            # Reproduce the failure seen in the real-machine report: a generic
            # Ethernet PHY/transceiver is returned ahead of a real RS485 part.
            return {
                "productList": [
                    {
                        "id": 1,
                        "code": "C111",
                        "model": "LAN8742AI-CZ-TR",
                        "name": "10/100M Ethernet Transceiver PHY",
                        "catalogName": "以太网芯片",
                        "standard": "QFN-24",
                    },
                    {
                        "id": 2,
                        "code": "C222",
                        "model": "MAX3485ESA+T",
                        "name": "3.3V RS485 收发器",
                        "catalogName": "RS485/RS422收发器",
                        "standard": "SOIC-8",
                    },
                ]
            }
        raise AssertionError(url)


def test_rs485_intent_hard_filters_unrelated_ethernet_phy(tmp_path):
    client = RelevanceClient(tmp_path)
    parts = client.search("RS485 收发器 3.3V", max_results=10, enrich=False)
    assert [p.mpn for p in parts] == ["MAX3485ESA+T"]
    meta = client.search_relevance(parts[0], "RS485 收发器 3.3V")
    assert meta["hard_pass"] is True
    assert "RS485" in meta["triggered_intents"]


def test_explicit_model_token_is_a_relevance_anchor():
    good = Part(mpn="STM32F407VET6", name="ARM Cortex-M4 MCU")
    bad = Part(mpn="STM32F103C8T6", name="ARM Cortex-M3 MCU")
    good_meta = SZLCSCClient.search_relevance(good, "STM32F407")
    bad_meta = SZLCSCClient.search_relevance(bad, "STM32F407")
    assert good_meta["hard_pass"] is True
    assert bad_meta["hard_pass"] is False
    assert good_meta["score"] > bad_meta["score"]


def test_engineering_value_is_not_a_hard_model_anchor():
    part = Part(mpn="REF3033AIDBZR", name="电压基准芯片", category="电压基准芯片")
    meta = SZLCSCClient.search_relevance(part, "3.3V 电压基准")
    assert meta["hard_pass"] is True
    assert "3.3v" not in meta["missing_model_anchors"]
