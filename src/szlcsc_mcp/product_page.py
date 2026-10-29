"""Parse only the current product's public state, never recommendations/session data."""
from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from .errors import SourcingError
from .models import Part
from .normalize import normalize_datasheet_url, part_from_dict
from .supplier_models import amount, integer


def validate_identity(raw: dict, expected: Part) -> None:
    identities = (
        (expected.lcsc_code, raw.get("productCode")),
        (expected.mpn, raw.get("productModel")),
        (expected.product_id, raw.get("productId")),
    )
    if not expected.lcsc_code or any(
        known is not None and str(known).casefold() != str(actual).casefold()
        for known, actual in identities
    ):
        raise SourcingError("Product page identity does not match the requested part", code="IDENTITY_MISMATCH")


def product_snapshot(html: str, expected: Part) -> dict[str, Any]:
    node = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        payload = json.loads(node.get_text() if node else "")
        props = payload["props"]["pageProps"]
        web = props["webData"]
        raw = web["productRecord"]
        if not isinstance(raw, dict):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise SourcingError("Product page has no supported own-product state", code="INVALID_RESPONSE") from None
    validate_identity(raw, expected)

    # Whitelist fields before caching. NEXT_DATA also contains recommendations,
    # tracking state and anonymous session attributes which must never be retained.
    fields = ("productCode", "productModel", "productId", "productName", "encapsulationModel",
              "minBuyNumber", "minWholeNumber", "splitRatio", "orderMultiple", "packageUnit")
    own = {key: raw[key] for key in fields if key in raw}
    own["stockNumber"] = integer(raw.get("validStockNumber", raw.get("stockNumber")))
    own["totalStockNumber"] = integer(web.get("totalStockNumber"))
    own["brandName"] = (web.get("brandVO") or {}).get("brandName") if isinstance(web.get("brandVO"), dict) else None
    category = web.get("currentCatalog")
    if isinstance(category, dict):
        own.update({k: category[k] for k in ("catalogId", "catalogName") if k in category})
    own["params"] = web.get("paramList") if isinstance(web.get("paramList"), list) else []
    # SEO advertises one price without a quantity interval. It is useful evidence,
    # but cannot certify a price at MOQ or fill a complete price ladder.
    shown = raw.get("isShowPrice") is True
    advertised = amount(props.get("price")) if shown else None
    own["priceList"] = raw.get("priceList", []) if shown else []
    datasheet = None
    files = raw.get("fileTypeVOList", [])
    for group in files if isinstance(files, list) else []:
        if not isinstance(group, dict) or group.get("fileType") != "pdf_property":
            continue
        rows = group.get("detailVOList", [])
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                datasheet = normalize_datasheet_url(row.get("linkAddress") or row.get("fileUrl"))
                if datasheet:
                    break
        if datasheet:
            break
    own["datasheetUrl"] = datasheet
    return {
        "product": own,
        "advertised_unit_price_cny": float(advertised) if advertised is not None else None,
        "annex_number": str(raw["pdfDESProductId"]) if raw.get("pdfDESProductId") else None,
    }


def apply_snapshot(part: Part, snapshot: dict[str, Any]) -> None:
    validate_identity(snapshot["product"], part)
    current = part_from_dict(snapshot["product"])
    raw_tiers = snapshot["product"].get("priceList", [])
    if (not isinstance(raw_tiers, list) or len(raw_tiers) != len(current.price_tiers)
            or any(t.min_qty is None or t.unit_price_cny is None for t in current.price_tiers)):
        current.price_tiers = []
        current.small_batch_price_cny = None
    changed = [name for name in ("stock", "total_stock", "min_order_qty", "small_batch_price_cny")
               if getattr(part, name) != getattr(current, name)]
    # Replace the entire procurement snapshot. Missing current fields stay unknown;
    # retaining stale warehouses/tiers would produce an internally conflicting offer.
    for name in ("stock", "total_stock", "stock_status", "min_order_qty", "order_multiple",
                 "full_pack_qty", "price_tiers", "small_batch_price_cny"):
        setattr(part, name, getattr(current, name))
    part.warehouse_stock = current.warehouse_stock.copy()
    for name in ("name", "package", "brand", "category", "category_id", "image_url", "datasheet_url"):
        if getattr(current, name) is not None:
            setattr(part, name, getattr(current, name))
    part.parameters.update(current.parameters)
    part.procurement = {
        "source": "public_product_page",
        "product_url": part.product_url,
        "quantity_price_verified": bool(current.price_tiers),
        "price_basis": "explicit_quantity_tiers" if current.price_tiers else "advertised_quantity_unspecified",
        "advertised_unit_price_cny": snapshot.get("advertised_unit_price_cny"),
        "currency": "CNY",
        "legacy_disagreement_fields": changed,
    }
    if changed:
        part.warnings.append("Current public product page superseded legacy API procurement fields")
    if not current.price_tiers:
        part.warnings.append("Public advertised price has no quantity interval; quantity quote and full ladder are unknown")


def unknown_procurement(part: Part, code: str) -> None:
    """A successful legacy request must not masquerade as current retail data."""
    for name in ("stock", "total_stock", "stock_status", "min_order_qty", "order_multiple",
                 "small_batch_price_cny", "full_pack_qty"):
        setattr(part, name, None)
    part.warehouse_stock = {}
    part.price_tiers = []
    part.procurement = {"source": "unverified", "quantity_price_verified": False, "error_code": code}
    part.warnings.append(f"Current public procurement unavailable: {code}; legacy stock/price withheld")
