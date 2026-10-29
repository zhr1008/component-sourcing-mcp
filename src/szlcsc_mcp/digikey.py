"""Official DigiKey Product Information v4, with two-legged OAuth in memory."""

from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

from pydantic import ValidationError

from .cache import SqliteCache
from .errors import SourcingError
from .supplier_http import SupplierHTTP
from .supplier_models import (
    ComponentIdentity,
    ComponentRecord,
    SupplierOffer,
    SupplierPage,
    currency,
    integer,
    price_breaks,
    public_url,
    text,
)


class DigiKeyClient:
    name = "digikey"
    registration_url = "https://developer.digikey.com/"
    required_environment = ["DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET"]

    def __init__(
        self,
        cache: SqliteCache,
        *,
        client_id=None,
        client_secret=None,
        access_token=None,
        sandbox=None,
        settings=None,
    ):
        def setting(name, default=""):
            return os.getenv(name, (settings or {}).get(name, default))

        self.client_id = (
            setting("DIGIKEY_CLIENT_ID") if client_id is None else client_id
        )
        self.client_secret = (
            setting("DIGIKEY_CLIENT_SECRET") if client_secret is None else client_secret
        )
        self._token = (
            setting("DIGIKEY_ACCESS_TOKEN") if access_token is None else access_token
        )
        self._manual_token = bool(self._token)
        if self._manual_token:
            self.required_environment = ["DIGIKEY_CLIENT_ID", "DIGIKEY_ACCESS_TOKEN"]
        self._expires_at = 0.0
        sandbox = (
            setting("DIGIKEY_SANDBOX", "false").lower()
            if sandbox is None
            else str(sandbox).lower()
        )
        if sandbox not in {"true", "false"}:
            raise ValueError("Invalid supplier sandbox setting")
        self.base = (
            "https://sandbox-api.digikey.com"
            if sandbox == "true"
            else "https://api.digikey.com"
        )
        self.site = setting("DIGIKEY_SITE", "US").upper()
        self.currency = setting("DIGIKEY_CURRENCY", "USD").upper()
        self.language = setting("DIGIKEY_LANGUAGE", "en")
        self.account = setting("DIGIKEY_ACCOUNT_ID")
        if (
            not re.fullmatch(r"[A-Z]{2}", self.site)
            or not currency(self.currency)
            or not re.fullmatch(r"[a-z]{2,3}", self.language)
        ):
            raise ValueError("Invalid supplier locale configuration")
        if self.account and integer(self.account) is None:
            raise ValueError("Invalid supplier account configuration")
        secrets = (self.client_id, self.client_secret, self._token, self.account)
        if any(len(s) > 8192 or any(c in s for c in "\r\n\0") for s in secrets):
            raise ValueError("Invalid supplier credential configuration")
        self.http = SupplierHTTP(
            cache,
            self.name,
            "\0".join(secrets),
            secrets=(self.client_id, self.client_secret, self._token),
            delay=0.6,
        )

    @property
    def configured(self):
        return bool(self.client_id and (self.client_secret or self._token))

    def close(self):
        self.http.close()
        self._token = ""

    def _headers(self):
        if not self.configured:
            raise SourcingError(
                "DigiKey application credentials are not configured; see component_providers and supplier setup guide",
                code="AUTHENTICATION_REQUIRED",
            )
        if not self._manual_token and time.monotonic() >= self._expires_at:
            token = self.http.request(
                "POST",
                self.base + "/v1/oauth2/token",
                redact_response=False,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
            )
            access = text(token.get("access_token"), 8192)
            expires = integer(token.get("expires_in"), minimum=1)
            if (
                not access
                or not expires
                or not isinstance(token.get("token_type"), str)
                or token["token_type"].casefold() != "bearer"
                or any(c in access for c in "\r\n\0")
            ):
                raise SourcingError(
                    "DigiKey returned invalid OAuth credentials",
                    code="INVALID_RESPONSE",
                )
            self.http.redactor.add(access)
            self._token = access
            self._expires_at = time.monotonic() + max(1, expires - min(30, expires / 2))
        headers = {
            "X-DIGIKEY-Client-Id": self.client_id,
            "Authorization": "Bearer " + self._token,
            "X-DIGIKEY-Locale-Site": self.site,
            "X-DIGIKEY-Locale-Currency": self.currency,
            "X-DIGIKEY-Locale-Language": self.language,
        }
        if self.account:
            headers["X-DIGIKEY-Account-Id"] = self.account
        return headers

    @staticmethod
    def parse(raw: dict, *, locale: dict, detail: bool) -> ComponentRecord | None:
        mpn = text(raw.get("ManufacturerProductNumber"), 200)
        if not mpn:
            return None
        maker = raw.get("Manufacturer")
        description = raw.get("Description")
        parameters = {}
        rows = raw.get("Parameters")
        for row in rows[:64] if isinstance(rows, list) else []:
            if isinstance(row, dict) and (key := text(row.get("ParameterText"), 100)):
                parameters[key] = text(row.get("ValueText"), 300)
        package = next(
            (
                v
                for k, v in parameters.items()
                if k.casefold()
                in {"package / case", "package", "supplier device package"}
            ),
            None,
        )
        variants = raw.get("ProductVariations")
        offers = []
        for variant in variants[:50] if isinstance(variants, list) else []:
            if not isinstance(variant, dict) or not (
                sku := text(variant.get("DigiKeyProductNumber"), 200)
            ):
                continue
            mine = variant.get("MyPricing") if detail else None
            mine = mine if isinstance(mine, list) and mine else None
            package_type = variant.get("PackageType")
            status = raw.get("ProductStatus")
            offers.append(
                SupplierOffer(
                    provider="digikey",
                    supplier_sku=sku,
                    inventory_pool="DigiKey_marketplace"
                    if variant.get("MarketPlace") is True
                    else "DigiKey_distribution",
                    stock=integer(variant.get("QuantityAvailableforPackageType")),
                    min_order_quantity=integer(
                        variant.get("MinimumOrderQuantity"), minimum=1
                    ),
                    max_order_quantity=integer(
                        variant.get("MaxQuantityForDistribution"), minimum=1
                    ),
                    packaging=text(package_type.get("Name"))
                    if isinstance(package_type, dict)
                    else None,
                    lifecycle=text(status.get("Status"))
                    if isinstance(status, dict)
                    else None,
                    lead_time=text(raw.get("ManufacturerLeadWeeks")),
                    region=text(locale.get("Site")),
                    product_url=public_url(raw.get("ProductUrl")),
                    pricing_basis="account" if mine else "standard",
                    price_breaks=price_breaks(
                        mine or variant.get("StandardPricing"),
                        quantity_key="BreakQuantity",
                        price_key="UnitPrice",
                        default_currency=locale.get("Currency"),
                    ),
                    warnings=[
                        "Stock belongs to this packaging variant; shipping/tax and reeling fees are excluded"
                    ],
                )
            )
        if not offers:
            return None
        return ComponentRecord(
            component=ComponentIdentity(
                manufacturer=text(maker.get("Name"))
                if isinstance(maker, dict)
                else None,
                mpn=mpn,
                package=package,
                description=text(description.get("ProductDescription"))
                if isinstance(description, dict)
                else None,
                parameters=parameters,
                datasheet_url=public_url(raw.get("DatasheetUrl")),
            ),
            offers=offers,
        )

    def _query(self, path, body, *, refresh, page=1, limit=10, detail=False):
        if not self.configured:
            self._headers()  # Returns the same structured configuration error without HTTP.
        url = self.base + "/products/v4" + path
        context = {
            "body": body,
            "site": self.site,
            "currency": self.currency,
            "language": self.language,
        }
        key = self.http.cache_key(url, context)
        with self.http.lock:
            entry = self.http.read(key, refresh)
            result = None
            if entry:
                try:
                    result = SupplierPage(
                        records=[
                            ComponentRecord.model_validate(r)
                            for r in entry[0]["records"]
                        ],
                        total=integer(entry[0].get("total")),
                    )
                except (KeyError, TypeError, ValueError, ValidationError):
                    result = None
            if result is None:
                payload = self.http.request(
                    "GET" if detail else "POST",
                    url,
                    headers=self._headers(),
                    **({} if detail else {"json": body, "retry_safe": True}),
                )
                if (
                    payload.get("Errors")
                    or payload.get("status")
                    and payload.get("status") != 200
                ):
                    raise SourcingError(
                        "DigiKey rejected the product request", code="UPSTREAM_REJECTED"
                    )
                rows = [payload.get("Product")] if detail else payload.get("Products")
                locale = payload.get("SearchLocaleUsed")
                if (
                    not isinstance(rows, list)
                    or not isinstance(locale, dict)
                    or not currency(locale.get("Currency"))
                ):
                    raise SourcingError(
                        "DigiKey returned invalid products or unverified currency",
                        code="INVALID_RESPONSE",
                    )
                if not detail and len(rows) > limit:
                    raise SourcingError(
                        "DigiKey exceeded the requested page size",
                        code="INVALID_RESPONSE",
                    )
                records = [
                    record
                    for raw in rows
                    if isinstance(raw, dict)
                    and (record := self.parse(raw, locale=locale, detail=detail))
                ]
                if len(records) != len(rows):
                    raise SourcingError(
                        "DigiKey returned invalid product/packaging records",
                        code="INVALID_RESPONSE",
                    )
                result = SupplierPage(
                    records=records,
                    total=1 if detail else integer(payload.get("ProductsCount")),
                )
                fetched = int(time.time())
                self.http.write(
                    key, url, [r.model_dump() for r in records], result.total
                )
                cached = False
            else:
                fetched, cached = entry[1], True
            for record in result.records:
                for offer in record.offers:
                    offer.cached, offer.fetched_at_unix = cached, fetched
            result.has_more = (
                (page * limit < result.total)
                if result.total is not None
                else len(result.records) == limit
            )
            return result

    def search(self, query: str, *, page=1, limit=10, refresh=False):
        return self._query(
            "/search/keyword",
            {"Keywords": query, "Limit": limit, "Offset": (page - 1) * limit},
            refresh=refresh,
            page=page,
            limit=limit,
        )

    def lookup(self, identifier: str, *, refresh=True):
        return self._query(
            "/search/" + quote(identifier, safe="") + "/productdetails",
            None,
            refresh=refresh,
            detail=True,
        )
