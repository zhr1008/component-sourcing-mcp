"""Minimal public catalog facts; no captured customer or session state."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import requests

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient, SZLCSCError
from szlcsc_mcp.models import Part, PriceTier


def page(record=None, **page_fields):
    product = {
        "productCode": "C2671569", "productModel": "ISO1042QDWRQ1", "productId": "2763484",
        "encapsulationModel": "SOIC-16-300mil", "stockNumber": 2560, "minBuyNumber": 10,
        "minWholeNumber": 2000, "theRatio": 1, "isShowPrice": True,
        "fileTypeVOList": [
            {"fileType": "certification_data_property", "detailVOList": [{"fileUrl": "upload/rohs.pdf"}]},
            {"fileType": "pdf_property", "detailVOList": [{"linkAddress": "https://www.ti.com/lit/ds/symlink/iso1042-q1.pdf"}]},
        ],
    } | (record or {})
    data = {"webData": {"productRecord": product, "totalStockNumber": 2560}, "price": 12.375} | page_fields
    return ('<a href="https://static.szlcsc.com/doc/isoiec/last/iso_iec_doc.pdf">ISO</a>'
            '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": data}}) + '</script>')


def legacy_part():
    return Part(lcsc_code="C2671569", mpn="ISO1042QDWRQ1", product_id=2763484,
                product_url="https://item.szlcsc.com/2763484.html", total_stock=0,
                warehouse_stock={"JS": 0}, min_order_qty=1,
                small_batch_price_cny=29.19, price_tiers=[PriceTier(10, 26.04)])


def html_client(tmp_path, html):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    r = requests.Response()
    r.status_code = 200
    r.url = "https://item.szlcsc.com/2763484.html"
    r._content = html.encode()
    r.encoding = "utf8"
    r._content_consumed = True
    client.session.get = Mock(return_value=r)
    return client


def test_current_public_page_replaces_legacy_stock_and_moq_but_does_not_invent_price_tiers(tmp_path):
    client = html_client(tmp_path, page())
    part = legacy_part()
    try:
        client._enrich_from_html(part)
        assert part.stock == part.total_stock == 2560
        assert part.warehouse_stock == {}  # no stale warehouse counts
        assert part.min_order_qty == 10
        assert part.price_tiers == []  # SEO price has no quantity range
        assert part.small_batch_price_cny is None
        assert part.procurement["advertised_unit_price_cny"] == 12.375
        assert part.procurement["quantity_price_verified"] is False
        assert part.datasheet_url == "https://www.ti.com/lit/ds/symlink/iso1042-q1.pdf"
    finally:
        client.close()


@pytest.mark.parametrize("record", [{"productCode": "C999"}, {"productModel": "OTHER"}, {"productId": "1"}])
def test_page_identity_conflicts_cannot_overwrite_procurement(tmp_path, record):
    client = html_client(tmp_path, page(record))
    part = legacy_part()
    try:
        with pytest.raises(SZLCSCError) as caught:
            client._enrich_from_html(part)
        assert caught.value.code == "IDENTITY_MISMATCH"
        assert part.total_stock == 0
    finally:
        client.close()


def test_focused_lookup_reads_public_procurement_even_without_static_material(tmp_path):
    client = html_client(tmp_path, page())
    client._find_exact = Mock(side_effect=lambda _: legacy_part())
    try:
        part = client.get_part("C2671569", refresh=True, include_static=False)
        assert part.stock == 2560
        assert part.min_order_qty == 10
        assert part.data_freshness["request_count"] == 1
    finally:
        client.close()


def test_no_generic_site_pdf_is_accepted_as_component_datasheet(tmp_path):
    client = html_client(tmp_path, '<a href="https://static.szlcsc.com/doc/isoiec/last/iso_iec_doc.pdf">ISO</a>')
    part = legacy_part()
    try:
        with pytest.raises(SZLCSCError):
            client._enrich_from_html(part)
        assert part.datasheet_url is None
    finally:
        client.close()


def test_failed_current_page_withholds_legacy_procurement(tmp_path):
    client = html_client(tmp_path, "unsupported page")
    client._find_exact = Mock(side_effect=lambda _: legacy_part())
    try:
        part = client.get_part("C2671569", include_static=False)
        assert part.in_stock is None
        assert part.min_order_qty is None
        assert part.small_batch_price_cny is None
        assert not part.price_tiers
        assert part.procurement["source"] == "unverified"
        assert "INVALID_RESPONSE" in " ".join(part.warnings)
    finally:
        client.close()


def test_product_cache_keeps_no_recommendations_or_session_data_and_refresh_bypasses_it(tmp_path):
    client = html_client(tmp_path, page(resourceData={"customer": "private-placeholder"},
                                         similarProductRecommend={"pdfUrl": "https://example.org/unrelated.pdf"}))
    client._find_exact = Mock(side_effect=lambda _: legacy_part())
    try:
        first = client.get_part("C2671569", include_static=False)
        cached = client.get_part("C2671569", include_static=False)
        fresh = client.get_part("C2671569", refresh=True, include_static=False)
        assert client.session.get.call_count == 2
        assert first.data_freshness["cache_hits"] == 0
        assert cached.data_freshness["cache_hits"] == 1
        assert fresh.data_freshness["refresh_requested"] is True
        with client.cache._connect() as db:
            text = " ".join(r[0] for r in db.execute("SELECT payload FROM http_cache"))
        assert "private-placeholder" not in text
        assert "unrelated.pdf" not in text
        # Cached data must pass identity checks too.
        wrong = legacy_part()
        wrong.mpn = "OTHER"
        with pytest.raises(SZLCSCError) as caught:
            client._enrich_from_html(wrong)
        assert caught.value.code == "IDENTITY_MISMATCH"
    finally:
        client.close()


def test_only_explicit_own_product_quantity_tiers_can_form_a_quote(tmp_path):
    client = html_client(tmp_path, page({"priceList": [
        {"startNumber": 10, "endNumber": 99, "price": 12.375},
        {"startNumber": 100, "endNumber": -1, "price": 11},
    ]}))
    part = legacy_part()
    try:
        client._enrich_from_html(part)
        assert [(t.min_qty, t.max_qty) for t in part.price_tiers] == [(10, 99), (100, None)]
        assert part.small_batch_price_cny == 12.375
        assert part.procurement["quantity_price_verified"] is True
    finally:
        client.close()


def test_concurrent_public_page_misses_share_the_validated_snapshot(tmp_path):
    client = html_client(tmp_path, page())
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            parts = list(pool.map(lambda _: legacy_part(), range(4)))
            list(pool.map(client._enrich_from_html, parts))
        assert client.session.get.call_count == 1
        assert all(p.stock == 2560 for p in parts)
    finally:
        client.close()


@pytest.mark.parametrize("price_list", [[{"startNumber": 10}], [{"price": 12.375}],
                                        [{"startNumber": 10, "price": 12.375}, {"startNumber": 100}],
                                        [{"startNumber": 1, "price": 10}, {"startNumber": 100, "endNumber": 10, "price": 1}]])
def test_partial_price_rows_cannot_certify_a_quantity_quote(tmp_path, price_list):
    client = html_client(tmp_path, page({"priceList": price_list}))
    part = legacy_part()
    try:
        client._enrich_from_html(part)
        assert part.procurement["quantity_price_verified"] is False
        assert part.small_batch_price_cny is None
        assert part.price_tiers == []
    finally:
        client.close()


def test_missing_product_page_never_certifies_legacy_inventory_or_prices(tmp_path):
    client = html_client(tmp_path, "")
    client._find_exact = Mock(return_value=Part(lcsc_code="C1", mpn="PART", stock=10,
                                               small_batch_price_cny=1, min_order_qty=1, full_pack_qty=2000))
    try:
        part = client.get_part("C1", include_static=False)
        assert part.in_stock is None
        assert part.min_order_qty is part.full_pack_qty is None
        assert part.small_batch_price_cny is None
        assert part.procurement["error_code"] == "PRODUCT_PAGE_MISSING"
        client.session.get.assert_not_called()
    finally:
        client.close()
