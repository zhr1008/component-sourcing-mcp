import asyncio
import json
from unittest.mock import Mock

import pytest
from fastmcp import Client

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient
from szlcsc_mcp.models import Part, PriceTier
from szlcsc_mcp.selection import selection_criteria, selection_row


def test_shortlist_then_change_accuracy_then_quote_through_mcp(monkeypatch, tmp_path):
    from szlcsc_mcp import server_base, server_sourcing
    from szlcsc_mcp.server import mcp

    backend = SZLCSCClient(cache=SqliteCache(tmp_path / "catalog.db"), request_delay=0)
    variants = [
        ("REF1001", "5V", "0.1%", "SOT-23-3"),
        ("REF2001", "3.3V", "0.15%", "SOT-23-3"),
        ("REF3001", "3.3V", "0.4%", "SOT-23-3"),
        ("REF4001", "3.3V", "0.1%", "SOIC-8"),
        ("REF5001", "3.3V", None, "SOT-23-5"),
        *[(f"REF60{i}1", "3.3V", "0.1%", "SOT-23-3") for i in range(6)],
    ]
    catalog = []
    for index, (model, voltage, accuracy, package) in enumerate(variants, 2000):
        parameters = {"输出电压": voltage, "精度": "0.02%", "温度系数": "15ppm/℃"}
        if accuracy is not None:
            parameters["初始精度"] = accuracy
        catalog.append({
            "id": index, "code": f"C{index}", "model": model, "name": "电压基准",
            "catalogName": "电压基准", "brandName": "验证品牌", "standard": package,
            "productSignId": f"product-{index}", "productStockNumber": 9000,
            "priceList": [{"startNumber": 1, "price": 0.01}],
        })
        catalog[-1]["detail"] = parameters

    def upstream(url, params=None, **kwargs):
        if url.endswith("/product/search"):
            keyword = params["keyword"]
            if keyword in {"电压基准", "3.3V 电压基准", "voltage reference"}:
                products = catalog
            else:
                products = [row for row in catalog if keyword.casefold() in (row["code"] + " " + row["model"]).casefold()]
            return {"productList": products, "total": len(products)}
        if "/phone/p/product-" in url:
            part = next(row for row in catalog if url.endswith(row["productSignId"]))
            return {"code": part["code"], "model": part["model"], "params": part["detail"],
                    "datasheet": "https://example.org/reference.pdf"}
        pytest.fail(f"Unexpected catalog or procurement request to {url}")

    def current_product(part):
        part.stock = 80
        part.min_order_qty = 20
        part.order_multiple = 5
        part.price_tiers = [PriceTier(min_qty=20, unit_price_cny=0.85)]
        part.procurement = {"source": "public_product_page", "quantity_price_verified": True}

    monkeypatch.setattr(backend, "_get_json", Mock(side_effect=upstream))
    page = Mock(side_effect=current_product)
    monkeypatch.setattr(backend, "_enrich_from_html", page)
    monkeypatch.setattr(server_sourcing, "client", backend)
    monkeypatch.setattr(server_base, "client", backend)

    async def run():
        async with Client(mcp) as conn:
            first = await conn.call_tool("szlcsc_select", {
                "request": "找一颗3.3V电压基准，初始精度≤0.2%，SOT-23，立创好买",
            })
            result = first.structured_content
            assert not first.is_error and result["count"] == len(result["results"]) == 5
            assert result["selection"]["constraints"] == {
                "package": "=SOT-23", "initial_accuracy": "<=0.2%", "output_voltage": "=3.3V",
            }
            assert result["coverage"]["returned_count"] == 5
            assert not any(call.args[0].endswith("/product/search") and
                           call.args[1]["keyword"].startswith("C2") for call in backend._get_json.call_args_list)
            assert result["excluded_by_condition"] == {"初始精度": 1, "封装": 1, "输出电压": 1}
            first_row = result["results"][0]
            assert first_row["lcsc_code"] == "C2001"
            assert first_row["ad_comment"] == "REF2001"
            assert first_row["ad_description"].split("/")[2] == "REF2001"
            assert first_row["ad_description"].endswith("/SOT-23-3/C2001/验证品牌")
            assert first_row["evidence"]["datasheet_url"] == "https://example.org/reference.pdf"
            assert all(row["match_status"] == "catalog_match" for row in result["results"])
            assert all("procurement" not in row for row in result["results"])
            page.assert_not_called()

            updated = await conn.call_tool("szlcsc_select", {
                "request": "精度可以放宽到0.5%", "previous": result["selection"],
            })
            changed = updated.structured_content
            assert changed["selection"]["constraints"] == {
                "package": "=SOT-23", "initial_accuracy": "<=0.5%", "output_voltage": "=3.3V",
            }
            assert changed["eligible_inspected"] == result["eligible_inspected"] + 1
            assert any(row["lcsc_code"] == "C2002" for row in changed["results"])
            page.assert_not_called()

            quoted = await conn.call_tool("szlcsc_select", {
                "request": "这次12颗", "previous": changed["selection"],
            })
            quotes = quoted.structured_content
            assert quotes["selection"]["constraints"] == changed["selection"]["constraints"]
            assert quotes["selection"]["quantity"] == 12
            assert page.call_count == quotes["count"] == 5
            price = quotes["results"][0]["procurement"]
            assert (price["stock"], price["requested_quantity"], price["order_quantity"]) == (80, 12, 20)
            assert price["unit_price_cny"] == 0.85 and price["line_total_cny"] == "17.00"

            cleared = await conn.call_tool("szlcsc_select", {
                "request": "暂时不查报价，取消这12颗的报价", "previous": quotes["selection"],
            })
            assert cleared.structured_content["selection"]["quantity"] is None
            assert cleared.structured_content["selection"]["constraints"] == changed["selection"]["constraints"]
            assert all("procurement" not in row for row in cleared.structured_content["results"])

            detail = await conn.call_tool("szlcsc_get_part", {
                "identifier": "C2001", "include_procurement": False,
            })
            part = json.loads(detail.content[0].text)
            assert not detail.is_error and part["mpn"] == "REF2001"
            assert not part["price_tiers"] and part["stock"] is None
            assert part["procurement"]["source"] == "not_requested"
            discover = await conn.call_tool("szlcsc_discover", {
                "query": "电压基准", "max_results": 2, "include_procurement": False,
            })
            rows = json.loads(discover.content[0].text)["results"]
            assert not discover.is_error and len(rows) == 2
            assert all(row["part"]["stock"] is None and not row["part"]["price_tiers"] for row in rows)
            assert page.call_count == quotes["count"]

    try:
        asyncio.run(run())
    finally:
        backend.close()


