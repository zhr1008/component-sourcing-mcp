"""Bounded requirement/family discovery, separate from exact identity resolution."""
from __future__ import annotations

import re
from collections import deque

from .errors import SourcingError
from .freshness import bypass
from .parametric import evaluate_constraints
from .product_page import unknown_procurement


def family_query(mpn: str) -> str | None:
    """Conservative discovery prefix, never an exact manufacturer identity."""
    for pattern in (r"^(STM32[A-Z]\d{1,2})", r"^(GD32[A-Z]\d{3})", r"^([A-Z]{2,}-[A-Z]+\d{3,})", r"^([A-Z]{2,}\d{3,})"):
        match = re.match(pattern, mpn.strip(), re.I)
        if match:
            return match[1]
    return None


def query_plan(client, query: str, families: list[str]) -> list[str]:
    terms = [query.strip(), *families]
    tokens = client._query_tokens(query)
    anchors = [t for t in tokens if client._is_model_anchor(t) and t not in {"can", "rs485", "rs-485", "rs422", "rs-422"}]
    if anchors:
        terms.extend(family_query(t) for t in anchors)
    else:
        aliases = {
            "CAN": ["CAN收发器", "CAN transceiver"],
            "RS485": ["RS485收发器", "RS485 transceiver"],
            "RS422": ["RS422收发器"],
            "MCU": ["微控制器", "MCU"],
            "AFE": ["模拟前端", "AFE"],
            "ADC": ["模数转换器", "ADC"],
            "DAC": ["数模转换器", "DAC"],
            "opamp": ["运算放大器", "operational amplifier"],
            "voltage_reference": ["电压基准", "voltage reference"],
        }
        for trigger, _, label in client._INTENT_RULES:
            if trigger.search(query):
                if label == "CAN" and re.search(r"隔离|isolat", query, re.I):
                    terms.extend(["隔离式CAN收发器", "isolated CAN"])
                else:
                    terms.extend(aliases.get(label, []))
    return list(dict.fromkeys(t for t in terms if t))


def requirement_report(client, part, query: str) -> list[dict]:
    haystack = client._search_haystack(part)
    rows = []
    checks = (
        (r"隔离|isolat", "isolation", r"隔离|\bisolat(?:ed|ion|ing)\b", r"非隔离|non[- ]?isolat"),
        (r"低功耗|low[- ]?power", "low_power", r"低功耗|low[- ]?power", None),
        (r"can[- ]?fd", "CAN-FD", r"can[- ]?fd", None),
    )
    for trigger, label, positive, negative in checks:
        if re.search(trigger, query, re.I):
            status = "mismatched" if negative and re.search(negative, haystack, re.I) else (
                "catalog_claim" if re.search(positive, haystack, re.I) else "unknown"
            )
            rows.append({"requirement": label, "status": status, "evidence_level": "catalog_only"})
    for match in re.finditer(r"AEC[-_ ]?Q\d+|ASIL[-_ ]?[ABCD]|SIL[-_ ]?\d+|车规|automotive", query, re.I):
        rows.append({"requirement": match[0].upper(), "status": "unknown",
                     "reason": "Requires manufacturer evidence for the exact ordering code; names/suffixes are insufficient"})
    if re.search(r"便宜|低价|inexpensive|cheap|affordable", query, re.I):
        rows.append({"requirement": "cost", "status": "unknown",
                     "reason": "Specify currency, budget and quantity; compare verified quantity quotes"})
    return rows


