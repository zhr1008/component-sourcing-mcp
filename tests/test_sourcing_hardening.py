import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastmcp import Client
from jsonschema import validate

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient, SZLCSCError
from szlcsc_mcp.errors import SourcingError
from szlcsc_mcp.http import SafeSession, retry_after_seconds, safe_get
from szlcsc_mcp.models import Part, PriceTier
from szlcsc_mcp.normalize import (
    as_float,
    as_int,
    merge_part,
    normalize_datasheet_url,
    part_from_dict,
)


def response(status=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    result.url = "https://so.szlcsc.com/test"
    return result


@pytest.fixture
def isolated_http(monkeypatch):
    import szlcsc_mcp.http as http

    monkeypatch.setattr(http, "_HOSTS", {})
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)


@pytest.mark.parametrize(
    "status,code",
    [(401, "ACCESS_DENIED"), (403, "ACCESS_DENIED"), (429, "RATE_LIMITED")],
)
def test_access_denial_and_rate_limit_never_retry_or_rotate(
    monkeypatch, isolated_http, status, code
):
    request = Mock(return_value=response(status, headers={"Retry-After": "120"}))
    monkeypatch.setattr(requests.Session, "request", request)
    session = SafeSession(delay=0, retries=2)
    with pytest.raises(SourcingError) as caught:
        session.get("https://so.szlcsc.com/test")
    assert caught.value.code == code
    assert request.call_count == 1
    with pytest.raises(SourcingError):
        session.get("https://so.szlcsc.com/test")
    assert request.call_count == 1  # host cooldown, no additional upstream hit


def test_transient_read_retries_but_permanent_404_does_not(monkeypatch, isolated_http):
    request = Mock(side_effect=[response(503), response(502), response(200)])
    monkeypatch.setattr(requests.Session, "request", request)
    session = SafeSession(delay=0, retries=2)
    assert session.get("https://so.szlcsc.com/test").status_code == 200
    assert request.call_count == 3
    request.reset_mock(side_effect=True)
    request.return_value = response(404)
    assert session.get("https://so.szlcsc.com/test").status_code == 404
    assert request.call_count == 1


def test_timeout_retries_are_bounded(monkeypatch, isolated_http):
    request = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(requests.Session, "request", request)
    with pytest.raises(SourcingError, match="connection"):
        SafeSession(delay=0, retries=2).get("https://so.szlcsc.com/test")
    assert request.call_count == 3


def test_session_get_cannot_reenable_automatic_redirects(monkeypatch, isolated_http):
    request = Mock(
        return_value=response(302, headers={"Location": "http://127.0.0.1/private"})
    )
    monkeypatch.setattr(requests.Session, "request", request)
    result = SafeSession(delay=0).get("https://so.szlcsc.com/test")
    assert result.status_code == 302
    assert request.call_args.kwargs["allow_redirects"] is False


def test_retry_after_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120))
    assert 118 <= retry_after_seconds(future) <= 120


@pytest.mark.parametrize("value", ["NaN", "inf", "garbage"])
def test_retry_after_rejects_invalid_values(value):
    assert retry_after_seconds(value) is None


