from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .cache import SqliteCache
from .errors import SourcingError
from .freshness import bypass, record, tracked
from .http import SafeSession, safe_get
from . import __version__
from .models import Part
from .normalize import merge_part, normalize_c_code, normalize_datasheet_url, part_from_dict
from .parametric import evaluate_constraints


class SZLCSCError(SourcingError):
    pass


class SZLCSCClient:
    SEARCH_HOST = "https://so.szlcsc.com"
    ITEM_HOST = "https://item.szlcsc.com"
    LIST_HOST = "https://list.szlcsc.com"
    ICSEARCH_HOST = "https://icsearch.szlcsc.com"

    def __init__(
        self,
        cache: SqliteCache | None = None,
        timeout: float = 15.0,
        request_delay: float | None = None,
        retries: int = 2,
    ):
        self.cache = cache or SqliteCache(os.getenv("SZLCSC_CACHE_PATH"))
        self.timeout = float(os.getenv("SZLCSC_TIMEOUT", timeout))
        self.request_delay = float(
            os.getenv("SZLCSC_REQUEST_DELAY", request_delay if request_delay is not None else 0.5)
        )
        self.retries = int(os.getenv("SZLCSC_RETRIES", retries))
        self.session = SafeSession(delay=self.request_delay, retries=self.retries, timeout=self.timeout)
        self.session.headers.update(
            {
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                "User-Agent": f"szlcsc-mcp/{__version__} (+local engineering assistant; respectful caching)",
                "X-Lc-Source": "Ios",
                "Origin": "https://m.szlcsc.com",
                "Referer": "https://m.szlcsc.com/",
            }
        )
        self._last_request = 0.0
        self._rate_lock = threading.Lock()
        self._fetch_lock = threading.RLock()

    def close(self) -> None:
        self.session.close()

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self.request_delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if isinstance(payload, dict):
            code = payload.get("code")
            if payload.get("success") is False or code not in (None, 0, "0", 200, "200"):
                raise SZLCSCError(f"SZLCSC API rejected the request (code={code})", code="UPSTREAM_REJECTED")
        if isinstance(payload, dict) and "result" in payload:
            return payload.get("result")
        return payload

    def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        ttl: int = 600,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = self.cache.make_key("GET", url, params)
        # Single-flight per client: recheck after waiting for an identical fetch.
        with self._fetch_lock:
            entry = self.cache.get_entry(key)
            if not bypass.get() and entry is not None and time.time() - entry[1] < ttl:
                record(url, entry[1], cached=True)
                return entry[0]
            try:
                with self.session.get(url, params=params, timeout=self.timeout) as r:
                    r.raise_for_status()
                    if 300 <= r.status_code < 400:
                        raise SZLCSCError("API unexpectedly redirected", code="INVALID_RESPONSE")
                    result = self._unwrap(r.json())
                self.cache.set(key, url, result)
                record(url, int(time.time()), cached=False)
                return result
            except SourcingError as exc:
                raise SZLCSCError(str(exc), code=exc.code, retryable=exc.retryable,
                                  retry_after=exc.retry_after) from exc
            except (requests.RequestException, ValueError) as exc:
                # Never silently substitute a week-old price/stock snapshot.
                raise SZLCSCError("Upstream request failed or returned invalid JSON", code="INVALID_RESPONSE") from exc

    @staticmethod
    def _product_list(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if not isinstance(data, dict):
            return []
        for key in ("productList", "list", "records", "rows", "products"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return []

    _INTENT_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
        (re.compile(r"(?:^|\W)rs[-_ ]?485(?:$|\W)|485\s*(?:总线|收发器)", re.I), ("rs485", "rs-485", "485收发器", "485 transceiver"), "RS485"),
        (re.compile(r"(?:^|\W)rs[-_ ]?422(?:$|\W)|422\s*(?:总线|收发器)", re.I), ("rs422", "rs-422", "422收发器", "422 transceiver"), "RS422"),
        (re.compile(r"(?:^|\W)can(?:$|\W)|can\s*(?:总线|收发器)", re.I), ("can", "can总线", "can收发器", "can transceiver"), "CAN"),
        (re.compile(r"电压基准|基准电压|voltage\s+reference", re.I), ("电压基准", "基准芯片", "voltage reference", "reference voltage"), "voltage_reference"),
        (re.compile(r"运放|运算放大器|op\s*-?amp|operational\s+amplifier", re.I), ("运放", "运算放大器", "opamp", "op amp", "operational amplifier"), "opamp"),
        (re.compile(r"(?:^|\W)adc(?:$|\W)|模数转换|模/数转换", re.I), ("adc", "模数转换", "模/数转换"), "ADC"),
        (re.compile(r"(?:^|\W)dac(?:$|\W)|数模转换|数/模转换", re.I), ("dac", "数模转换", "数/模转换"), "DAC"),
    )

    @staticmethod
    def _search_haystack(part: Part) -> str:
        values: list[str] = [
            part.lcsc_code or "",
            part.mpn or "",
            part.name or "",
            part.brand or "",
            part.package or "",
            part.category or "",
        ]
        for key, value in part.parameters.items():
            values.extend((str(key), str(value)))
        return " ".join(values).casefold()

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        # Preserve engineering/model tokens such as 3.3V, STM32F407, RS485, 100nF.
        return [
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+%/_-]*|[\u4e00-\u9fff]+", query)
            if token.strip()
        ]

    @staticmethod
    def _is_model_anchor(token: str) -> bool:
        """Return True for model/bus-like tokens that should strongly constrain relevance.

        Pure engineering values (3.3V, 100nF, 10k, 500mA) are deliberately not hard
        anchors because shallow search results often omit parametric values.
        """
        low = token.casefold()
        if low in {"rs485", "rs-485", "rs422", "rs-422", "can"}:
            return True
        # Electrical value/unit tokens should influence score only, not gate results.
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:p|n|u|µ|m|k|meg|g)?(?:v|a|f|h|hz|ohm|ω|%)?", low):
            return False
        return bool(re.search(r"[a-z]", low) and re.search(r"\d", low))

    @classmethod
    def search_relevance(cls, part: Part, query: str) -> dict[str, Any]:
        """Explain client-side relevance without pretending it is an electrical match.

        SZLCSC full-text search can return broad category matches (for example an
        Ethernet PHY for an `RS485 收发器` query).  This layer only checks lexical /
        functional intent; datasheet-level compatibility remains a separate task.
        """
        haystack = cls._search_haystack(part)
        tokens = cls._query_tokens(query)
        matched_tokens: list[str] = []
        missing_model_anchors: list[str] = []
        score = 0.0

        ident = query.strip().casefold()
        if ident and ident in {(part.lcsc_code or "").casefold(), (part.mpn or "").casefold()}:
            score += 100.0

        for token in tokens:
            if token in haystack:
                matched_tokens.append(token)
                score += 12.0 if cls._is_model_anchor(token) else 3.0
            elif cls._is_model_anchor(token):
                missing_model_anchors.append(token)
                score -= 12.0

        triggered_intents: list[str] = []
        missing_intents: list[str] = []
        query_fold = query.casefold()
        for trigger, aliases, label in cls._INTENT_RULES:
            if not trigger.search(query_fold):
                continue
            triggered_intents.append(label)
            if any(alias.casefold() in haystack for alias in aliases):
                score += 25.0
            else:
                missing_intents.append(label)
                score -= 40.0

        # A missing explicit functional intent/model anchor is a hard gate.  This is
        # intentionally stricter than ordinary keyword ranking: returning no result is
        # safer than presenting a clearly different device class as the top candidate.
        hard_pass = not missing_intents and not missing_model_anchors
        return {
            "score": round(score, 3),
            "hard_pass": hard_pass,
            "matched_tokens": matched_tokens,
            "missing_model_anchors": missing_model_anchors,
            "triggered_intents": triggered_intents,
            "missing_intents": missing_intents,
        }

    @tracked
    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        package: str | None = None,
        brand: str | None = None,
        category_id: int | None = None,
        in_stock: bool | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        sort: int = 0,
        enrich: bool = True,
        page: int = 1,
    ) -> list[Part]:
        query = normalize_c_code(query.strip())
        if not query or not 1 <= max_results <= 50 or not 1 <= page <= 1000:
            raise SZLCSCError("query, max_results (1–50) and page (1–1000) are required", code="INVALID_ARGUMENT")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise SZLCSCError("min_price must not exceed max_price", code="INVALID_ARGUMENT")
        fetch_limit = min(max(max_results, 1), 50)
        params = {
            "keyword": query,
            "currPage": page,
            "pageSize": fetch_limit,
            "sort": sort,
            "stock": bool(in_stock) if in_stock is not None else False,
            "queryBeginPrice": min_price,
            "queryEndPrice": max_price,
            "standard": package,
            "catalog": category_id,
        }
        data = self._get_json(f"{self.SEARCH_HOST}/phone/p/product/search", params, ttl=600)
        parts = [part_from_dict(x) for x in self._product_list(data)]

        if enrich:
            enriched: list[Part] = []
            for part in parts[:fetch_limit]:
                if part.category_id and part.lcsc_code:
                    try:
                        extra = self._category_exact(part.category_id, part.lcsc_code)
                        if extra:
                            merge_part(part, extra)
                    except SZLCSCError as exc:
                        part.warnings.append(f"Category price/stock enrichment failed: {exc.code}")
                enriched.append(part)
            parts = enriched

        if package:
            p = package.casefold()
            parts = [x for x in parts if x.package and p in x.package.casefold()]
        if brand:
            b = brand.casefold()
            parts = [x for x in parts if x.brand and b in x.brand.casefold()]
        if in_stock is True:
            parts = [x for x in parts if x.in_stock is True]
        elif in_stock is False:
            parts = [x for x in parts if x.in_stock is False]
        if min_price is not None:
            parts = [x for x in parts if x.small_batch_price_cny is not None and x.small_batch_price_cny >= min_price]
        if max_price is not None:
            parts = [x for x in parts if x.small_batch_price_cny is not None and x.small_batch_price_cny <= max_price]

        # Re-rank broad SZLCSC full-text results on the client side.  Exact C/MPN
        # lookups remain dominant, while explicit functional intents such as RS485
        # are hard-gated to prevent unrelated transceivers/PHYs from surfacing first.
        relevance = [(x, self.search_relevance(x, query)) for x in parts]
        if any(meta["triggered_intents"] or meta["missing_model_anchors"] for _, meta in relevance):
            relevance = [(x, meta) for x, meta in relevance if meta["hard_pass"]]
        relevance.sort(
            key=lambda item: (
                -item[1]["score"],
                0 if item[0].in_stock is True else 1,
                item[0].small_batch_price_cny if item[0].small_batch_price_cny is not None else 1e18,
                item[0].mpn or item[0].lcsc_code or "",
            )
        )
        return [x for x, _ in relevance[:max_results]]

    def _category_exact(self, category_id: int, c_code: str) -> Part | None:
        data = self._get_json(
            f"{self.LIST_HOST}/phone/p/product/list",
            {
                "catalog": category_id,
                "keyword": c_code,
                "currPage": 1,
                "pageSize": 10,
                "sort": 0,
                "stock": False,
            },
            ttl=300,
        )
        for raw in self._product_list(data):
            p = part_from_dict(raw)
            if (p.lcsc_code or "").casefold() == c_code.casefold():
                return p
        return None

    def _find_exact(self, identifier: str) -> Part | None:
        identifier = normalize_c_code(identifier.strip())
        # Resolve identity before expensive category/price enrichment. A lookup
        # should not fetch twenty unrelated category pages before choosing a part.
        candidates = self.search(identifier, max_results=20, enrich=False)
        ident = identifier.casefold()
        code_matches = [p for p in candidates if (p.lcsc_code or "").casefold() == ident]
        if code_matches:
            return code_matches[0]
        if re.fullmatch(r"c\d+", ident):
            return None
        exact = [
            p
            for p in candidates
            if ident in {(p.lcsc_code or "").casefold(), (p.mpn or "").casefold()}
        ]
        if exact:
            if len({p.lcsc_code for p in exact}) > 1:
                raise SZLCSCError("MPN maps to multiple C-codes; use an exact C-code: " +
                                  ", ".join(p.lcsc_code or "unknown" for p in exact), code="AMBIGUOUS_PART")
            return exact[0]
        return None

    @tracked
    def get_part(self, identifier: str, *, refresh: bool = False, include_static: bool = True) -> Part:
        if not identifier or not identifier.strip():
            raise SZLCSCError("identifier must not be empty", code="INVALID_ARGUMENT")
        part = self._find_exact(identifier)
        if not part:
            raise SZLCSCError(f"No exact SZLCSC part found for {identifier!r}", code="PART_NOT_FOUND")

        if part.category_id and part.lcsc_code:
            try:
                extra = self._category_exact(part.category_id, part.lcsc_code)
                if extra:
                    merge_part(part, extra)
            except SZLCSCError as exc:
                part.warnings.append(f"Category price/stock enrichment failed: {exc.code}")

        if include_static and part.product_sign_id:
            try:
                detail = self._get_json(
                    f"{self.ITEM_HOST}/phone/p/{part.product_sign_id}", ttl=86400
                )
                if isinstance(detail, dict):
                    candidate = part_from_dict(detail)
                    if candidate.lcsc_code and candidate.lcsc_code != part.lcsc_code:
                        raise SZLCSCError("Detail response belongs to a different C-code", code="IDENTITY_MISMATCH")
                    if candidate.mpn and part.mpn and candidate.mpn.casefold() != part.mpn.casefold():
                        raise SZLCSCError("Detail response belongs to a different MPN", code="IDENTITY_MISMATCH")
                    merge_part(part, candidate, dynamic=False)
                    self._extract_detail_attachments(part, detail)
            except SZLCSCError as exc:
                part.warnings.append(f"Detail enrichment failed: {exc.code}")

        if include_static and part.product_url:
            try:
                self._enrich_from_html(part)
            except SZLCSCError as exc:
                part.warnings.append(f"HTML enrichment failed: {exc.code}")
        return part

    @staticmethod
    def _walk(obj: Any):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from SZLCSCClient._walk(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from SZLCSCClient._walk(value)

    def _extract_detail_attachments(self, part: Part, detail: dict[str, Any]) -> None:
        for obj in self._walk(detail):
            for key, value in obj.items():
                if not isinstance(value, str):
                    continue
                low = value.lower()
                key_low = key.lower()
                if (
                    not part.datasheet_url
                    and ("pdf" in key_low or "datasheet" in key_low or low.endswith(".pdf"))
                ):
                    candidate_url = normalize_datasheet_url(value)
                    if candidate_url:
                        part.datasheet_url = candidate_url
                if (
                    not part.image_url
                    and key_low in {"image", "imageurl", "productimage", "productimageurl"}
                    and value.startswith("http")
                ):
                    part.image_url = value

    @staticmethod
    def _find_scalar(obj: Any, wanted_key: str) -> Any | None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == wanted_key and value not in (None, ""):
                    return value
                found = SZLCSCClient._find_scalar(value, wanted_key)
                if found not in (None, ""):
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = SZLCSCClient._find_scalar(value, wanted_key)
                if found not in (None, ""):
                    return found
        return None

    def _annex_datasheet(self, c_code: str, annex_number: str) -> str | None:
        try:
            data = self._get_json(
                f"{self.SEARCH_HOST}/product/showProductPDFAndPCB",
                {"productCode": c_code, "annexNumber": annex_number},
                ttl=86400,
            )
        except SZLCSCError:
            return None
        for obj in self._walk(data):
            for key, value in obj.items():
                if not isinstance(value, str):
                    continue
                if key.casefold() in {"pdflink", "pdfurl", "datasheet", "datasheeturl"} or ".pdf" in value.casefold():
                    candidate_url = normalize_datasheet_url(value)
                    if candidate_url:
                        return candidate_url
        return None

    def _enrich_from_html(self, part: Part) -> None:
        assert part.product_url
        key = self.cache.make_key("GETHTML", part.product_url, None)
        html = None if bypass.get() else self.cache.get(key, 86400)
        if html is None:
            try:
                def validate_page(url):
                    parsed = urlparse(url)
                    if parsed.scheme != "https" or parsed.hostname != "item.szlcsc.com":
                        raise SZLCSCError("Product page redirect left the domestic item host", code="ACCESS_DENIED")
                with safe_get(self.session, part.product_url, validate=validate_page, timeout=self.timeout) as r:
                    r.raise_for_status()
                    html = r.text
            except (requests.RequestException, SourcingError) as exc:
                raise SZLCSCError("Product page enrichment unavailable", code=getattr(exc, "code", "NETWORK_ERROR")) from exc
            else:
                self.cache.set(key, part.product_url, html)
        soup = BeautifulSoup(html, "html.parser")

        if not part.datasheet_url:
            for a in soup.find_all("a", href=True):
                href = str(a["href"])
                text = a.get_text(" ", strip=True)
                if ".pdf" in href.lower() or "数据手册" in text or "规格书" in text:
                    normalized = normalize_datasheet_url(href)
                    part.datasheet_url = normalized if normalized and urlparse(normalized).scheme else urljoin(part.product_url, href)
                    break

        for row in soup.find_all(["tr", "li"]):
            cells = [
                x.get_text(" ", strip=True)
                for x in row.find_all(["th", "td", "span", "div"], recursive=False)
            ]
            cells = [x for x in cells if x]
            if len(cells) == 2 and len(cells[0]) <= 40:
                part.parameters.setdefault(cells[0].rstrip("：:"), cells[1])

        node = soup.find("script", id="__NEXT_DATA__")
        if node and node.string:
            try:
                payload = json.loads(node.string)
                self._extract_detail_attachments(part, payload)
                # SZLCSC also exposes an annex identifier in NEXT_DATA. The public
                # showProductPDFAndPCB endpoint can provide the PDF when detail.out_pdf_url is empty.
                if not part.datasheet_url and part.lcsc_code:
                    annex_number = self._find_scalar(payload, "pdfDESProductId")
                    if annex_number not in (None, ""):
                        part.datasheet_url = self._annex_datasheet(part.lcsc_code, str(annex_number))
                # Some pages contain the actual product object inside NEXT_DATA.
                for obj in self._walk(payload):
                    if isinstance(obj, dict) and any(k in obj for k in ("productCode", "code", "model")):
                        candidate = part_from_dict(obj)
                        if ((candidate.lcsc_code and candidate.lcsc_code == part.lcsc_code)
                                or (candidate.mpn and candidate.mpn == part.mpn)):
                            merge_part(part, candidate, dynamic=False)
            except ValueError:
                pass

    def parametric_search(
        self,
        query: str,
        constraints: dict[str, str],
        *,
        max_results: int = 10,
        fetch_pool: int = 20,
        package: str | None = None,
        brand: str | None = None,
        in_stock: bool | None = True,
        max_price: float | None = None,
    ) -> list[dict[str, Any]]:
        pool = self.search(
            query,
            max_results=min(max(fetch_pool, max_results), 50),
            package=package,
            brand=brand,
            in_stock=in_stock,
            max_price=max_price,
            enrich=True,
        )
        evaluated: list[dict[str, Any]] = []
        for shallow in pool:
            part = shallow
            if part.lcsc_code:
                try:
                    part = self.get_part(part.lcsc_code)
                except SZLCSCError:
                    pass
            report = evaluate_constraints(part, constraints)
            score = report["matched"] / max(report["total"], 1)
            evaluated.append({"part": part, "match": report, "match_score": score})

        def key(item: dict[str, Any]):
            part: Part = item["part"]
            report = item["match"]
            # Complete matches first, then fraction matched, then in-stock, then known low 1+ price.
            price = part.small_batch_price_cny if part.small_batch_price_cny is not None else 1e18
            return (
                0 if report["all_matched"] else 1,
                -item["match_score"],
                0 if part.in_stock is True else 1,
                price,
                part.mpn or part.lcsc_code or "",
            )

        evaluated.sort(key=key)
        return evaluated[:max_results]

    def alternatives(
        self, identifier: str, *, max_results: int = 10, in_stock_only: bool = True
    ) -> list[Part]:
        source = self.get_part(identifier)
        if not source.lcsc_code:
            raise SZLCSCError("Alternatives require an SZLCSC C-number")
        data = self._get_json(
            f"{self.ICSEARCH_HOST}/phone/p/sub/list",
            {"mainCode": source.lcsc_code},
            ttl=3600,
        )
        raws = self._product_list(data)
        if not raws and isinstance(data, dict):
            for obj in self._walk(data):
                if any(k in obj for k in ("code", "productCode", "model", "productModel")):
                    raws.append(obj)
        seen: set[str] = set()
        out: list[Part] = []
        for raw in raws:
            candidate = part_from_dict(raw)
            identity = (candidate.lcsc_code or candidate.mpn or "").casefold()
            if not identity or identity in seen or identity == source.lcsc_code.casefold():
                continue
            seen.add(identity)
            if candidate.lcsc_code:
                try:
                    candidate = self.get_part(candidate.lcsc_code)
                except SZLCSCError:
                    pass
            out.append(candidate)
            if len(out) >= max_results * 3:
                break
        if in_stock_only:
            out = [p for p in out if p.in_stock is True]
        return self.rank_alternatives(source, out)[:max_results]

    @staticmethod
    def alternative_score(source: Part, candidate: Part) -> dict[str, Any]:
        score = 0.0
        reasons: list[str] = []
        if source.package and candidate.package:
            if source.package.casefold() == candidate.package.casefold():
                score += 5.0
                reasons.append("封装相同")
            else:
                reasons.append("封装不同")
        if candidate.in_stock is True:
            score += 3.0
            reasons.append("国内有货")
        elif candidate.in_stock is False:
            reasons.append("国内无货")
        else:
            reasons.append("库存未知")

        common = set(source.parameters) & set(candidate.parameters)
        exact_parameter_matches = 0
        for key in common:
            if str(source.parameters[key]).strip().casefold() == str(candidate.parameters[key]).strip().casefold():
                exact_parameter_matches += 1
        score += min(exact_parameter_matches * 0.4, 4.0)
        if exact_parameter_matches:
            reasons.append(f"{exact_parameter_matches} 个同名参数完全一致")
        return {
            "score": round(score, 3),
            "reasons": reasons,
            "package_match": (
                source.package.casefold() == candidate.package.casefold()
                if source.package and candidate.package
                else None
            ),
            "in_stock": candidate.in_stock,
            "exact_parameter_matches": exact_parameter_matches,
            "warning": "仅为候选排序，不能据此声明 pin-to-pin 或电气完全兼容；必须核对 datasheet。",
        }

    @staticmethod
    def rank_alternatives(source: Part, candidates: list[Part]) -> list[Part]:
        def sort_key(part: Part):
            meta = SZLCSCClient.alternative_score(source, part)
            price = part.small_batch_price_cny if part.small_batch_price_cny is not None else 1e18
            return (-meta["score"], price, part.mpn or part.lcsc_code or "")

        return sorted(candidates, key=sort_key)
