import json
from pathlib import Path

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FixtureClient(SZLCSCClient):
    def __init__(self, tmp_path):
        super().__init__(cache=SqliteCache(tmp_path / "cache.sqlite3"), request_delay=0)

    def _get_json(self, url, params=None, ttl=600):
        if url.endswith("/phone/p/product/search"):
            return load("search.json")
        if "list.szlcsc.com" in url:
            return load("category.json")
        if "item.szlcsc.com/phone/p/" in url:
            return load("detail.json")
        if "icsearch.szlcsc.com" in url:
            return load("alternatives.json")
        raise AssertionError(url)

    def _enrich_from_html(self, part):
        # HTML path is separately best-effort; fixture detail already includes PDF.
        return None


def test_search_enriches_domestic_price_and_stock(tmp_path):
    c = FixtureClient(tmp_path)
    parts = c.search("TPR3333", max_results=5)
    assert len(parts) == 1
    p = parts[0]
    assert p.lcsc_code == "C5291487"
    assert p.small_batch_price_cny == 1.35
    assert p.stock == 3210
    assert p.product_url == "https://item.szlcsc.com/6034806.html"


def test_get_part_merges_parameters_and_datasheet(tmp_path):
    c = FixtureClient(tmp_path)
    p = c.get_part("C5291487")
    assert p.parameters["输出电压"] == "3.3V"
    assert p.datasheet_url == "https://atta.szlcsc.com/upload/public/datasheet.pdf"
    assert p.small_batch_price_cny == 1.35


def test_parametric_search_explains_matches(tmp_path):
    c = FixtureClient(tmp_path)
    rows = c.parametric_search(
        "TPR3333",
        {"output_voltage": "=3.3V", "accuracy": "<=0.2%", "tempco": "<=20ppm/°C"},
        max_results=3,
        fetch_pool=3,
    )
    assert len(rows) == 1
    assert rows[0]["match"]["all_matched"] is True
    assert rows[0]["part"].small_batch_price_cny == 1.35
