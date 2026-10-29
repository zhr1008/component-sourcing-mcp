"""Supplier-neutral identities and offers; amounts retain their original currency."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderName = Literal["szlcsc", "jlcpcb", "mouser", "digikey", "huaqiu"]


def text(value: Any, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def integer(value: Any, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    raw = str(value).strip()
    if len(raw) > 32:
        return None
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", raw):
        return None
    result = int(raw.replace(",", ""))
    return result if minimum <= result <= 10**12 else None


def amount(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    raw = str(value).strip()
    raw = re.sub(r"^(?:US\$|[$€£¥￥])\s*", "", raw)
    if "," in raw:
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            return None  # Ambiguous localized decimal strings remain unknown.
        raw = raw.replace(",", "")
    if len(raw) > 100:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    if (
        not number.is_finite()
        or number < 0
        or abs(number.as_tuple().exponent) > 24
        or number.adjusted() > 24
    ):
        return None
    return format(number, "f")


def currency(value: Any) -> str | None:
    value = text(value, 10)
    if value in {"RMB", "¥", "￥"}:
        return "CNY"
    return value.upper() if value and re.fullmatch(r"[A-Za-z]{3}", value) else None


def public_url(value: Any) -> str | None:
    value = text(value, 2000)
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        forbidden = {
            "apikey",
            "api_key",
            "key",
            "token",
            "access_token",
            "refresh_token",
            "signature",
            "sig",
        }
        query = [
            (k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in forbidden
        ]
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
        )
    except ValueError:
        return None


class ComponentIdentity(BaseModel):
    manufacturer: str | None = None
    mpn: str | None = None
    package: str | None = None
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    datasheet_url: str | None = None


class PriceBreak(BaseModel):
    min_quantity: int = Field(ge=1)
    max_quantity: int | None = Field(default=None, ge=1)
    unit_price: str
    currency: str | None = None

    @field_validator("unit_price", mode="before")
    @classmethod
    def valid_amount(cls, value):
        result = amount(value)
        if result is None:
            raise ValueError("Expected a finite nonnegative decimal price")
        return result

    @field_validator("currency", mode="before")
    @classmethod
    def valid_currency(cls, value):
        if value is None:
            return None
        result = currency(value)
        if result is None:
            raise ValueError("Expected an explicit three-letter currency")
        return result


class SupplierOffer(BaseModel):
    provider: ProviderName
    supplier_sku: str
    inventory_pool: str
    market: Literal["retail", "assembly"] = "retail"
    stock: int | None = Field(default=None, ge=0, le=10**12)
    availability_text: str | None = None
    min_order_quantity: int | None = Field(default=None, ge=1, le=10**12)
    order_multiple: int | None = Field(default=None, ge=1, le=10**12)
    max_order_quantity: int | None = Field(default=None, ge=1, le=10**12)
    packaging: str | None = None
    lead_time: str | None = None
    lifecycle: str | None = None
    region: str | None = None
    pricing_basis: str = "supplier_response"
    product_url: str | None = None
    price_breaks: list[PriceBreak] = Field(default_factory=list)
    fetched_at_unix: int | None = None
    cached: bool = False
    warnings: list[str] = Field(default_factory=list)

    def quote(self, quantity: int) -> dict:
        order = max(quantity, self.min_order_quantity or quantity)
        multiple = self.order_multiple or 1
        order = ((order + multiple - 1) // multiple) * multiple
        within_limit = (
            self.max_order_quantity is None or order <= self.max_order_quantity
        )
        quotes = []
        for code in sorted({p.currency for p in self.price_breaks if p.currency}):
            tiers = [
                p
                for p in self.price_breaks
                if p.currency == code
                and p.min_quantity <= order
                and (p.max_quantity is None or order <= p.max_quantity)
            ]
            selected = (
                max(tiers, key=lambda p: p.min_quantity)
                if tiers and within_limit
                else None
            )
            quotes.append(
                {
                    "currency": code,
                    "unit_price": selected.unit_price if selected else None,
                    "line_total": format(Decimal(selected.unit_price) * order, "f")
                    if selected
                    else None,
                }
            )
        return {
            "requested_quantity": quantity,
            "order_quantity": order,
            "stock_sufficient": self.stock >= order if self.stock is not None else None,
            "within_maximum_order": within_limit,
            "prices": quotes,
            "shipping_and_tax_included": None,
        }


class ComponentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: ComponentIdentity
    offers: list[SupplierOffer]
    warnings: list[str] = Field(default_factory=list)
    constraint_match: dict | None = None


class SupplierPage(BaseModel):
    records: list[ComponentRecord]
    total: int | None = None
    has_more: bool | None = None
    warnings: list[str] = Field(default_factory=list)


def price_breaks(
    rows: Any,
    *,
    quantity_key: str,
    price_key: str,
    currency_key: str | None = None,
    default_currency: Any = None,
    max_key: str | None = None,
) -> list[PriceBreak]:
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        quantity = integer(row.get(quantity_key), minimum=1)
        price = amount(row.get(price_key))
        upper = integer(row.get(max_key), minimum=1) if max_key else None
        if quantity is None or price is None or upper is not None and upper < quantity:
            continue
        result.append(
            PriceBreak(
                min_quantity=quantity,
                max_quantity=upper,
                unit_price=price,
                currency=currency(
                    row.get(currency_key) if currency_key else default_currency
                ),
            )
        )
    # Contradictory duplicate breaks are not usable purchasing evidence.
    grouped: dict[tuple, list[PriceBreak]] = {}
    for tier in result:
        grouped.setdefault((tier.currency, tier.min_quantity), []).append(tier)
    return sorted(
        [
            values[0]
            for values in grouped.values()
            if len({(p.unit_price, p.max_quantity) for p in values}) == 1
        ],
        key=lambda p: (p.currency or "", p.min_quantity),
    )


def exact_records(
    records: list[ComponentRecord],
    identifier: str,
    manufacturer: str | None = None,
    package: str | None = None,
) -> list[ComponentRecord]:
    def same(a, b):
        return bool(a and b and a.strip().casefold() == b.strip().casefold())

    selected = [
        r for r in records if any(same(o.supplier_sku, identifier) for o in r.offers)
    ]
    if not selected:
        selected = [r for r in records if same(r.component.mpn, identifier)]
    if manufacturer:
        selected = [r for r in selected if same(r.component.manufacturer, manufacturer)]
    if package:
        selected = [r for r in selected if same(r.component.package, package)]
    return selected
