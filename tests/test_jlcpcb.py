from unittest.mock import Mock

import pytest
from test_sourcing_hardening import response

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.errors import SourcingError
from szlcsc_mcp.jlcpcb import JLCPCBClient


def raw(code="C1", kind="base"):
    return {
        "componentCode": code,
        "componentModelEn": "PART",
        "componentLibraryType": kind,
        "stockCount": 10,
        "componentPrices": [{"startNumber": 1, "productPrice": 0.2}],
        "attributes": [
            {"attribute_name_en": "Voltage", "attribute_value_name": "3.3V"}
        ],
        "frontendUiSettings": "discard me",
    }


def test_assembly_filters_pagination_cleaning_and_cache(tmp_path):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    client.session.post = Mock(
        side_effect=lambda *args, **kwargs: response(
            200,
            {
                "code": 200,
                "data": {"componentPageInfo": {"list": [raw()], "total": 40}},
            },
        )
    )
    first = client.search(
        "PART", library_type="basic", page=2, page_size=10, min_stock=5, package="0603"
    )
    client.search(
        "PART", library_type="basic", page=2, page_size=10, min_stock=5, package="0603"
    )
    assert client.session.post.call_count == 1
    args = client.session.post.call_args.kwargs["json"]
    assert args["componentLibraryType"] == "base" and args["currentPage"] == 2
    assert args["componentSpecification"] == "0603" and args["startStockNumber"] == 5
    row = first["results"][0]
    assert row["inventory_pool"] == "JLCPCB_assembly" and row["assembly_stock"] == 10
    assert row["price_tiers"][0]["currency"] is None
    assert "frontendUiSettings" not in row
    assert first["has_more"] is True
    client.search(
        "PART",
        library_type="basic",
        page=2,
        page_size=10,
        min_stock=5,
        package="0603",
        refresh=True,
    )
    assert client.session.post.call_count == 2


def test_no_fee_deduplicates_basic_and_preferred_windows(tmp_path):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    preferred = {**raw("C2", "expand"), "preferredComponentFlag": True}
    basic_page = {"componentPageInfo": {"list": [raw()], "total": 1}}
    preferred_page = {"componentPageInfo": {"list": [preferred, preferred], "total": 1}}
    client._request = Mock(side_effect=[(basic_page, {}), (preferred_page, {})])
    result = client.search("PART", library_type="no_fee")
    assert [p["lcsc_code"] for p in result["results"]] == ["C1", "C2"]
    assert result["count"] == 2
    assert result["complete"] is True


def test_no_fee_retains_basic_page_when_preferred_is_denied(tmp_path):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    client._request = Mock(side_effect=[
        ({"componentPageInfo": {"list": [raw()], "total": 1}}, {}),
        SourcingError("denied", code="ACCESS_DENIED", retry_after=60),
    ])
    result = client.search("PART", library_type="no_fee")
    assert result["count"] == 1 and result["results"][0]["library_type"] == "basic"
    assert result["complete"] is False
    assert result["unavailable_libraries"][0]["library_filter"] == "preferred"
    assert result["unavailable_libraries"][0]["error"]["code"] == "ACCESS_DENIED"


def test_no_fee_is_an_error_when_both_windows_are_unavailable(tmp_path):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    client._request = Mock(side_effect=SourcingError("cooldown", code="RATE_LIMITED", retry_after=60))
    with pytest.raises(SourcingError) as caught:
        client.search("PART", library_type="no_fee")
    assert caught.value.code == "RATE_LIMITED"


@pytest.mark.parametrize(
    "shape",
    [
        None,
        [],
        {"code": 200, "data": None},
        {"code": 500, "data": {}},
        {"code": 200, "data": {}},
    ],
)
def test_malformed_assembly_envelopes_are_not_cached(tmp_path, shape):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    client.session.post = Mock(return_value=response(200, shape))
    with pytest.raises(SourcingError):
        client.search("PART")
    assert client.cache.status()["entries"] == 0


def test_category_hierarchy_and_day_cache(tmp_path):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    client.session.post = Mock(
        side_effect=lambda *args, **kwargs: response(
            200,
            {
                "code": 200,
                "data": {
                    "sortAndCountVoList": [
                        {
                            "componentSortKeyId": 1,
                            "sortName": "Passives",
                            "childSortList": [
                                {"componentSortKeyId": 2, "sortName": "Resistors"}
                            ],
                        }
                    ]
                },
            },
        )
    )
    result = client.categories()
    assert result["categories"][0]["children"][0]["id"] == 2
    assert result["data_freshness"]["ttl_seconds"] == 86400
    client.categories()
    assert client.session.post.call_count == 1
