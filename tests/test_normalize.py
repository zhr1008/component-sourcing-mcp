import json
from pathlib import Path

from szlcsc_mcp.normalize import part_from_dict

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_small_batch_price_uses_first_tier_not_bulk():
    raw = load("category.json")["productList"][0]
    p = part_from_dict(raw)
    assert p.small_batch_price_cny == 1.35
    assert p.stock == 3210
    assert p.in_stock is True


def test_missing_price_and_stock_are_unknown():
    p = part_from_dict({"code": "C1", "model": "X"})
    assert p.small_batch_price_cny is None
    assert p.stock is None
    assert p.in_stock is None


def test_relative_datasheet_url_is_normalized_to_szlcsc_attachment_cdn():
    from szlcsc_mcp.normalize import normalize_datasheet_url

    assert normalize_datasheet_url(
        "upload/public/pdf/source/20250911/7AD593C71E111FA457F06F9E865C29ED.pdf"
    ) == "https://atta.szlcsc.com/upload/public/pdf/source/20250911/7AD593C71E111FA457F06F9E865C29ED.pdf"
    assert normalize_datasheet_url(
        "/upload/public/pdf/source/20210621/C2040_96F425825606B7957D87DD32E2512CA9.pdf"
    ) == "https://atta.szlcsc.com/upload/public/pdf/source/20210621/C2040_96F425825606B7957D87DD32E2512CA9.pdf"
    assert normalize_datasheet_url("https://www.ti.com/lit/ds/symlink/opa333.pdf") == "https://www.ti.com/lit/ds/symlink/opa333.pdf"


def test_non_url_relative_datasheet_scalar_is_not_accepted():
    from szlcsc_mcp.normalize import normalize_datasheet_url
    assert normalize_datasheet_url("123456") is None


def test_product_gallery_is_one_real_url_not_a_joined_request():
    from szlcsc_mcp.normalize import normalize_image_url

    first = "https://alimg.szlcsc.com/first.jpg"
    assert part_from_dict({"image": first + "<$>https://alimg.szlcsc.com/second.jpg"}).image_url == first
    assert normalize_image_url([None, "javascript:alert(1)", "//alimg.szlcsc.com/a.png"]) == "https://alimg.szlcsc.com/a.png"
    assert normalize_image_url("<$>  " + first) == first
    assert normalize_image_url({"url": first}) is None
    assert normalize_image_url("https://user:secret@alimg.szlcsc.com/a.jpg") is None
