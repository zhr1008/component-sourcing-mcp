from __future__ import annotations

import re
import math
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from .models import Part, PriceTier


def first(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return None


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        text = re.sub(r"[^0-9.eE+\-]", "", str(value))
        result = float(text) if text else None
        return result if result is not None and math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize_c_code(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "")
    if re.fullmatch(r"[cC]\d+", compact):
        return "C" + compact[1:]
    return (value or "").strip()



_SZLCSC_DATASHEET_BASE = "https://atta.szlcsc.com/"


def normalize_image_url(value: Any) -> str | None:
    """Select one HTTP(S) product image from SZLCSC's `<$>` gallery field."""
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str):
            continue
        for candidate in item.split("<$>"):
            candidate = candidate.strip()
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            try:
                parsed = urlparse(candidate)
                if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                    return candidate
            except ValueError:
                continue
    return None


def normalize_datasheet_url(value: Any) -> str | None:
    """Normalize SZLCSC datasheet references to a safe absolute URL.

    SZLCSC sometimes returns attachment values as relative paths such as
    ``upload/public/pdf/source/...pdf``. Those paths are served by the official
    attachment CDN at atta.szlcsc.com. Absolute HTTP(S) vendor URLs are preserved
    for the downloader's host allowlist to validate later.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("//"):
        return "https:" + text
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        return text
    if parsed.scheme:
        return None
    # Official SZLCSC attachment responses commonly omit both scheme and host.
    # Resolve relative PDF references against the attachment CDN, not item.szlcsc.com.
    if text.lower().endswith(".pdf") or text.lstrip("/").startswith("upload/"):
        return urljoin(_SZLCSC_DATASHEET_BASE, text.lstrip("/"))
    return None

def normalize_price_tiers(raw: Any) -> list[PriceTier]:
    if isinstance(raw, dict):
        raw = raw.get("list") or raw.get("priceList") or raw.get("prices") or list(raw.values())
    if not isinstance(raw, list):
        return []
    tiers: list[PriceTier] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        qty = as_int(first(item, "startNumber", "startQty", "minQty", "min_qty", "qFrom", "qty", "number", "ladder"))
        max_qty = as_int(first(item, "endNumber", "endQty", "maxQty", "max_qty", "qTo"))
        if max_qty == -1:
            max_qty = None  # SZLCSC marks the final, unbounded tier with -1.
        price = as_float(first(item, "price", "productPrice", "priceValue", "unitPrice", "unit_price", "cnyPrice", "rmbPrice"))
        currency = str(first(item, "currency", default="CNY") or "CNY").upper()
        if currency not in {"CNY", "RMB", "¥", "￥"}:
            continue  # unit_price_cny must never contain an unconverted foreign quote.
        currency = "CNY"
        if price is not None and price < 0:
            price = None
        if qty is not None and qty <= 0:
            qty = None
        if qty is not None and max_qty is not None and max_qty < qty:
            continue
        if qty is None and price is None:
            continue
        tiers.append(PriceTier(min_qty=qty, unit_price_cny=price, max_qty=max_qty, currency=currency))
    tiers.sort(key=lambda x: (x.min_qty is None, x.min_qty or 10**18))
    return tiers


def small_batch_price(tiers: Iterable[PriceTier]) -> float | None:
    """Use the smallest-quantity CNY tier (normally 1+) rather than bulk pricing."""
    usable = [t for t in tiers if t.unit_price_cny is not None and t.min_qty is not None and t.min_qty > 0
              and t.currency in {"CNY", "RMB", "¥", "￥"}]
    if not usable:
        return None
    usable.sort(key=lambda x: (x.min_qty is None, x.min_qty or 10**18))
    return usable[0].unit_price_cny


def _params_from_detail(data: dict[str, Any]) -> dict[str, Any]:
    raw = first(data, "param", "params", "parameter", "parameters", "attribute", "attributes", default={})
    if isinstance(raw, dict):
        return {str(k).strip(): v for k, v in raw.items() if str(k).strip()}
    if isinstance(raw, list):
        out: dict[str, Any] = {}
        for row in raw:
            if isinstance(row, dict):
                k = first(row, "name", "paramName", "key", "label", "attributeName")
                v = first(row, "value", "paramValue", "val", "attributeValue")
                if k:
                    out[str(k).strip()] = v
        return out
    return {}


def _warehouse_stock(data: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    aliases = {
        "JS": ("jsWarehouseStockNumber", "jsStock", "whs-js"),
        "GD": ("gdWarehouseStockNumber", "gdStock", "whs-gd"),
        "ZH": ("zhWarehouseStockNumber", "zhStock", "whs-zh"),
        "HK": ("hkWarehouseStockNumber", "hkStock", "whs-hk"),
    }
    for name, keys in aliases.items():
        val = as_int(first(data, *keys))
        if val is not None:
            out[name] = val
    raw = first(data, "warehouseStock", "warehouseStocks", default=None)
    if isinstance(raw, dict):
        for k, v in raw.items():
            val = as_int(v)
            if val is not None:
                out[str(k)] = val
    return out


def _positive_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = as_int(data.get(key))
        if value is not None and value > 0:
            return value
    return None


def part_from_dict(data: dict[str, Any]) -> Part:
    tiers = normalize_price_tiers(first(data, "priceList", "productPriceList", "prices", "ladderPrice", default=[]))
    product_id = as_int(first(data, "id", "productId", "product_id"))
    code = first(data, "code", "productCode", "lcscCode", "lcsc_code", "number")
    code = normalize_c_code(str(code)) if code else None
    return Part(
        lcsc_code=code,
        mpn=first(data, "model", "productModel", "mpn", "mfrPart", "manufacturerPartNumber"),
        name=first(data, "name", "productName", "title", "description"),
        brand=first(data, "brandName", "brand", "manufacturer", "manufacturerName"),
        package=first(data, "standard", "package", "packageName", "encapsulation", "encapsulationModel"),
        category_id=as_int(first(data, "catalogId", "categoryId")),
        category=first(data, "catalogName", "categoryName", "category"),
        product_id=product_id,
        product_sign_id=first(data, "productSignId", "product_sign_id", "signId"),
        stock=as_int(first(data, "stockNumber", "stock", "inventory", "quantity")),
        total_stock=as_int(first(data, "totalStockNumber", "totalStock", "total_stock", "totalQuantity")),
        stock_status=first(data, "productStockStatus", "stockStatus"),
        warehouse_stock=_warehouse_stock(data),
        price_tiers=tiers,
        small_batch_price_cny=small_batch_price(tiers),
        # minWholeNumber/packageNumber describe a whole reel/tray, not the
        # minimum split purchase. If the API omits MOQ, retain unknown.
        min_order_qty=_positive_int(data, "minBuyNumber", "minOrderQty", "moq", "min_order_qty"),
        full_pack_qty=_positive_int(data, "minWholeNumber", "packageNumber", "full_pack_qty"),
        order_multiple=_positive_int(data, "splitRatio", "orderMultiple", "order_multiple"),
        packaging=first(data, "packageUnit", "packaging", "arrangeName", "packageType"),
        image_url=normalize_image_url(first(data, "image", "imageUrl", "productImage", "productImageUrl")),
        datasheet_url=normalize_datasheet_url(first(data, "datasheet", "datasheetUrl", "pdfUrl", "pdf")),
        product_url=(f"https://item.szlcsc.com/{product_id}.html" if product_id else None),
        parameters=_params_from_detail(data),
    )


def merge_part(base: Part, extra: Part, *, dynamic: bool = True) -> Part:
    """Merge non-empty fields from extra while retaining known base identity."""
    if base.lcsc_code and extra.lcsc_code and base.lcsc_code.casefold() != extra.lcsc_code.casefold():
        return base
    if base.mpn and extra.mpn and base.mpn.casefold() != extra.mpn.casefold():
        return base
    for field_name in (
        "lcsc_code", "mpn", "name", "brand", "package", "category_id", "category",
        "product_id", "product_sign_id", "stock", "total_stock", "stock_status",
        "min_order_qty", "full_pack_qty", "order_multiple", "packaging", "image_url", "datasheet_url", "product_url",
    ):
        value = getattr(extra, field_name)
        if not dynamic and field_name in {"stock", "total_stock", "stock_status", "min_order_qty", "order_multiple"}:
            # Explicit page/detail purchase metadata can fill an unknown MOQ,
            # but must not overwrite known values from the fresh API response.
            if field_name not in {"min_order_qty", "order_multiple"} or getattr(base, field_name) is not None:
                continue
        if value not in (None, "", []):
            setattr(base, field_name, value)
    if dynamic and extra.warehouse_stock:
        base.warehouse_stock.update(extra.warehouse_stock)
    if dynamic and extra.price_tiers:
        base.price_tiers = extra.price_tiers
        base.small_batch_price_cny = extra.small_batch_price_cny
    if extra.parameters:
        base.parameters.update(extra.parameters)
    return base
