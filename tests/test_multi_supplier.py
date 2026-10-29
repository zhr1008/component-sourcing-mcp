"""Official-contract mocks, real transport preparation and supplier isolation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from fastmcp import Client
import pytest
import requests

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient
from szlcsc_mcp.digikey import DigiKeyClient
from szlcsc_mcp.errors import SourcingError
from szlcsc_mcp.mouser import MouserClient
from szlcsc_mcp.normalize import part_from_dict
from szlcsc_mcp.supplier_http import DailyBudget
from szlcsc_mcp.supplier_models import (
    ComponentIdentity,
    ComponentRecord,
    PriceBreak,
    SupplierOffer,
    SupplierPage,
    amount,
    integer,
)
from szlcsc_mcp.suppliers import (
    DomesticProvider,
    SupplierRegistry,
    UnavailableProvider,
    build_registry,
)


@pytest.fixture(autouse=True)
def supplier_environment(monkeypatch):
    import szlcsc_mcp.http as http

    for key in (
        "COMPONENT_SUPPLIERS_CONFIG",
        "MOUSER_API_KEY",
        "DIGIKEY_CLIENT_ID",
        "DIGIKEY_CLIENT_SECRET",
        "DIGIKEY_ACCESS_TOKEN",
        "DIGIKEY_SANDBOX",
        "DIGIKEY_SITE",
        "DIGIKEY_CURRENCY",
        "DIGIKEY_LANGUAGE",
        "DIGIKEY_ACCOUNT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(http, "_HOSTS", {})
    monkeypatch.setattr(http.time, "sleep", lambda _: None)


def mouser_part(**values):
    return {
        "MouserPartNumber": "123-ABC",
        "ManufacturerPartNumber": "ABC",
        "Manufacturer": "Fixture Semiconductor",
        "Description": "Reference component",
        "AvailabilityInStock": "1,234",
        "Availability": "1234 In Stock",
        "Min": "5",
        "Mult": "5",
        "ProductAttributes": [
            {"AttributeName": "Package / Case", "AttributeValue": "SOT-23-5"}
        ],
        "PriceBreaks": [
            {"Quantity": 1, "Price": "$0.1234", "Currency": "USD"},
            {"Quantity": 10, "Price": "$0.1000", "Currency": "USD"},
        ],
        "ProductDetailUrl": "https://www.mouser.com/ProductDetail/fixture",
        **values,
    }


def mouser_payload(parts=None, **values):
    return {
        "Errors": [],
        "SearchResults": {
            "Parts": [mouser_part()] if parts is None else parts,
            "NumberOfResult": 1,
        },
        **values,
    }


def digikey_product():
    return {
        "ManufacturerProductNumber": "ABC0",
        "Manufacturer": {"Id": 1, "Name": "Fixture Semiconductor"},
        "Description": {"ProductDescription": "Reference component"},
        "QuantityAvailable": 9999,
        "Parameters": [{"ParameterText": "Package / Case", "ValueText": "SOT-23-5"}],
        "ProductVariations": [
            {
                "DigiKeyProductNumber": "ABC0CT-ND",
                "PackageType": {"Name": "Cut Tape"},
                "QuantityAvailableforPackageType": 12,
                "MinimumOrderQuantity": 1,
                "StandardPackage": 3000,
                "StandardPricing": [
                    {"BreakQuantity": 1, "UnitPrice": 0.25, "TotalPrice": 0.25}
                ],
                "MyPricing": [
                    {"BreakQuantity": 1, "UnitPrice": 0.20, "TotalPrice": 0.20}
                ],
            }
        ],
    }


def transport(monkeypatch, callback):
    calls = []

    def send(adapter, request, **kwargs):
        calls.append((request, kwargs))
        logging.getLogger("urllib3.connectionpool").debug(
            "request %s headers %s", request.url, request.headers
        )
        status, payload, headers = callback(request)
        response = requests.Response()
        response.status_code = status
        response.url, response.request = request.url, request
        response._content = json.dumps(payload).encode()
        response._content_consumed = True
        response.headers.update(headers)
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return calls


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$0.012300", "0.012300"),
        ("1,234.56", "1234.56"),
        ("0,12", None),
        ("1.234,56", None),
        ("NaN", None),
        ("Infinity", None),
        (-1, None),
        (True, None),
    ],
)
def test_money_retains_decimal_precision_and_rejects_ambiguous_prices(raw, expected):
    assert amount(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("unknown", None),
        (0, 0),
        ("1,234", 1234),
        (True, None),
        ("10 available", None),
    ],
)
def test_inventory_missing_is_unknown(raw, expected):
    assert integer(raw) == expected


def test_mouser_exact_contract_cache_and_credential_redaction(
    monkeypatch, tmp_path, caplog
):
    key = "fixture-credential-" + "mouser"
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = MouserClient(cache, api_key=key)
    caplog.set_level(logging.DEBUG, logger="urllib3.connectionpool")
    raw = mouser_part(
        Description="echo " + key,
        UIConfig={"unused": "large"},
        DataSheetUrl="https://example.com/doc?apiKey=" + key,
    )
    calls = transport(monkeypatch, lambda _: (200, mouser_payload([raw]), {}))
    try:
        first = api.lookup("ABC")
        assert json.loads(calls[0][0].body) == {
            "SearchByPartRequest": {
                "mouserPartNumber": "ABC",
                "partSearchOptions": "Exact",
            }
        }
        assert parse_qs(urlsplit(calls[0][0].url).query)["apiKey"] == [key]
        assert calls[0][1]["timeout"] == 15
        assert first.records[0].offers[0].stock == 1234
        assert first.records[0].offers[0].quote(6)["prices"] == [
            {"currency": "USD", "unit_price": "0.1000", "line_total": "1.0000"}
        ]
        second = api.lookup("ABC", refresh=False)
        assert second.records[0].offers[0].cached
        assert len(calls) == 1
        data = json.dumps(second.model_dump())
        assert key not in data and "UIConfig" not in data
        with cache._connect() as db:
            stored = repr(db.execute("SELECT * FROM http_cache").fetchall())
        assert key not in stored and key not in caplog.text
        assert first.records[0].component.datasheet_url == "https://example.com/doc"
        api.lookup("ABC", refresh=True)
        assert len(calls) == 2
    finally:
        api.close()


def test_mouser_keyword_pagination_and_account_cache_isolation(monkeypatch, tmp_path):
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    calls = transport(monkeypatch, lambda _: (200, mouser_payload(), {}))
    a, b = (
        MouserClient(cache, "fixture-account-one"),
        MouserClient(cache, "fixture-account-two"),
    )
    try:
        a.search("ABC", page=3, limit=5)
        body = json.loads(calls[0][0].body)["SearchByKeywordRequest"]
        assert body["startingRecord"] == 10 and body["records"] == 5
        a.search("ABC", page=3, limit=5)
        assert len(calls) == 1
        b.search("ABC", page=3, limit=5)
        assert len(calls) == 2
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize(
    "status,expected",
    [(401, "ACCESS_DENIED"), (403, "ACCESS_DENIED"), (429, "RATE_LIMITED")],
)
def test_provider_denial_does_not_retry_or_cache(
    monkeypatch, tmp_path, status, expected
):
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = MouserClient(cache, "fixture-account-value")
    calls = transport(monkeypatch, lambda _: (status, {}, {"Retry-After": "120"}))
    try:
        with pytest.raises(SourcingError) as caught:
            api.lookup("ABC")
        assert caught.value.code == expected
        assert len(calls) == 1 and cache.status()["entries"] == 0
    finally:
        api.close()


def test_business_errors_and_network_exceptions_never_echo_secrets(
    monkeypatch, tmp_path
):
    key = "fixture-private-value"
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = MouserClient(cache, key)
    calls = transport(monkeypatch, lambda _: (200, {"Errors": [{"Message": key}]}, {}))
    try:
        with pytest.raises(SourcingError) as caught:
            api.lookup("ABC")
        assert caught.value.code == "UPSTREAM_REJECTED" and key not in str(caught.value)
        assert cache.status()["entries"] == 0

        def fail(*args, **kwargs):
            raise requests.ConnectionError("https://example.com/?apiKey=" + key)

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fail)
        with pytest.raises(SourcingError) as caught:
            api.lookup("ABC")
        assert key not in str(caught.value) and caught.value.__suppress_context__
        with cache._connect() as db:
            used = db.execute("SELECT used FROM supplier_budget").fetchone()[0]
        assert used == 4  # One business rejection plus three actual network attempts.
        assert len(calls) == 1
    finally:
        api.close()


def test_redirect_is_not_followed_with_supplier_key(monkeypatch, tmp_path):
    api = MouserClient(SqliteCache(tmp_path / "cache.sqlite3"), "fixture-api-value")
    calls = transport(
        monkeypatch, lambda _: (302, {}, {"Location": "https://example.com/steal"})
    )
    try:
        with pytest.raises(SourcingError, match="unavailable"):
            api.lookup("ABC")
        assert len(calls) == 1
    finally:
        api.close()


def test_daily_budget_is_atomic_across_instances(tmp_path):
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    budgets = [DailyBudget(cache, "mouser", "fixture-scope", 2) for _ in range(2)]

    def take(i):
        try:
            budgets[i % 2].consume()
            return True
        except SourcingError as exc:
            assert exc.code == "RATE_LIMITED" and exc.retry_after > 0
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(take, range(10))) == 2
    DailyBudget(cache, "mouser", "different-fixture-scope", 1).consume()


@pytest.mark.parametrize("reuse_token", [False, True])
def test_digikey_oauth_expiry_variation_inventory_and_actual_currency(
    monkeypatch, tmp_path, reuse_token
):
    monkeypatch.setenv("DIGIKEY_ACCOUNT_ID", "0")
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = DigiKeyClient(
        cache, client_id="fixture-client-id", client_secret="fixture-client-secret"
    )
    grants = []

    def respond(request):
        if request.url.endswith("/oauth2/token"):
            grants.append(parse_qs(request.body))
            return (
                200,
                {
                    "access_token": "fixture-token-"
                    + str(1 if reuse_token else len(grants)),
                    "expires_in": 599,
                    "token_type": "Bearer",
                },
                {},
            )
        return (
            200,
            {
                "Product": digikey_product(),
                "SearchLocaleUsed": {"Currency": "EUR", "Site": "DE"},
            },
            {},
        )

    calls = transport(monkeypatch, respond)
    try:
        first = api.lookup("ABC0CT-ND")
        assert grants[0]["grant_type"] == ["client_credentials"]
        offer = first.records[0].offers[0]
        assert (
            offer.stock == 12
            and offer.min_order_quantity == 1
            and offer.order_multiple is None
        )
        assert (
            offer.pricing_basis == "account" and offer.price_breaks[0].currency == "EUR"
        )
        assert offer.quote(3)["prices"][0]["line_total"] == "0.6"
        assert (
            first.records[0].component.mpn == "ABC0"
        )  # Account ID 0 must not redact arbitrary zeros.
        api.lookup("ABC0CT-ND")
        assert len(grants) == 1
        api._expires_at = 0
        api.lookup("ABC0CT-ND")
        assert len(grants) == 2
        assert calls[-1][0].headers["Authorization"] == (
            "Bearer fixture-token-" + str(1 if reuse_token else 2)
        )
        with cache._connect() as db:
            stored = repr(db.execute("SELECT * FROM http_cache").fetchall())
        assert "fixture-token-" not in stored and "fixture-client-secret" not in stored
        assert calls[1][0].headers["X-DIGIKEY-Locale-Currency"] == "USD"
    finally:
        api.close()


def test_digikey_search_contract_and_standard_pricing(monkeypatch, tmp_path):
    api = DigiKeyClient(
        SqliteCache(tmp_path / "cache.sqlite3"),
        client_id="fixture-client-id",
        access_token="fixture-token-value",
    )
    calls = transport(
        monkeypatch,
        lambda _: (
            200,
            {
                "Products": [digikey_product()],
                "ProductsCount": 100,
                "SearchLocaleUsed": {"Currency": "USD", "Site": "US"},
            },
            {},
        ),
    )
    try:
        result = api.search("ABC0", page=3, limit=5)
        assert json.loads(calls[0][0].body) == {
            "Keywords": "ABC0",
            "Limit": 5,
            "Offset": 10,
        }
        assert result.has_more
        offer = result.records[0].offers[0]
        assert (
            offer.pricing_basis == "standard"
            and offer.price_breaks[0].unit_price == "0.25"
        )
    finally:
        api.close()


@pytest.mark.parametrize("kind", ["mouser", "digikey"])
def test_missing_credentials_never_make_a_network_request(monkeypatch, tmp_path, kind):
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = MouserClient(cache) if kind == "mouser" else DigiKeyClient(cache)
    calls = transport(monkeypatch, lambda _: pytest.fail("Unexpected HTTP call"))
    try:
        with pytest.raises(SourcingError) as caught:
            api.search("ABC")
        assert caught.value.code == "AUTHENTICATION_REQUIRED" and not calls
    finally:
        api.close()


def record(
    provider="mouser",
    *,
    mpn="ABC",
    maker="Fixture Semiconductor",
    package="SOT-23-5",
    code="123-ABC",
    currency="USD",
    stock=10,
):
    return ComponentRecord(
        component=ComponentIdentity(manufacturer=maker, mpn=mpn, package=package),
        offers=[
            SupplierOffer(
                provider=provider,
                supplier_sku=code,
                inventory_pool=provider,
                stock=stock,
                price_breaks=[
                    PriceBreak(min_quantity=1, unit_price="0.10", currency=currency)
                ],
            )
        ],
    )


class FakeProvider:
    configured = True
    registration_url = "https://example.com/"
    required_environment = []

    def __init__(self, name, rows, error=None):
        self.name, self.rows, self.error = name, rows, error

    def search(self, query, *, page=1, limit=5, refresh=False):
        if self.error:
            raise self.error
        rows = deepcopy(self.rows[(page - 1) * limit : page * limit])
        return SupplierPage(
            records=rows, total=len(self.rows), has_more=page * limit < len(self.rows)
        )

    def lookup(self, identifier, *, refresh=True):
        if self.error:
            raise self.error
        return SupplierPage(records=deepcopy(self.rows), has_more=False)

    def close(self):
        pass


def test_combined_pagination_does_not_silently_drop_fetched_results():
    registry = SupplierRegistry(
        [
            FakeProvider("mouser", [record(code=str(i)) for i in range(8)]),
            FakeProvider("digikey", [record("digikey", code=str(i)) for i in range(8)]),
        ]
    )
    first = registry.search("ABC", limit=5)
    second = registry.search("ABC", limit=5, page=2)
    assert first["count"] == 10 and second["count"] == 6
    assert {r["offers"][0]["supplier_sku"] for r in first["results"]}.isdisjoint(
        {r["offers"][0]["supplier_sku"] for r in second["results"]}
    )


def test_partial_failure_and_unexpected_errors_do_not_leak_or_hide_results():
    registry = SupplierRegistry(
        [
            FakeProvider("mouser", [record()]),
            FakeProvider("digikey", [], RuntimeError("fixture-private-secret")),
            UnavailableProvider("huaqiu"),
        ]
    )
    result = registry.search("ABC")
    assert result["ok"] and not result["complete"] and result["count"] == 1
    assert result["provider_errors"][0]["error"]["code"] == "INTERNAL_ERROR"
    assert "fixture-private-secret" not in json.dumps(result)
    assert not registry.search("ABC", providers=["huaqiu"])["ok"]


def test_exact_lookup_rejects_ambiguous_makers_and_prefers_sku():
    registry = SupplierRegistry(
        [
            FakeProvider(
                "mouser", [record(), record(maker="Other Fixture", code="456-ABC")]
            )
        ]
    )
    with pytest.raises(SourcingError) as caught:
        registry.get("mouser", "ABC")
    assert caught.value.code == "AMBIGUOUS_PART"
    assert (
        registry.get("mouser", "ABC", manufacturer="Other Fixture")["component"][
            "manufacturer"
        ]
        == "Other Fixture"
    )
    assert registry.get("mouser", "123-ABC")["offers"][0]["supplier_sku"] == "123-ABC"
    with pytest.raises(SourcingError):
        registry.get("mouser", "AB")


def domestic_fixture(monkeypatch, pages):
    client = SZLCSCClient.__new__(SZLCSCClient)
    rows = [row for page in pages for row in page]
    calls = []

    def get_json(url, params, ttl=600):
        calls.append((url, params.copy()))
        page = params["currPage"]
        return {"productList": pages[page - 1] if page <= len(pages) else []}

    def search(identifier, **kwargs):
        return [
            part_from_dict(row) for row in rows
            if identifier.casefold() in (row["model"].casefold(), row["code"].casefold())
        ]

    monkeypatch.setattr(client, "_get_json", get_json)
    monkeypatch.setattr(client, "search", search)
    return SupplierRegistry([DomesticProvider(client)]), calls


def domestic_row(code, maker, package, mpn="ABC"):
    return {"code": code, "model": mpn, "brandName": maker, "standard": package}


def test_domestic_exact_mpn_filters_and_unfiltered_ambiguity(monkeypatch):
    rows = [
        domestic_row("C1", "Maker A", "P1"),
        domestic_row("C2", "Maker B", "P2"),
        domestic_row("C3", "Maker A", "P2"),
        domestic_row("C4", "Maker A", "P1", "ABCD"),
    ]
    registry, _ = domestic_fixture(monkeypatch, [rows, []])
    found = registry.get("szlcsc", "ABC", manufacturer="maker a", package="p1")
    assert found["offers"][0]["supplier_sku"] == "C1"
    assert registry.get("szlcsc", "ABC", manufacturer="Maker B")["offers"][0][
        "supplier_sku"
    ] == "C2"
    with pytest.raises(SourcingError) as caught:
        registry.get("szlcsc", "ABC")
    assert caught.value.code == "AMBIGUOUS_PART"
    with pytest.raises(SourcingError) as caught:
        registry.get("szlcsc", "ABC", manufacturer="Maker A")
    assert caught.value.code == "AMBIGUOUS_PART"
    result = registry.offers("ABC", manufacturer="Maker B", providers=["szlcsc"])
    assert result["complete"] and result["offers"][0]["offer"]["supplier_sku"] == "C2"


@pytest.mark.parametrize("full_first_page", [False, True])
def test_domestic_incomplete_window_never_claims_unique_match(monkeypatch, full_first_page):
    first = [domestic_row("C1", "Maker A", "P1")]
    if full_first_page:
        first.extend(
            domestic_row(f"C{i}", "Other", "P2", f"OTHER{i}") for i in range(2, 51)
        )
    registry, _ = domestic_fixture(
        monkeypatch, [first, [domestic_row("C99", "Maker A", "P1")]]
    )
    with pytest.raises(SourcingError) as caught:
        registry.get("szlcsc", "ABC", manufacturer="Maker A", package="P1")
    assert caught.value.code == "AMBIGUOUS_PART"
    result = registry.offers("ABC", providers=["szlcsc"])
    assert not result["complete"]
    assert result["provider_errors"][0]["error"]["code"] == "INCOMPLETE_RESULTS"


def test_domestic_exact_code_uses_legacy_lookup_fastpath(monkeypatch):
    registry, calls = domestic_fixture(monkeypatch, [[domestic_row("C1", "Maker A", "P1")]])
    found = registry.get("szlcsc", "C1")
    assert found["offers"][0]["supplier_sku"] == "C1"
    assert calls == []


@pytest.mark.parametrize("empty", [False, True])
def test_domestic_null_page_is_empty_only_after_the_reported_end(monkeypatch, empty):
    first = [] if empty else [domestic_row("C1", "Maker A", "P1")]
    registry, _ = domestic_fixture(monkeypatch, [first, []])
    client = registry.providers["szlcsc"].client

    def get_json(url, params, ttl=600):
        return {"productList": None if empty or params["currPage"] == 2 else first, "total": len(first)}

    monkeypatch.setattr(client, "_get_json", get_json)
    if empty:
        with pytest.raises(SourcingError) as caught:
            registry.get("szlcsc", "ABC")
        assert caught.value.code == "PART_NOT_FOUND"
    else:
        assert registry.get("szlcsc", "ABC")["offers"][0]["supplier_sku"] == "C1"


def test_domestic_null_page_before_reported_end_is_not_treated_as_complete(monkeypatch):
    registry, _ = domestic_fixture(monkeypatch, [[], []])
    monkeypatch.setattr(registry.providers["szlcsc"].client, "_get_json", lambda *a, **k: {"productList": None, "total": 1})
    with pytest.raises(SourcingError) as caught:
        registry.get("szlcsc", "ABC")
    assert caught.value.code == "INVALID_RESPONSE"


def test_component_get_passes_domestic_identity_filters(monkeypatch):
    from szlcsc_mcp import server, server_suppliers

    registry, _ = domestic_fixture(monkeypatch, [[
        domestic_row("C1", "Maker A", "P1"),
        domestic_row("C2", "Maker B", "P2"),
    ], []])
    monkeypatch.setattr(server_suppliers, "registry", registry)

    async def run():
        async with Client(server.mcp) as client:
            result = await client.call_tool("component_get", {
                "provider": "szlcsc", "identifier": "ABC",
                "manufacturer": "Maker A", "package": "P1",
            })
            assert result.structured_content["offers"][0]["supplier_sku"] == "C1"

    asyncio.run(run())


def test_offer_comparison_keeps_currencies_markets_and_unknown_identity_separate():
    assembly = record("jlcpcb", currency="USD")
    assembly.offers[0].market = "assembly"
    registry = SupplierRegistry(
        [
            FakeProvider(
                "mouser",
                [record(currency="USD"), record(currency="EUR", code="456-ABC")],
            ),
            FakeProvider("jlcpcb", [assembly]),
            FakeProvider("szlcsc", [record("szlcsc", package=None, currency="CNY")]),
        ]
    )
    result = registry.offers("ABC", quantity=3)
    assert result["count"] == 4
    assert {(g["currency"], g["market"]) for g in result["comparison_groups"]} == {
        ("USD", "retail"),
        ("EUR", "retail"),
        ("USD", "assembly"),
    }
    assert all(g["currency"] != "CNY" for g in result["comparison_groups"])
    assert result["offers"][0]["quote"]["prices"][0]["line_total"] == "0.30"
    assert "total_stock" not in result


def test_offer_moq_multiples_maximum_and_missing_price():
    offer = record().offers[0]
    offer.min_order_quantity, offer.order_multiple = 12, 5
    assert offer.quote(1)["order_quantity"] == 15
    offer.max_order_quantity = 10
    assert offer.quote(1)["prices"][0]["unit_price"] is None
    offer.price_breaks = []
    assert offer.quote(1)["prices"] == []


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "not-a-price"])
def test_poisoned_cached_prices_fail_model_validation(value):
    with pytest.raises(ValueError):
        PriceBreak(min_quantity=1, unit_price=value, currency="USD")


def test_invalid_supplier_configuration_isolated_from_other_providers(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DIGIKEY_SITE", "invalid-site")
    domestic = Mock(cache=SqliteCache(tmp_path / "cache.sqlite3"))
    registry = build_registry(domestic, Mock())
    try:
        statuses = {r["provider"]: r for r in registry.status()["providers"]}
        assert statuses["digikey"]["status"] == "configuration_error"
        assert (
            statuses["szlcsc"]["configured"]
            and statuses["mouser"]["status"] == "authentication_required"
        )
        assert (
            statuses["huaqiu"]["status"] == "unsupported"
            and statuses["huaqiu"]["registration_url"]
        )
    finally:
        registry.close()


def test_new_tools_work_through_mcp_and_survive_unconfigured_provider(monkeypatch):
    from szlcsc_mcp import server, server_suppliers

    fake = SupplierRegistry(
        [FakeProvider("mouser", [record()]), UnavailableProvider("huaqiu")]
    )
    monkeypatch.setattr(server_suppliers, "registry", fake)

    async def run():
        async with Client(server.mcp) as client:
            assert len(await client.list_tools()) == 49
            providers = await client.call_tool("component_providers")
            assert not providers.is_error
            assert await client.read_resource("components://supplier-guide")
            assert await client.get_prompt(
                "compare_supplier_offers", {"mpn": "ABC", "quantity": "3"}
            )
            found = await client.call_tool(
                "component_search", {"query": "ABC", "providers": ["mouser"]}
            )
            assert found.structured_content["count"] == 1
            invalid = await client.call_tool(
                "component_get",
                {"provider": "huaqiu", "identifier": "ABC"},
                raise_on_error=False,
            )
            assert (
                invalid.is_error
                and invalid.structured_content["error"]["code"]
                == "UNSUPPORTED_PROVIDER"
            )
            malformed = await client.call_tool(
                "component_search",
                {"query": "ABC", "providers": ["unknown"]},
                raise_on_error=False,
            )
            assert (
                malformed.is_error
                and malformed.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            )
            offers = await client.call_tool(
                "component_offers",
                {"mpn": "ABC", "quantity": 3, "providers": ["mouser"]},
            )
            assert (
                offers.structured_content["offers"][0]["quote"]["prices"][0][
                    "line_total"
                ]
                == "0.30"
            )

    asyncio.run(run())


def test_local_credential_file_and_environment_precedence(monkeypatch, tmp_path):
    file = tmp_path / "suppliers.json"
    file.write_text(
        json.dumps(
            {
                "mouser": {"api_key": "fixture-file-key"},
                "digikey": {
                    "client_id": "fixture-client",
                    "client_secret": "fixture-secret",
                    "sandbox": True,
                },
            }
        )
    )
    monkeypatch.setenv("COMPONENT_SUPPLIERS_CONFIG", str(file))
    monkeypatch.setenv("MOUSER_API_KEY", "fixture-environment-key")
    registry = build_registry(
        Mock(cache=SqliteCache(tmp_path / "cache.sqlite3")), Mock()
    )
    try:
        assert registry.providers["mouser"].api_key == "fixture-environment-key"
        assert registry.providers["digikey"].base == "https://sandbox-api.digikey.com"
        assert registry.providers["digikey"].configured
        status = json.dumps(registry.status())
        assert (
            "fixture-file-key" not in status
            and "fixture-secret" not in status
            and str(file) not in status
        )
    finally:
        registry.close()


@pytest.mark.parametrize(
    "data",
    [
        "not-json",
        json.dumps({"digikey": {"unexpected": "fixture"}}),
        json.dumps({"mouser": {"api_key": 123}}),
    ],
)
def test_malformed_private_config_does_not_disable_domestic_tools(
    monkeypatch, tmp_path, data
):
    file = tmp_path / "suppliers.json"
    file.write_text(data)
    monkeypatch.setenv("COMPONENT_SUPPLIERS_CONFIG", str(file))
    registry = build_registry(
        Mock(cache=SqliteCache(tmp_path / "cache.sqlite3")), Mock()
    )
    try:
        states = {s["provider"]: s["status"] for s in registry.status()["providers"]}
        assert (
            states["szlcsc"] == "configured"
            and states["mouser"] == states["digikey"] == "configuration_error"
        )
    finally:
        registry.close()


def test_supplier_refresh_does_not_fall_back_to_expired_price_on_failure(
    monkeypatch, tmp_path
):
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = MouserClient(cache, "fixture-api-key")
    calls = transport(monkeypatch, lambda _: (200, mouser_payload(), {}))
    try:
        api.lookup("ABC", refresh=False)
        with cache._connect() as db:
            db.execute("UPDATE http_cache SET fetched_at=0")
        cache._memory.clear()
        transport(monkeypatch, lambda _: (503, {}, {}))
        with pytest.raises(SourcingError):
            api.lookup("ABC", refresh=False)
        assert len(calls) == 1
    finally:
        api.close()


def test_single_flight_requests_share_only_the_valid_cache_entry(monkeypatch, tmp_path):
    api = MouserClient(SqliteCache(tmp_path / "cache.sqlite3"), "fixture-api-key")
    calls = transport(monkeypatch, lambda _: (200, mouser_payload(), {}))
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            records = list(
                pool.map(lambda _: api.lookup("ABC", refresh=False), range(5))
            )
        assert len(calls) == 1
        assert sum(r.records[0].offers[0].cached for r in records) == 4
    finally:
        api.close()


@pytest.mark.parametrize(
    "payload",
    [[], {"SearchResults": {"Parts": [{}]}}, {"SearchResults": {"Parts": ["invalid"]}}],
)
def test_malformed_mouser_results_are_errors_not_empty_inventory(
    monkeypatch, tmp_path, payload
):
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    api = MouserClient(cache, "fixture-api-key")
    transport(monkeypatch, lambda _: (200, payload, {}))
    try:
        with pytest.raises(SourcingError) as caught:
            api.lookup("ABC")
        assert caught.value.code == "INVALID_RESPONSE"
        assert cache.status()["entries"] == 0
    finally:
        api.close()


def test_unknown_digikey_currency_is_not_replaced_by_requested_currency(
    monkeypatch, tmp_path
):
    api = DigiKeyClient(
        SqliteCache(tmp_path / "cache.sqlite3"),
        client_id="fixture-client",
        access_token="fixture-token",
    )
    transport(
        monkeypatch,
        lambda _: (200, {"Products": [digikey_product()], "SearchLocaleUsed": {}}, {}),
    )
    try:
        with pytest.raises(SourcingError) as caught:
            api.search("ABC0")
        assert caught.value.code == "INVALID_RESPONSE"
    finally:
        api.close()


def test_common_electrical_filter_retains_matching_evidence_and_excludes_unknown_stock():
    good, wrong, unknown = (
        record(),
        record(code="wrong"),
        record(code="unknown", stock=None),
    )
    good.component.parameters = {"输出电压": "3.3V"}
    wrong.component.parameters = {"输出电压": "5V"}
    unknown.component.parameters = {"输出电压": "3.3V"}
    registry = SupplierRegistry([FakeProvider("mouser", [good, wrong, unknown])])
    result = registry.search(
        "ABC", constraints={"output_voltage": "=3.3V"}, in_stock=True
    )
    assert (
        result["count"] == 1 and result["results"][0]["constraint_match"]["all_matched"]
    )


def test_duplicate_provider_list_and_incomplete_exact_lookup_are_rejected():
    provider = FakeProvider("mouser", [record()])
    registry = SupplierRegistry([provider])
    with pytest.raises(SourcingError):
        registry.search("ABC", providers=["mouser", "mouser"])
    provider.lookup = lambda *a, **k: SupplierPage(records=[record()], has_more=True)
    with pytest.raises(SourcingError) as caught:
        registry.get("mouser", "ABC")
    assert caught.value.code == "AMBIGUOUS_PART"
    result = registry.offers("ABC")
    assert (
        result["ok"]
        and not result["complete"]
        and result["provider_errors"][0]["error"]["code"] == "INCOMPLETE_RESULTS"
    )
