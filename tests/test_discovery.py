from unittest.mock import Mock

import pytest

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient, SZLCSCError
from szlcsc_mcp.models import Part
from szlcsc_mcp.suppliers import DomesticProvider


def client_for(tmp_path, rows):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client._get_json = Mock(side_effect=lambda url, params, **kw: {
        "productList": rows.get((params["keyword"], params["currPage"]), []),
    })
    client.get_part = Mock(side_effect=lambda code, **kw: Part(lcsc_code=code, mpn=code, stock=10))
    return client


def can(code, model, package):
    return {"code": code, "model": model, "standard": package, "name": "Isolated CAN transceiver"}


def test_requirement_discovery_expands_family_and_keeps_different_pin_count_variants(tmp_path):
    client = client_for(tmp_path, {
        ("隔离式CAN收发器", 1): [can("C3234990", "ISO1042QDWVRQ1", "SOIC-8")],
        ("ISO1042", 1): [can("C3234990", "ISO1042QDWVRQ1", "SOIC-8"),
                            can("C2671569", "ISO1042QDWRQ1", "SOIC-16-300mil")],
    })
    try:
        result = client.discover("AEC-Q100 isolated CAN", max_results=2, enrich=False)
        assert {p["part"].lcsc_code for p in result["results"]} == {"C3234990", "C2671569"}
        assert any(q["query"] == "ISO1042" for q in result["coverage"]["queries"])
        assert result["coverage"]["complete"] is False
        for row in result["results"]:
            qualification = next(r for r in row["requirements"] if r["requirement"] == "AEC-Q100")
            assert qualification["status"] == "unknown"
    finally:
        client.close()


def test_discovery_checks_later_upstream_page_before_claiming_no_candidates(tmp_path):
    client = client_for(tmp_path, {
        ("CW1573", 1): [{"code": f"C{i}", "model": "OTHER"} for i in range(50)],
        ("CW1573", 2): [{"code": "C53199075", "model": "CW1573DLCS"}],
    })
    try:
        result = client.discover("CW1573", enrich=False)
        assert [r["part"].mpn for r in result["results"]] == ["CW1573DLCS"]
        assert result["results"][0]["exact_identity"] is False
        assert len(result["coverage"]["queries"]) == 2
        # Exact lookup still cannot substitute a family variant.
        with pytest.raises(SZLCSCError, match="family/variant"):
            SZLCSCClient.get_part(client, "CW1573")
    finally:
        client.close()


def test_partial_query_errors_preserve_candidates_with_explicit_coverage(tmp_path):
    client = client_for(tmp_path, {})
    original = client.search

    def search(query, **kw):
        if query == "isolated CAN":
            raise SZLCSCError("unavailable", code="NETWORK_ERROR")
        return original(query, **kw)

    client.search = Mock(side_effect=search)
    client._get_json = Mock(return_value={"productList": [can("C1", "ISO1042QDWRQ1", "SOIC-16")]})
    try:
        result = client.discover("isolated CAN", enrich=False)
        assert result["results"]
        assert result["coverage"]["query_errors"][0]["code"] == "NETWORK_ERROR"
        assert result["coverage"]["complete"] is False
    finally:
        client.close()


def test_discovery_never_applies_legacy_stock_filters_before_current_lookup(tmp_path):
    client = client_for(tmp_path, {("CW1573", 1): [{"code": "C53199075", "model": "CW1573DLCS", "stockNumber": 0}]})
    try:
        result = client.discover("CW1573", in_stock=True)
        assert result["results"][0]["part"].stock == 10
        assert all(call.args[1]["stock"] is False for call in client._get_json.call_args_list)
    finally:
        client.close()


def test_access_limit_stops_query_expansion_instead_of_switching_terms(tmp_path):
    client = client_for(tmp_path, {})
    client.search = Mock(side_effect=SZLCSCError("limited", code="RATE_LIMITED"))
    try:
        result = client.discover("isolated CAN")
        assert result["results"] == []
        assert result["coverage"]["stopped_on_access_limit"] is True
        assert result["coverage"]["query_errors"][0]["code"] == "RATE_LIMITED"
        assert client.search.call_count == 1
    finally:
        client.close()


def test_real_thirty_row_page_is_not_treated_as_exhausted_fifty_row_request(tmp_path):
    client = client_for(tmp_path, {
        ("CW1573", 1): [{"code": f"C{i}", "model": "OTHER"} for i in range(30)],
        ("CW1573", 2): [{"code": "C53199075", "model": "CW1573DLCS"}],
    })
    try:
        result = client.discover("CW1573", enrich=False)
        assert result["count"] == 1
        assert result["coverage"]["page_size"] == 30
        assert result["coverage"]["queries"][0]["has_more"] is True
    finally:
        client.close()