def test_capacitor_description_and_comment_use_catalog_values():
    criteria, _ = selection_criteria("100nF 25V X7R 0402")
    assert criteria.constraints == {
        "package": "=0402", "dielectric": "==X7R", "voltage_rating": ">=25V", "capacitance": "=100nF",
    }
    part = Part(
        mpn="CAP0402", lcsc_code="C765", package="0402",
        parameters={"容值": "100nF", "耐压": "50V", "精度": "±10%", "介质": "X7R"},
    )
    result = selection_row({"part": part, "found_by": [], "requirements": []}, criteria)
    assert result["ad_comment"] == "100nF/50V"
    assert result["ad_description"] == "贴片电容/100nF/50V/±10%/X7R/0402"


def test_condition_edits_preserve_hard_requirements():
    initial, _ = selection_criteria("找5V供电的隔离CAN FD收发器，隔离耐压≥2.5kV，SOIC-16")
    updated, _ = selection_criteria("封装不限", initial)
    assert initial.quantity is None and updated.quantity is None
    assert updated.catalog_query == "隔离式CAN收发器"
    assert updated.constraints == {
        "isolation_voltage": ">=2.5kV", "supply_voltage": "=5V", "can_fd": "=CAN FD",
    }


def test_ad_comment_preserves_ordering_suffix_and_description_uses_the_same_model():
    criteria, _ = selection_criteria("3.3V电压基准")
    part = Part(mpn="LM4132AMF-3.3/NOPB", lcsc_code="C181258", package="SOT-23-5",
                brand="TI", parameters={"输出电压": "3.3V"})
    row = selection_row({"part": part, "found_by": [], "requirements": []}, criteria)
    assert row["ad_comment"] == row["mpn"] == part.mpn
    assert row["ad_description"] == "电压基准/输出电压3.3V/LM4132AMF-3.3/NOPB/重要参数待核实/SOT-23-5/C181258/TI"


@pytest.mark.parametrize("token, expected", [("10K", "10KΩ"), ("2m", "2mΩ"), ("1M", "1MΩ")])
def test_passive_requirements_in_chinese_shorthand(token, expected):
    criteria, _ = selection_criteria(f"选{token}电阻，0603，100mW，1%")
    assert criteria.constraints == {"resistance": "=" + expected, "package": "=0603",
                                    "power": ">=100mW", "tolerance": "<=1%"}
    updated, _ = selection_criteria("额定功率至少1/4W", criteria)
    assert updated.constraints == criteria.constraints | {"power": ">=1/4W"}


def test_regulator_range_and_passive_ad_fields():
    criteria, _ = selection_criteria("找一颗LDO，输入3V~5V，输出1.8V")
    assert criteria.constraints == {"input_voltage_min": "<=3V", "input_voltage_max": ">=5V", "output_voltage": "=1.8V"}
    for query, params, comment, source_value in (
        ("贴片电阻", {"阻值": "4.7kΩ", "功率": "100mW"}, "4.7K", "4.7kΩ"),
        ("贴片电容", {"容值": "1µF", "耐压": "50V"}, "1uF/50V", "1uF"),
        ("电感", {"电感量": "6.8µH"}, "6.8µH", "6.8µH"),
    ):
        part = Part(lcsc_code="C123", mpn="FIXTURE", brand="FH(风华)", package="0603", parameters=params)
        row = selection_row({"part": part, "requirements": [], "found_by": []}, selection_criteria(query)[0])
        assert row["ad_comment"] == comment
        assert row["generic_description"].split("/")[1] == source_value
        fields = row["specific_description"].split("/")
        assert len(fields) == 7 and fields[2] == "FIXTURE" and fields[-3:] == ["0603", "C123", "FH(风华)"]


def test_brand_preference_is_carried_separately_from_hard_filters():
    criteria, _ = selection_criteria("10K电阻优先风华")
    assert criteria.preferred_brand == "风华" and "brand" not in criteria.constraints
    updated, _ = selection_criteria("功率250mW", criteria)
    assert updated.preferred_brand == "风华"
    cleared, _ = selection_criteria("品牌不限", updated)
    assert cleared.preferred_brand is None and cleared.constraints == updated.constraints
