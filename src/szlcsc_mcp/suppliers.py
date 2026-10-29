"""Provider registry, compatibility adapters and bounded multi-supplier queries."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Protocol

from .digikey import DigiKeyClient
from .errors import SourcingError, error_payload
from .freshness import bypass
from .models import Part
from .mouser import MouserClient
from .normalize import normalize_c_code, part_from_dict
from .parametric import evaluate_constraints
from .supplier_config import read_supplier_settings
from .supplier_models import (
    ComponentIdentity,
    ComponentRecord,
    SupplierOffer,
    SupplierPage,
    exact_records,
    price_breaks,
    public_url,
    text,
)


class Provider(Protocol):
    name: str
    configured: bool
    registration_url: str
    required_environment: list[str]

    def search(
        self, query: str, *, page: int, limit: int, refresh: bool
    ) -> SupplierPage: ...
    def lookup(self, identifier: str, *, refresh: bool) -> SupplierPage: ...
    def close(self): ...


class DomesticProvider:
    name, configured = "szlcsc", True
    registration_url = "https://www.szlcsc.com/"
    required_environment = []

    def __init__(self, client):
        self.client = client

    @staticmethod
    def convert(part: Part):
        if not part.lcsc_code:
            raise SourcingError(
                "Domestic result has no supplier identifier", code="INVALID_RESPONSE"
            )
        return ComponentRecord(
            component=ComponentIdentity(
                manufacturer=part.brand,
                mpn=part.mpn,
                package=part.package,
                description=part.name,
                parameters=part.parameters,
                datasheet_url=public_url(part.datasheet_url),
            ),
            offers=[
                SupplierOffer(
                    provider="szlcsc",
                    supplier_sku=part.lcsc_code,
                    inventory_pool="SZLCSC_domestic_retail",
                    stock=part.stock if part.stock is not None else part.total_stock,
                    min_order_quantity=part.min_order_qty,
                    order_multiple=part.order_multiple,
                    packaging=part.packaging,
                    region="CN",
                    product_url=public_url(part.product_url),
                    price_breaks=price_breaks(
                        part.to_dict()["price_tiers"],
                        quantity_key="min_qty",
                        price_key="unit_price_cny",
                        currency_key="currency",
                        max_key="max_qty",
                    ),
                    fetched_at_unix=part.data_freshness.get("oldest_fetched_at_unix"),
                    cached=bool(part.data_freshness.get("cache_hits")),
                    warnings=part.warnings,
                )
            ],
        )

    def search(self, query, *, page=1, limit=10, refresh=False):
        token = bypass.set(refresh or bypass.get())
        try:
            records = [
                self.convert(p)
                for p in self.client.search(query, page=page, max_results=limit)
            ]
            return SupplierPage(
                records=records,
                has_more=None,
                warnings=[
                    "Domestic search does not expose a reliable total; pagination completeness is unknown"
                ],
            )
        finally:
            bypass.reset(token)

    def lookup(self, identifier, *, refresh=True):
        identifier = normalize_c_code(identifier.strip())
        if re.fullmatch(r"C\d+", identifier):
            return SupplierPage(
                records=[self.convert(self.client.get_part(identifier, refresh=refresh))],
                total=1,
                has_more=False,
            )
        if not identifier:
            raise SourcingError("identifier must not be empty", code="INVALID_ARGUMENT")

        # search() applies relevance filters, so its length cannot establish whether
        # the upstream page was exhausted. Inspect the unfiltered candidate window.
        params = {
            "keyword": identifier, "currPage": 1, "pageSize": 50,
            "sort": 0, "stock": False,
        }
        token = bypass.set(refresh or bypass.get())
        try:
            def fetch_page():
                data = self.client._get_json(
                    f"{self.client.SEARCH_HOST}/phone/p/product/search", params, ttl=600
                )
                rows = data if isinstance(data, list) else None
                if isinstance(data, dict):
                    rows = next(
                        (data[key] for key in ("productList", "list", "records", "rows", "products")
                         if isinstance(data.get(key), list)),
                        None,
                    )
                    # The live API returns a null productList past its reported
                    # total. A null list before that boundary remains invalid.
                    total = data.get("total")
                    if (
                        rows is None and "productList" in data
                        and data["productList"] is None
                        and isinstance(total, int) and not isinstance(total, bool)
                        and 0 <= total <= (params["currPage"] - 1) * params["pageSize"]
                    ):
                        rows = []
                if rows is None or any(not isinstance(row, dict) for row in rows):
                    raise SourcingError(
                        "Invalid domestic candidate page", code="INVALID_RESPONSE"
                    )
                return rows

            rows = fetch_page()
            has_more = len(rows) >= params["pageSize"]
            if not has_more:
                params["currPage"] = 2
                has_more = bool(fetch_page())
            parts = [part_from_dict(row) for row in rows]
            exact = [
                part for part in parts
                if part.mpn and part.mpn.strip().casefold() == identifier.casefold()
            ]
            if any(not part.lcsc_code for part in exact):
                raise SourcingError(
                    "Domestic result has no supplier identifier", code="INVALID_RESPONSE"
                )
            records = [
                self.convert(self.client.get_part(part.lcsc_code, refresh=refresh))
                for part in exact
            ]
            return SupplierPage(records=records, has_more=has_more)
        finally:
            bypass.reset(token)

    def close(self):
        pass  # The shared legacy server owns its session.


class AssemblyProvider:
    name, configured = "jlcpcb", True
    registration_url = "https://jlcpcb.com/parts"
    required_environment = []

    def __init__(self, client):
        self.client = client

    def search(self, query, *, page=1, limit=10, refresh=False):
        payload = self.client.search(query, page=page, page_size=limit, refresh=refresh)
        records = []
        snapshots = payload.get("data_freshness", [])
        oldest = min(
            (
                r["fetched_at_unix"]
                for r in snapshots
                if r.get("fetched_at_unix") is not None
            ),
            default=None,
        )
        for row in payload["results"]:
            records.append(
                ComponentRecord(
                    component=ComponentIdentity(
                        manufacturer=text(row.get("brand")),
                        mpn=text(row.get("mpn")),
                        package=text(row.get("package")),
                        description=text(row.get("description")),
                        parameters=row.get("parameters") or {},
                        datasheet_url=public_url(row.get("datasheet_url")),
                    ),
                    offers=[
                        SupplierOffer(
                            provider="jlcpcb",
                            supplier_sku=row["lcsc_code"],
                            market="assembly",
                            inventory_pool="JLCPCB_assembly",
                            stock=row.get("assembly_stock"),
                            pricing_basis="assembly",
                            fetched_at_unix=oldest,
                            cached=any(r.get("cached") for r in snapshots),
                            price_breaks=price_breaks(
                                row.get("price_tiers"),
                                quantity_key="min_qty",
                                price_key="unit_price",
                                currency_key="currency",
                                max_key="max_qty",
                            ),
                            warnings=[
                                "JLCPCB assembly inventory is separate from domestic retail; unknown currency stays null",
                                "Assembly library: " + str(row.get("library_type")),
                            ],
                        )
                    ],
                )
            )
        return SupplierPage(records=records, has_more=payload.get("has_more"))

    def lookup(self, identifier, *, refresh=True):
        return self.search(identifier, page=1, limit=50, refresh=refresh)

    def close(self):
        pass


class UnavailableProvider:
    configured = False

    def __init__(
        self,
        name,
        *,
        code="UNSUPPORTED_PROVIDER",
        message="Official adapter contract is not yet available",
        registration_url="",
        required_environment=None,
    ):
        self.name, self.code, self.message, self.registration_url = (
            name,
            code,
            message,
            registration_url,
        )
        self.required_environment = required_environment or []

    def search(self, *args, **kwargs):
        raise SourcingError(self.message, code=self.code)

    lookup = search

    def close(self):
        pass


class SupplierRegistry:
    def __init__(self, providers: list[Provider]):
        self.providers = {p.name: p for p in providers}
        self.last_status: dict[str, str] = {}

    def close(self):
        for provider in self.providers.values():
            provider.close()

    def status(self):
        rows = []
        for provider in self.providers.values():
            unsupported = isinstance(provider, UnavailableProvider)
            rows.append(
                {
                    "provider": provider.name,
                    "implemented": not unsupported,
                    "configured": provider.configured,
                    "status": (
                        "configuration_error"
                        if provider.code == "CONFIGURATION_ERROR"
                        else "unsupported"
                    )
                    if unsupported
                    else (
                        "configured"
                        if provider.configured
                        else "authentication_required"
                    ),
                    "last_request_status": self.last_status.get(
                        provider.name, "not_tested"
                    ),
                    "required_environment": provider.required_environment,
                    "registration_url": provider.registration_url,
                    "note": provider.message
                    if unsupported
                    else "Configured does not imply a successful authenticated upstream request",
                }
            )
        return {
            "providers": rows,
            "count": len(rows),
            "credential_values_returned": False,
            "local_configuration": "Copy suppliers.example.json outside the repository, fill your own keys, set COMPONENT_SUPPLIERS_CONFIG to that file and reconnect MCP. Environment variables override file fields.",
        }

    def _selected(self, providers):
        names = (
            providers
            if providers is not None
            else [n for n in self.providers if n != "huaqiu"]
        )
        if (
            not names
            or len(names) > 5
            or len(set(names)) != len(names)
            or any(n not in self.providers for n in names)
        ):
            raise SourcingError(
                "Choose 1–5 distinct registered providers", code="INVALID_ARGUMENT"
            )
        return names

    def _collect(self, names, operation, identifier, *, page=1, limit=5, refresh=False):
        def call(name):
            try:
                provider = self.providers[name]
                result = (
                    provider.search(identifier, page=page, limit=limit, refresh=refresh)
                    if operation == "search"
                    else provider.lookup(identifier, refresh=refresh)
                )
                if operation == "search" and len(result.records) > limit:
                    raise SourcingError(
                        "Provider exceeded the requested page size",
                        code="INVALID_RESPONSE",
                    )
                self.last_status[name] = "ready"
                return name, result, None
            except SourcingError as error:
                self.last_status[name] = error.code.lower()
                return name, None, error_payload(error)["error"]
            except Exception:
                self.last_status[name] = "internal_error"
                return (
                    name,
                    None,
                    error_payload(
                        "Provider failed without exposing internal details",
                        code="INTERNAL_ERROR",
                    )["error"],
                )

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            return list(pool.map(call, names))

    @staticmethod
    def _filter(record, manufacturer, package, constraints, in_stock):
        if manufacturer and (
            not record.component.manufacturer
            or record.component.manufacturer.strip().casefold()
            != manufacturer.strip().casefold()
        ):
            return False
        if package and (
            not record.component.package
            or record.component.package.strip().casefold() != package.strip().casefold()
        ):
            return False
        if in_stock:
            record.offers = [
                o for o in record.offers if o.stock is not None and o.stock > 0
            ]
            if not record.offers:
                return False
        if constraints:
            identity = record.component
            evidence = evaluate_constraints(
                Part(
                    mpn=identity.mpn,
                    brand=identity.manufacturer,
                    package=identity.package,
                    parameters=identity.parameters,
                ),
                constraints,
            )
            record.constraint_match = evidence
            if not evidence["all_matched"]:
                return False
        return True

    def search(
        self,
        query,
        *,
        providers=None,
        page=1,
        limit=5,
        manufacturer=None,
        package=None,
        constraints=None,
        in_stock=False,
        refresh=False,
    ):
        names = self._selected(providers)
        if not query.strip() or not 1 <= page <= 1000 or not 1 <= limit <= 50:
            raise SourcingError("Invalid query or pagination", code="INVALID_ARGUMENT")
        results, statuses, failures = [], [], []
        for name, response, error in self._collect(
            names, "search", query, page=page, limit=limit, refresh=refresh
        ):
            if error:
                failures.append({"provider": name, "error": error})
                continue
            records = [
                r
                for r in response.records
                if self._filter(r, manufacturer, package, constraints, in_stock)
            ]
            results.extend(r.model_dump() for r in records)
            statuses.append(
                {
                    "provider": name,
                    "returned": len(records),
                    "upstream_total": response.total,
                    "has_more": response.has_more,
                    "warnings": response.warnings,
                }
            )
        payload = {
            "ok": bool(statuses),
            "complete": not failures,
            "results": results,
            "count": len(results),
            "page": page,
            "page_size_per_provider": limit,
            "provider_results": statuses,
            "provider_errors": failures,
            "filter_scope": "Exact manufacturer/package and electrical filters apply within each fetched provider page; increase page to inspect later candidates",
        }
        if not statuses:
            payload.update(
                error_payload(
                    "No supplier query succeeded; inspect provider_errors",
                    code="NO_PROVIDER_SUCCEEDED",
                )
            )
        return payload

    def get(
        self, provider, identifier, *, manufacturer=None, package=None, refresh=True
    ):
        self._selected([provider])
        _, response, error = self._collect(
            [provider], "lookup", identifier, refresh=refresh
        )[0]
        if error:
            raise SourcingError(
                error["message"],
                code=error["code"],
                retryable=error["retryable"],
                retry_after=error.get("retry_after_seconds"),
            )
        selected = exact_records(response.records, identifier, manufacturer, package)
        if not selected:
            raise SourcingError(
                "No exact supplier SKU/MPN matched the requested identity and filters",
                code="PART_NOT_FOUND",
            )
        if response.has_more and not any(
            o.supplier_sku.strip().casefold() == identifier.strip().casefold()
            for r in selected
            for o in r.offers
        ):
            raise SourcingError(
                "Exact lookup returned an incomplete candidate window; use a supplier SKU",
                code="AMBIGUOUS_PART",
            )
        identities = {
            (r.component.manufacturer, r.component.mpn, r.component.package)
            for r in selected
        }
        if len(identities) != 1:
            raise SourcingError(
                "Part number matches multiple identities; specify manufacturer/package or supplier SKU",
                code="AMBIGUOUS_PART",
            )
        result = selected[0].model_copy(deep=True)
        seen = {o.supplier_sku for o in result.offers}
        for record in selected[1:]:
            result.offers.extend(o for o in record.offers if o.supplier_sku not in seen)
            seen.update(o.supplier_sku for o in record.offers)
        # A supplier SKU identifies its packaging variant, not every packaging of the MPN.
        matching = [
            o
            for o in result.offers
            if o.supplier_sku.strip().casefold() == identifier.strip().casefold()
        ]
        if matching:
            result.offers = matching
        return result.model_dump()

    def offers(
        self,
        mpn,
        *,
        quantity=1,
        providers=None,
        manufacturer=None,
        package=None,
        refresh=True,
    ):
        if not 1 <= quantity <= 1_000_000_000:
            raise SourcingError(
                "Quantity must be 1–1000000000", code="INVALID_ARGUMENT"
            )
        names = self._selected(providers)
        results, failures, groups = [], [], {}
        for name, response, error in self._collect(
            names, "lookup", mpn, refresh=refresh
        ):
            if error:
                failures.append({"provider": name, "error": error})
                continue
            matched = exact_records(response.records, mpn, manufacturer, package)
            if response.has_more:
                failures.append(
                    {
                        "provider": name,
                        "error": error_payload(
                            "Exact offer candidate window is incomplete; results are partial",
                            code="INCOMPLETE_RESULTS",
                        )["error"],
                    }
                )
            # This tool requires MPN equality even when the input resembles a supplier SKU.
            matched = [
                r
                for r in matched
                if r.component.mpn
                and r.component.mpn.strip().casefold() == mpn.strip().casefold()
            ]
            for record in matched:
                for offer in record.offers:
                    quote = offer.quote(quantity)
                    row = {
                        "component": record.component.model_dump(),
                        "offer": offer.model_dump(),
                        "quote": quote,
                    }
                    results.append(row)
                    identity = record.component
                    if identity.manufacturer and identity.package:
                        for price in quote["prices"]:
                            if price["line_total"] is not None:
                                key = (
                                    identity.manufacturer.strip().casefold(),
                                    identity.mpn.strip().casefold(),
                                    identity.package.strip().casefold(),
                                    offer.market,
                                    price["currency"],
                                )
                                groups.setdefault(key, []).append(
                                    {
                                        "provider": name,
                                        "supplier_sku": offer.supplier_sku,
                                        **price,
                                        "order_quantity": quote["order_quantity"],
                                    }
                                )
        comparison = [
            {
                "manufacturer": k[0],
                "mpn": k[1],
                "package": k[2],
                "market": k[3],
                "currency": k[4],
                "offers": sorted(v, key=lambda p: Decimal(p["line_total"])),
            }
            for k, v in groups.items()
        ]
        payload = {
            "ok": bool(results),
            "complete": not failures,
            "mpn": mpn,
            "requested_quantity": quantity,
            "offers": results,
            "count": len(results),
            "comparison_groups": comparison,
            "provider_errors": failures,
            "warnings": [
                "No FX conversion or stock aggregation. Groups require exact manufacturer/MPN/package and the same currency/retail-or-assembly market.",
                "Comparison covers component line price only; region, account pricing, shipping, tax, duty and reeling fees may differ.",
                "Missing manufacturer/package is not sufficient identity evidence for a comparison group.",
            ],
        }
        if not results:
            payload.update(
                error_payload(
                    "No exact supplier offers found; inspect provider_errors and identity filters",
                    code="NO_OFFERS_FOUND",
                )
            )
        return payload


def build_registry(domestic, assembly):
    providers = [DomesticProvider(domestic), AssemblyProvider(assembly)]
    try:
        settings, config_error = read_supplier_settings(), False
    except ValueError:
        settings, config_error = {}, True
    for factory in (MouserClient, DigiKeyClient):
        try:
            if config_error:
                raise ValueError("Invalid local supplier settings")
            providers.append(factory(domestic.cache, settings=settings))
        except (ValueError, TypeError):
            providers.append(
                UnavailableProvider(
                    factory.name,
                    code="CONFIGURATION_ERROR",
                    message="Invalid local supplier configuration; review supplier setup guide",
                    registration_url=factory.registration_url,
                    required_environment=factory.required_environment,
                )
            )
    providers.append(
        UnavailableProvider(
            "huaqiu",
            registration_url="https://www.hqchip.com/mycenter/account/openapi",
            message="Apply through Huaqiu OpenAPI; obtain the approved endpoint/signature contract before implementing an adapter. Setting a key alone does not enable this provider.",
        )
    )
    return SupplierRegistry(providers)