def test_partial_access_limit_keeps_candidates_without_more_detail_requests(tmp_path):
    client = client_for(tmp_path, {})
    cached = Part(lcsc_code="C123", mpn="ISO1042A", stock=99)
    client.search = Mock(side_effect=[[cached], SZLCSCError("limit", code="RATE_LIMITED")])
    try:
        result = client.discover("ISO1042", family_queries=["ISO1042A"])
        assert result["coverage"]["stopped_on_access_limit"] and result["count"] == 1
        assert result["results"][0]["part"].stock is None
        client.get_part.assert_not_called()
    finally:
        client.close()


def test_exact_code_stops_early_while_mpn_checks_later_pages(tmp_path):
    from szlcsc_mcp.client import SearchResults

    client = client_for(tmp_path, {})
    first = SearchResults([Part(lcsc_code="C123", mpn="PART10")], coverage={"has_more": True})
    second = SearchResults([Part(lcsc_code="C456", mpn="PART10")], coverage={"has_more": False})
    try:
        client.search = Mock(side_effect=[first])
        assert client._find_exact("c123").lcsc_code == "C123"
        assert client.search.call_count == 1
        client.search = Mock(side_effect=[first, second])
        with pytest.raises(SZLCSCError) as exc:
            client._find_exact("PART10")
        assert exc.value.code == "AMBIGUOUS_PART" and client.search.call_count == 2
    finally:
        client.close()


def paged_client(tmp_path):
    client = client_for(tmp_path, {})
    rows = [{"code": f"C{i}", "model": f"PART{i:03}", "name": "catalog part"} for i in range(60)]
    def get(url, params, **kw):
        start = (params["currPage"] - 1) * 30
        return {"productList": rows[start:start + 30], "total": len(rows)}
    client._get_json = Mock(side_effect=get)
    return client


def test_search_cursor_preserves_every_candidate_inside_upstream_windows(tmp_path):
    client = paged_client(tmp_path)
    codes = []
    cursor = {"page": 1, "result_offset": 0}
    try:
        while cursor:
            result = client.search("part", max_results=7, enrich=False, **cursor)
            codes.extend(p.lcsc_code for p in result)
            cursor = result.coverage["next_cursor"]
        assert len(codes) == len(set(codes)) == 60
        assert set(codes) == {f"C{i}" for i in range(60)}
    finally:
        client.close()


@pytest.mark.parametrize("limit", [7, 10, 50])
def test_supplier_pagination_does_not_skip_the_rest_of_a_thirty_row_window(tmp_path, limit):
    client = paged_client(tmp_path)
    provider = DomesticProvider(client)
    codes = []
    try:
        for page in range(1, (60 + limit - 1) // limit + 1):
            result = provider.search("part", page=page, limit=limit)
            codes.extend(r.offers[0].supplier_sku for r in result.records)
        assert len(codes) == len(set(codes)) == 60
        assert set(codes) == {f"C{i}" for i in range(60)}
    finally:
        client.close()


def test_empty_unknown_total_page_does_not_create_an_automatic_scan_to_page_limit(tmp_path):
    client = client_for(tmp_path, {})
    try:
        result = client.search("missing", enrich=False)
        assert result.coverage["next_cursor"] is None
        assert result.coverage["complete"] is False
    finally:
        client.close()


def test_verified_electrical_match_wins_before_output_cap(tmp_path):
    client = client_for(tmp_path, {})
    wrong = Part(lcsc_code="C1", mpn="REF1001", name="电压基准")
    right = Part(lcsc_code="C2", mpn="REF2001", name="电压基准")
    client.search = Mock(return_value=[wrong, right])
    wrong_detail = Part(lcsc_code="C1", mpn="REF1001", name="电压基准", parameters={"输出电压": "5V"})
    right_detail = Part(lcsc_code="C2", mpn="REF2001", name="电压基准", parameters={"输出电压": "3.3V"})
    client.get_part = Mock(side_effect={"C1": wrong_detail, "C2": right_detail}.__getitem__)
    try:
        result = client.discover("电压基准", max_results=1, max_queries=1, constraints={"output_voltage": "=3.3V"})
        assert [row["part"].mpn for row in result["results"]] == ["REF2001"]
        assert result["results"][0]["constraint_match"]["all_matched"]
    finally:
        client.close()
