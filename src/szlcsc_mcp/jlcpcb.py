"""Optional JLCPCB *assembly* discovery, isolated from domestic CNY retail data.

Public endpoint fields were cross-checked against the two reference projects.
No user cookies, credentials, browser impersonation or challenge solving.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from . import __version__
from .cache import SqliteCache
from .errors import SourcingError, error_payload
from .http import SafeSession
from .normalize import as_float, as_int
from .schemas import LibraryType


class JLCPCBClient:
    SEARCH_URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"

    def __init__(
        self,
        cache: SqliteCache,
        *,
        timeout: float = 15,
        delay: float = 0.5,
        retries: int = 2,
    ):
        self.cache = cache
        self.session = SafeSession(timeout=timeout, delay=delay, retries=retries)
        self.session.headers.update(
            {
                "User-Agent": f"szlcsc-mcp/{__version__}",
                "Accept": "application/json",
                "Origin": "https://jlcpcb.com",
                "Referer": "https://jlcpcb.com/parts",
            }
        )
        self._lock = threading.RLock()

    def close(self):
        self.session.close()

    def _request(
        self, params: dict, *, ttl: int = 120, refresh: bool = False
    ) -> tuple[dict, dict]:
        key = self.cache.make_key("POST", self.SEARCH_URL, params)
        with self._lock:
            entry = self.cache.get_entry(key)
            if not refresh and entry is not None and time.time() - entry[1] < ttl:
                return entry[0], {
                    "cached": True,
                    "fetched_at_unix": entry[1],
                    "ttl_seconds": ttl,
                }
            try:
                with self.session.post(
                    self.SEARCH_URL, json=params, retry_safe=True
                ) as response:
                    response.raise_for_status()
                    if 300 <= response.status_code < 400:
                        raise SourcingError(
                            "JLCPCB API unexpectedly redirected",
                            code="INVALID_RESPONSE",
                        )
                    payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise SourcingError(
                    "JLCPCB response unavailable or invalid", code="INVALID_RESPONSE"
                ) from exc
            if not isinstance(payload, dict) or payload.get("code") not in (200, "200"):
                raise SourcingError(
                    "JLCPCB rejected the catalog query", code="UPSTREAM_REJECTED"
                )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise SourcingError(
                    "JLCPCB returned no catalog object", code="INVALID_RESPONSE"
                )
            if params.get("searchType") == 3:
                valid = isinstance(data.get("sortAndCountVoList"), list)
            else:
                page_info = data.get("componentPageInfo")
                valid = isinstance(page_info, dict) and isinstance(page_info.get("list"), list)
            if not valid:
                raise SourcingError("JLCPCB catalog payload has an unexpected shape", code="INVALID_RESPONSE")
            self.cache.set(key, self.SEARCH_URL, data)
            return data, {
                "cached": False,
                "fetched_at_unix": int(time.time()),
                "ttl_seconds": ttl,
            }

    @staticmethod
    def _part(raw: dict) -> dict[str, Any]:
        kind = raw.get("componentLibraryType")
        preferred = raw.get("preferredComponentFlag") is True
        library = (
            "preferred"
            if preferred
            else {"base": "basic", "expand": "extended"}.get(kind)
        )
        attributes = raw.get("attributes")
        specs = (
            {
                str(a["attribute_name_en"]): a.get("attribute_value_name")
                for a in attributes
                if isinstance(a, dict) and a.get("attribute_name_en")
            }
            if isinstance(attributes, list)
            else {}
        )
        prices = raw.get("componentPrices")
        tiers = (
            [
                {
                    "min_qty": as_int(p.get("startNumber")),
                    "max_qty": as_int(p.get("endNumber")),
                    "unit_price": as_float(p.get("productPrice")),
                    "currency": p.get("currency") or raw.get("currency"),
                }
                for p in prices
                if isinstance(p, dict)
            ]
            if isinstance(prices, list)
            else []
        )
        tiers.sort(
            key=lambda p: p["min_qty"] if p["min_qty"] is not None else float("inf")
        )
        return {
            "lcsc_code": raw.get("componentCode"),
            "mpn": raw.get("componentModelEn"),
            "brand": raw.get("componentBrandEn"),
            "package": raw.get("componentSpecificationEn"),
            "description": raw.get("describe"),
            "assembly_stock": as_int(raw.get("stockCount")),
            "library_type": library,
            "category": raw.get("secondSortName"),
            "subcategory": raw.get("firstSortName"),
            "parameters": specs,
            "price_tiers": tiers,
            "datasheet_url": raw.get("dataManualUrl"),
            "source": "jlcpcb.com",
            "inventory_pool": "JLCPCB_assembly",
        }

    def search(
        self,
        query: str,
        *,
        library_type: LibraryType = "all",
        package: str | None = None,
        min_stock: int = 0,
        page: int = 1,
        page_size: int = 10,
        refresh: bool = False,
    ) -> dict:
        if library_type not in {"all", "basic", "preferred", "extended", "no_fee"}:
            raise SourcingError(
                "Unsupported assembly library type", code="INVALID_ARGUMENT"
            )
        if (
            not query.strip()
            or not 1 <= page <= 1000
            or not 1 <= page_size <= 50
            or min_stock < 0
        ):
            raise SourcingError(
                "Invalid query, pagination or minimum stock", code="INVALID_ARGUMENT"
            )
        kinds = ("basic", "preferred") if library_type == "no_fee" else (library_type,)
        parts, provenance, seen = [], [], set()
        failures = []
        has_more = False
        for kind in kinds:
            params = {
                "keyword": query.strip(),
                "currentPage": page,
                "pageSize": page_size,
                "searchSource": "search",
                "startStockNumber": min_stock,
            }
            if package:
                params["componentSpecification"] = package
            if kind in {"basic", "extended"}:
                params["componentLibraryType"] = "base" if kind == "basic" else "expand"
            if kind == "preferred":
                params["preferredComponentFlag"] = True
            try:
                data, meta = self._request(params, refresh=refresh)
            except SourcingError as exc:
                if library_type != "no_fee":
                    raise
                failures.append({"library_filter": kind, **error_payload(exc)})
                continue
            info = data.get("componentPageInfo")
            if not isinstance(info, dict) or not isinstance(info.get("list"), list):
                raise SourcingError(
                    "JLCPCB catalog page has an unexpected shape",
                    code="INVALID_RESPONSE",
                )
            total = as_int(info.get("total"))
            has_more |= (
                (page * page_size < total)
                if total is not None
                else len(info["list"]) == page_size
            )
            provenance.append({"library_filter": kind, **meta})
            for raw in info["list"]:
                if not isinstance(raw, dict):
                    continue
                part = self._part(raw)
                code = part["lcsc_code"]
                if not code or code in seen:
                    continue
                if kind != "all" and part["library_type"] != kind:
                    continue
                if package and (
                    not part["package"] or package.casefold() not in part["package"].casefold()
                ):
                    continue
                if min_stock and (
                    part["assembly_stock"] is None or part["assembly_stock"] < min_stock
                ):
                    continue
                seen.add(code)
                parts.append(part)
        if failures and not provenance:
            detail = failures[0]["error"]
            raise SourcingError(detail["message"], code=detail["code"],
                                retryable=detail["retryable"], retry_after=detail.get("retry_after_seconds"))
        return {
            "results": parts,
            "count": len(parts),
            "page": page,
            "page_size_per_library": page_size,
            "has_more": has_more,
            "library_type": library_type,
            "complete": not failures,
            "unavailable_libraries": failures,
            "data_freshness": provenance,
            "source": "jlcpcb.com",
            "inventory_pool": "JLCPCB_assembly",
            "note": "no_fee merges one page from each of basic/preferred. Currency is null unless upstream supplies it. Domestic CNY retail data is separate.",
        }

    def categories(self, *, refresh: bool = False) -> dict:
        data, meta = self._request(
            {
                "currentPage": 1,
                "pageSize": 1,
                "searchSource": "search",
                "searchType": 3,
            },
            ttl=86400,
            refresh=refresh,
        )
        raw = data.get("sortAndCountVoList")
        if not isinstance(raw, list):
            raise SourcingError(
                "JLCPCB returned no category dictionary", code="INVALID_RESPONSE"
            )

        def category(row):
            return {
                "id": as_int(row.get("componentSortKeyId")),
                "name": row.get("sortName"),
                "count": as_int(row.get("componentCount")),
            }

        rows = [
            {
                **category(r),
                "children": [
                    category(c)
                    for c in (r.get("childSortList") or [])
                    if isinstance(c, dict)
                ],
            }
            for r in raw
            if isinstance(r, dict)
        ]
        return {
            "categories": rows,
            "count": len(rows),
            "source": "jlcpcb.com",
            "data_freshness": meta,
        }