def test_redirect_target_is_validated_before_it_is_fetched():
    session = SimpleNamespace(
        get=Mock(
            return_value=response(302, headers={"Location": "http://127.0.0.1/private"})
        )
    )

    def validate_url(url):
        if not url.startswith("https://atta.szlcsc.com/"):
            raise SourcingError("unapproved redirect")

    with pytest.raises(SourcingError, match="unapproved"):
        safe_get(
            session, "https://atta.szlcsc.com/a.pdf", validate=validate_url, timeout=1
        )
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [200, 403])
def test_json_business_errors_rejected_without_result_key(status, tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.session.get = Mock(
        return_value=response(status, {"code": 403, "message": "denied"})
    )
    with pytest.raises(SZLCSCError):
        client._get_json("https://so.szlcsc.com/test")
    assert client.cache.status()["entries"] == 0


def test_expired_stock_is_not_silently_returned_on_network_failure(tmp_path):
    cache = SqliteCache(tmp_path / "cache.db")
    client = SZLCSCClient(cache=cache, request_delay=0)
    url = "https://so.szlcsc.com/test"
    key = cache.make_key("GET", url, {})
    cache.set(key, url, {"stockNumber": 10000})
    with cache._connect() as db:
        db.execute("UPDATE http_cache SET fetched_at=0")
    cache._memory.clear()
    client.session.get = Mock(side_effect=requests.Timeout())
    with pytest.raises(SZLCSCError):
        client._get_json(url, ttl=1)


def test_concurrent_cache_misses_share_one_upstream_fetch(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.session.get = Mock(
        side_effect=lambda *args, **kwargs: response(
            200, {"code": 200, "result": {"value": 1}}
        )
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(
            pool.map(
                lambda _: client._get_json("https://so.szlcsc.com/test"), range(16)
            )
        )
    assert rows == [{"value": 1}] * 16
    assert client.session.get.call_count == 1


def test_exact_lookup_never_accepts_single_fuzzy_result_or_ambiguous_mpn(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.search = Mock(return_value=[Part(lcsc_code="C2", mpn="ABC2")])
    with pytest.raises(SZLCSCError) as caught:
        client.get_part("C1")
    assert caught.value.code == "PART_NOT_FOUND"
    client.search = Mock(
        return_value=[Part(lcsc_code="C1", mpn="ABC"), Part(lcsc_code="C2", mpn="ABC")]
    )
    with pytest.raises(SZLCSCError) as caught:
        client.get_part("ABC")
    assert caught.value.code == "AMBIGUOUS_PART"


def test_static_enrichment_cannot_overwrite_live_stock_or_part_identity():
    live = Part(
        lcsc_code="C1",
        mpn="ABC",
        stock=20,
        price_tiers=[PriceTier(1, 1.2)],
        small_batch_price_cny=1.2,
    )
    merge_part(
        live,
        Part(lcsc_code="C1", mpn="ABC", stock=999, price_tiers=[PriceTier(1, 0.1)]),
        dynamic=False,
    )
    assert live.stock == 20 and live.small_batch_price_cny == 1.2
    merge_part(live, Part(lcsc_code="C2", mpn="XYZ", stock=999))
    assert live.lcsc_code == "C1" and live.mpn == "ABC" and live.stock == 20


def test_c_code_has_priority_over_an_mpn_that_looks_like_a_c_code(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.search = Mock(
        return_value=[Part(lcsc_code="C2", mpn="C1"), Part(lcsc_code="C1", mpn="RIGHT")]
    )
    assert client._find_exact("C1").mpn == "RIGHT"
    client.search.return_value = [Part(lcsc_code="C2", mpn="C1")]
    assert client._find_exact("C1") is None


def test_exact_resolution_only_enriches_the_selected_component(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.search = Mock(
        return_value=[
            Part(lcsc_code="C1", mpn="MATCH", category_id=1),
            Part(lcsc_code="C2", mpn="OTHER", category_id=2),
        ]
    )
    client._category_exact = Mock(return_value=Part(lcsc_code="C1", stock=5))
    assert client.get_part("MATCH", include_static=False).stock == 5
    client.search.assert_called_once_with("MATCH", max_results=20, enrich=False)
    client._category_exact.assert_called_once_with(1, "C1")


def test_usd_tier_cannot_become_domestic_cny_price():
    part = part_from_dict(
        {"code": "C1", "priceList": [{"minQty": 1, "price": 9, "currency": "USD"}]}
    )
    assert part.small_batch_price_cny is None
    assert part.price_tiers == []


def test_attachment_endpoint_extracts_a_nested_relative_pdf(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.session.get = Mock(
        return_value=response(
            200,
            {"code": 200, "result": {"files": [{"pdfUrl": "upload/public/test.pdf"}]}},
        )
    )
    assert (
        client._annex_datasheet("C1", "annex-id")
        == "https://atta.szlcsc.com/upload/public/test.pdf"
    )


def test_write_tools_are_serialized_while_protocol_errors_do_not_poison_the_lock():
    from fastmcp import FastMCP

    from szlcsc_mcp.runtime import SourcingMiddleware

    server = FastMCP("write-test", middleware=[SourcingMiddleware()])
    active, maximum = 0, 0

    @server.tool()
    async def mutate_manifest() -> dict:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"ok": True}

    async def run():
        async with Client(server) as client:
            results = await asyncio.gather(
                *(client.call_tool("mutate_manifest") for _ in range(4))
            )
            assert all(not r.is_error for r in results)

    asyncio.run(run())
    assert maximum == 1


def test_lifespan_closes_all_upstream_sessions(monkeypatch):
    from szlcsc_mcp import server_base, server_sourcing

    domestic, eda, assembly = Mock(), Mock(), Mock()
    monkeypatch.setattr(server_base.client, "close", domestic)
    monkeypatch.setattr(server_base.eda_client, "close", eda)
    monkeypatch.setattr(server_sourcing.jlc_client, "close", assembly)

    async def run():
        async with server_base._lifespan(None):
            pass

    asyncio.run(run())
    domestic.assert_called_once()
    eda.assert_called_once()
    assembly.assert_called_once()


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "1e999"])
def test_numeric_extremes_do_not_crash_or_emit_invalid_json(raw):
    assert as_int(raw) is None
    assert as_float(raw) is None


def test_scientific_notation_and_unsafe_datasheet_scheme():
    assert as_float("1e-5") == 0.00001
    assert normalize_datasheet_url("file:///C:/Windows/win.ini") is None


def test_memory_cache_is_bounded_and_does_not_share_mutable_values(tmp_path):
    cache = SqliteCache(tmp_path / "cache.db", memory_size=2)
    for i in range(4):
        cache.set(str(i), "url", {"items": []})
    result = cache.get("3", 10)
    result["items"].append("mutated")
    assert cache.get("3", 10) == {"items": []}
    assert cache.status()["memory_entries"] == 2
    cache.clear()
    assert cache.get("3", 10) is None


def test_explicit_refresh_bypasses_snapshot_and_reports_provenance(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    calls = []

    def get(*args, **kwargs):
        calls.append(args[0])
        return response(
            200,
            {
                "code": 200,
                "result": {
                    "productList": [
                        {"code": "C1", "stockNumber": len(calls), "model": "PART"}
                    ]
                },
            },
        )

    client.session.get = get
    first = client.get_part("C1", include_static=False)
    cached = client.get_part("C1", include_static=False)
    fresh = client.get_part("C1", refresh=True, include_static=False)
    assert first.stock == cached.stock == 1 and fresh.stock == 2
    assert cached.data_freshness["cache_hits"] == 1
    assert fresh.data_freshness["refresh_requested"] is True
    assert fresh.data_freshness["cache_hits"] == 0


def test_real_sdk_reports_legacy_failure_and_unexpected_error_without_dying(
    monkeypatch,
):
    from szlcsc_mcp import server_base
    from szlcsc_mcp.server import mcp

    async def run():
        async with Client(mcp) as client:
            monkeypatch.setattr(
                server_base.client,
                "get_part",
                Mock(side_effect=SZLCSCError("not found", code="PART_NOT_FOUND")),
            )
            result = await client.call_tool(
                "szlcsc_get_part", {"identifier": "C1"}, raise_on_error=False
            )
            assert result.is_error is True
            assert result.structured_content["error"]["code"] == "PART_NOT_FOUND"
            monkeypatch.setattr(
                server_base.client,
                "get_part",
                Mock(side_effect=AttributeError("secret internal detail")),
            )
            result = await client.call_tool(
                "szlcsc_get_part", {"identifier": "C1"}, raise_on_error=False
            )
            assert result.is_error is True
            assert result.structured_content["error"]["code"] == "INTERNAL_ERROR"
            assert "secret internal detail" not in str(result)
            result = await client.call_tool("szlcsc_cache_status")
            assert not result.is_error

    asyncio.run(run())


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"identifier": None},
        {"identifier": ""},
        {"identifier": 12},
        {"identifier": "C1", "surprise": True},
    ],
)
def test_real_sdk_rejects_invalid_identifiers_before_network(monkeypatch, arguments):
    from szlcsc_mcp import server_base
    from szlcsc_mcp.server import mcp

    lookup = Mock(side_effect=AssertionError("network must not be called"))
    monkeypatch.setattr(server_base.client, "get_part", lookup)

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(
                "szlcsc_get_part", arguments, raise_on_error=False
            )
            assert result.is_error

    asyncio.run(run())
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "X", "max_results": 0},
        {"query": "X", "max_results": 51},
        {"query": "X", "page": -1},
        {"query": "X", "min_price": -1},
    ],
)
def test_search_boundary_schema(arguments):
    from szlcsc_mcp.server import mcp

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(
                "szlcsc_search", arguments, raise_on_error=False
            )
            assert result.is_error

    asyncio.run(run())


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("szlcsc_parametric_search", {"query": "voltage reference", "constraints": {}}),
        ("szlcsc_get_pricing", {"identifier": "C1", "quantity": 0}),
        ("szlcsc_download_parts", {"identifiers": []}),
        ("szlcsc_sync_datasheets", {"identifiers": ["C1"] * 201}),
        ("szlcsc_altium_smoke_test", {"identifiers": ["C1"] * 6}),
    ],
)
def test_constraints_quantity_and_batch_reject_before_work(name, arguments):
    from szlcsc_mcp.server import mcp

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
            assert result.is_error
            assert result.structured_content["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())


def test_success_output_satisfies_advertised_schema_and_resource_prompt_work(
    monkeypatch,
):
    from szlcsc_mcp import server_sourcing
    from szlcsc_mcp.server import mcp

    part = Part(
        lcsc_code="C1",
        stock=0,
        min_order_qty=5,
        order_multiple=5,
        price_tiers=[PriceTier(1, 0.2), PriceTier(10, 0.1)],
    )
    lookup = Mock(return_value=part)
    monkeypatch.setattr(server_sourcing.client, "get_part", lookup)

    async def run():
        async with Client(mcp) as client:
            tools = {t.name: t for t in await client.list_tools()}
            result = await client.call_tool(
                "szlcsc_get_pricing", {"identifier": "C1", "quantity": 6}
            )
            assert result.structured_content["order_quantity"] == 10
            assert result.structured_content["unit_price_cny"] == 0.1
            validate(
                result.structured_content, tools["szlcsc_get_pricing"].output_schema
            )
            result = await client.call_tool("szlcsc_get_stock", {"identifier": "C1"})
            assert result.structured_content["in_stock"] is False
            assert (
                result.structured_content["inventory_pool"] == "SZLCSC_domestic_retail"
            )
            assert (
                tools["szlcsc_search"].output_schema is None
            )  # legacy JSON strings are not declared as objects
            assert await client.read_resource("szlcsc://sourcing-guide")
            assert await client.get_prompt(
                "select_domestic_component", {"requirements": "RS485"}
            )

    asyncio.run(run())
    assert lookup.call_args.kwargs["refresh"] is True