def discover(client, query, *, max_results=10, family_queries=None, constraints=None,
             package=None, brand=None, in_stock=None, max_price=None, max_queries=6,
             pages_per_query=2, refresh=False, enrich=True):
    if (not isinstance(query, str) or not query.strip() or len(query) > 200
            or type(max_results) is not int or not 1 <= max_results <= 50
            or type(max_queries) is not int or not 1 <= max_queries <= 8
            or type(pages_per_query) is not int or not 1 <= pages_per_query <= 3):
        raise SourcingError("Invalid discovery query or request budget", code="INVALID_ARGUMENT")
    families = family_queries or []
    if (not isinstance(families, list) or len(families) > 4
            or any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in families)):
        raise SourcingError("family_queries accepts at most four nonempty short queries", code="INVALID_ARGUMENT")
    if not enrich and (in_stock is not None or max_price is not None):
        raise SourcingError("Commercial filters require current detail enrichment", code="INVALID_ARGUMENT")
    pending = deque(query_plan(client, query, families))
    seen_queries = set()
    queries, errors = [], []
    candidates = {}
    coverage = {"queries": queries, "query_errors": errors, "complete": False,
                "max_queries": max_queries, "pages_per_query": pages_per_query,
                "page_size": client.SEARCH_PAGE_SIZE, "scope": "Bounded supplier search; not an exhaustive market survey"}
    token = bypass.set(bypass.get() or refresh)
    try:
        while pending and len(seen_queries) < max_queries:
            term = pending.popleft()
            if term.casefold() in seen_queries:
                continue
            seen_queries.add(term.casefold())
            for page in range(1, pages_per_query + 1):
                try:
                    window = client.search(term, max_results=50, page=page, enrich=False)
                except SourcingError as exc:
                    errors.append({"query": term, "page": page, "code": exc.code})
                    if exc.code in {"ACCESS_DENIED", "RATE_LIMITED"}:
                        pending.clear()  # respect shared host cooldown; no alternate-query bypass
                        coverage["stopped_on_access_limit"] = True
                    break
                meta = getattr(window, "coverage", {})
                raw_count = meta.get("raw_count", len(window))
                queries.append({"query": term, "page": page, "raw_count": raw_count,
                                "relevant_count": len(window), "has_more": meta.get("has_more"),
                                "more_possible": meta.get("has_more") is not False})
                for part in window:
                    relevance = client.search_relevance(part, query)
                    # A shorter family query may suggest another suffix, but must
                    # retain explicit function gates and label the identity mismatch.
                    if relevance["missing_intents"]:
                        continue
                    if relevance["missing_model_anchors"] and not any(
                        (family_query(anchor) or anchor).casefold() in (part.mpn or "").casefold()
                        for anchor in relevance["missing_model_anchors"]
                    ):
                        continue
                    key = (part.lcsc_code or part.mpn or "").casefold()
                    if not key:
                        continue
                    if key not in candidates:
                        candidates[key] = {"part": part, "found_by": [], "relevance": relevance}
                    candidates[key]["found_by"].append({"query": term, "page": page})
                    prefix = family_query(part.mpn or "")
                    if prefix and prefix.casefold() not in seen_queries and prefix not in pending:
                        pending.append(prefix)
                if coverage.get("stopped_on_access_limit") or meta.get("has_more") is False or raw_count < client.SEARCH_PAGE_SIZE:
                    break
            if coverage.get("stopped_on_access_limit"):
                break

        def rank(row):
            requirements = requirement_report(client, row["part"], query)
            contradicted = sum(r["status"] == "mismatched" for r in requirements)
            unsupported = sum(r["status"] == "unknown" for r in requirements)
            # A related automotive catalog description helps discovery only. It
            # never changes the qualification status from unknown.
            automotive_requested = re.search(r"aec[-_ ]?q\d+|车规|automotive", query, re.I)
            automotive_claim = bool(automotive_requested and re.search(
                r"aec[-_ ]?q\d+|车规|汽车|automotive", client._search_haystack(row["part"]), re.I
            ))
            return (contradicted, unsupported, -int(automotive_claim), -row["relevance"]["score"], row["part"].mpn or "")

        ranked = sorted(candidates.values(), key=rank)
        # Cover package/pin variants before spending all detail calls on one package.
        groups, deferred, ordered = set(), [], []
        for row in ranked:
            part = row["part"]
            group = ((family_query(part.mpn or "") or part.mpn or "").casefold(), (part.package or "").casefold())
            if group in groups:
                deferred.append(row)
            else:
                groups.add(group)
                ordered.append(row)
        ordered.extend(deferred)
        results = []
        detail_count = 0
        excluded = {"static_filters": 0, "commercial_filters": 0, "commercial_unknown": 0}
        for row in ordered:
            part = row["part"]
            if not client._matches_filters(part, package=package, brand=brand):
                excluded["static_filters"] += 1
                continue
            if detail_count >= 50:
                break
            detail_count += 1
            if enrich and part.lcsc_code:
                try:
                    part = client.get_part(part.lcsc_code)
                except SourcingError as exc:
                    unknown_procurement(part, exc.code)
            if not client._matches_filters(part, package=package, brand=brand,
                                           in_stock=in_stock, max_price=max_price):
                unknown = ((in_stock is not None and part.in_stock is None)
                           or (max_price is not None and part.small_batch_price_cny is None))
                excluded["commercial_unknown" if unknown else "commercial_filters"] += 1
                continue
            results.append({
                "part": part, "found_by": row["found_by"],
                "exact_identity": query.strip().casefold() in {(part.mpn or "").casefold(), (part.lcsc_code or "").casefold()},
                "requirements": requirement_report(client, part, query),
                "constraint_match": evaluate_constraints(part, constraints) if constraints else None,
                "manufacturer_verification_required": True,
            })
            if len(results) >= max_results:
                break
        coverage.update({"unique_candidates": len(candidates), "detail_inspected": detail_count,
                         "returned_count": len(results), "excluded": excluded,
                         "remaining_candidates": max(0, len(ordered) - detail_count - excluded["static_filters"]),
                         "query_budget_reached": bool(pending) and len(seen_queries) >= max_queries})
        response = {"results": results, "count": len(results), "coverage": coverage,
                    "guidance": "Candidates may have different suffixes/pin counts. Verify exact MPN, datasheet and quantity quote. No result is not proof of unavailability."}
        if errors and not queries:
            response.update({"ok": False, "error": {"code": errors[0]["code"],
                             "message": "Discovery queries failed; inspect coverage.query_errors", "retryable": False}})
        return response
    finally:
        bypass.reset(token)
