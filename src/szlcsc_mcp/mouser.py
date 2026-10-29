"""Official read-only Mouser Search API v1; no cart/order operations."""

from __future__ import annotations

import os
import time

from pydantic import ValidationError

from .cache import SqliteCache
from .errors import SourcingError
from .supplier_http import SupplierHTTP
from .supplier_models import (
    ComponentIdentity,
    ComponentRecord,
    SupplierOffer,
    SupplierPage,
    integer,
    price_breaks,
    public_url,
    text,
)


class MouserClient:
    name = "mouser"
    registration_url = "https://www.mouser.com/en/api-search/"
    required_environment = ["MOUSER_API_KEY"]
    BASE = "https://api.mouser.com/api/v1/search/"

    def __init__(
        self, cache: SqliteCache, api_key: str | None = None, *, settings=None
    ):
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("MOUSER_API_KEY", (settings or {}).get("MOUSER_API_KEY", ""))
        ).strip()
        if len(self.api_key) > 512 or any(c in self.api_key for c in "\r\n\0"):
            raise ValueError("Invalid supplier credential configuration")
        self.http = SupplierHTTP(cache, self.name, self.api_key)

    @property
    def configured(self):
        return bool(self.api_key)

    def close(self):
        self.http.close()

    @staticmethod
    def parse(raw: dict) -> ComponentRecord | None:
        sku, mpn = (
            text(raw.get("MouserPartNumber"), 200),
            text(raw.get("ManufacturerPartNumber"), 200),
        )
        if not sku or not mpn:
            return None
        attributes = raw.get("ProductAttributes")
        parameters = {}
        for row in attributes[:64] if isinstance(attributes, list) else []:
            if isinstance(row, dict) and (key := text(row.get("AttributeName"), 100)):
                parameters[key] = text(row.get("AttributeValue"), 300)
        normalized = {k.casefold(): v for k, v in parameters.items()}
        tiers = price_breaks(
            raw.get("PriceBreaks"),
            quantity_key="Quantity",
            price_key="Price",
            currency_key="Currency",
        )
        return ComponentRecord(
            component=ComponentIdentity(
                manufacturer=text(raw.get("Manufacturer")),
                mpn=mpn,
                package=normalized.get("package / case") or normalized.get("package"),
                description=text(raw.get("Description")),
                parameters=parameters,
                datasheet_url=public_url(raw.get("DataSheetUrl")),
            ),
            offers=[
                SupplierOffer(
                    provider="mouser",
                    supplier_sku=sku,
                    inventory_pool="Mouser_distribution",
                    stock=integer(raw.get("AvailabilityInStock")),
                    availability_text=text(raw.get("Availability")),
                    min_order_quantity=integer(raw.get("Min"), minimum=1),
                    order_multiple=integer(raw.get("Mult"), minimum=1),
                    max_order_quantity=integer(
                        raw.get("SalesMaximumOrderQty"), minimum=1
                    ),
                    packaging=normalized.get("packaging"),
                    lead_time=text(raw.get("LeadTime")),
                    lifecycle=text(raw.get("LifecycleStatus")),
                    product_url=public_url(raw.get("ProductDetailUrl")),
                    price_breaks=tiers,
                    warnings=[
                        "Currency, shipping, duty and tax depend on supplier account/region; no conversion applied"
                    ],
                )
            ],
        )

    def _query(
        self, operation: str, body: dict, *, refresh: bool, offset=0, limit=50
    ) -> SupplierPage:
        if not self.configured:
            raise SourcingError(
                "Mouser Search API key is not configured; see component_providers and supplier setup guide",
                code="AUTHENTICATION_REQUIRED",
            )
        url = self.BASE + operation
        key = self.http.cache_key(url, body)
        with self.http.lock:
            cached = self.http.read(key, refresh)
            page = None
            if cached:
                try:
                    page = SupplierPage(
                        records=[
                            ComponentRecord.model_validate(r)
                            for r in cached[0]["records"]
                        ],
                        total=integer(cached[0].get("total")),
                    )
                except (KeyError, TypeError, ValueError, ValidationError):
                    page = None
            if page is None:
                payload = self.http.request(
                    "POST",
                    url,
                    params={"apiKey": self.api_key},
                    json=body,
                    retry_safe=True,
                )
                if payload.get("Errors"):
                    raise SourcingError(
                        "Mouser rejected the search request; check account permissions and query",
                        code="UPSTREAM_REJECTED",
                    )
                result = payload.get("SearchResults")
                if not isinstance(result, dict) or not isinstance(
                    result.get("Parts"), list
                ):
                    raise SourcingError(
                        "Mouser search response has an unexpected shape",
                        code="INVALID_RESPONSE",
                    )
                rows = [
                    record
                    for raw in result["Parts"][:50]
                    if isinstance(raw, dict) and (record := self.parse(raw)) is not None
                ]
                if len(rows) != len(result["Parts"]):
                    raise SourcingError(
                        "Mouser search returned invalid or excessive part records",
                        code="INVALID_RESPONSE",
                    )
                if operation == "keyword" and len(rows) > limit:
                    raise SourcingError(
                        "Mouser exceeded the requested page size",
                        code="INVALID_RESPONSE",
                    )
                page = SupplierPage(
                    records=rows, total=integer(result.get("NumberOfResult"))
                )
                fetched = int(time.time())
                self.http.write(key, url, [r.model_dump() for r in rows], page.total)
                was_cached = False
            else:
                fetched = cached[1]
                was_cached = True
            for record in page.records:
                for offer in record.offers:
                    offer.cached, offer.fetched_at_unix = was_cached, fetched
            page.has_more = (
                offset + limit < page.total
                if page.total is not None
                else len(page.records) == limit
            )
            return page

    def search(self, query: str, *, page=1, limit=10, refresh=False) -> SupplierPage:
        return self._query(
            "keyword",
            {
                "SearchByKeywordRequest": {
                    "keyword": query,
                    "records": limit,
                    "startingRecord": (page - 1) * limit,
                    "searchOptions": "None",
                    "searchWithYourSignUpLanguage": "false",
                }
            },
            refresh=refresh,
            offset=(page - 1) * limit,
            limit=limit,
        )

    def lookup(self, identifier: str, *, refresh=True) -> SupplierPage:
        if not 3 <= len(identifier) <= 40 or "|" in identifier:
            raise SourcingError(
                "Mouser exact lookup requires one 3–40 character part number",
                code="INVALID_ARGUMENT",
            )
        return self._query(
            "partnumber",
            {
                "SearchByPartRequest": {
                    "mouserPartNumber": identifier,
                    "partSearchOptions": "Exact",
                }
            },
            refresh=refresh,
        )
