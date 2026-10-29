from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ad_catalog import VERIFIED_EVIDENCE_STATUSES, load_ad_catalog, save_ad_catalog
from .client import SZLCSCClient, SZLCSCError
from .downloader import _download
from .library_base import LibraryError
from .models import Part

_MPNISH_RE = re.compile(r"^(?=.{3,80}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9._+/#()-]*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _part_payload(part: Part) -> dict[str, Any]:
    return {
        "lcsc_code": part.lcsc_code,
        "mpn": part.mpn,
        "name": part.name,
        "brand": part.brand,
        "package": part.package,
        "category": part.category,
        "stock": part.stock,
        "total_stock": part.total_stock,
        "in_stock": part.in_stock,
        "small_batch_price_cny": part.small_batch_price_cny,
        "min_order_qty": part.min_order_qty,
        "order_multiple": part.order_multiple,
        "packaging": part.packaging,
        "datasheet_url": part.datasheet_url,
        "product_url": part.product_url,
        "parameters": dict(part.parameters or {}),
        "price_tiers": [
            {
                "min_qty": tier.min_qty,
                "max_qty": tier.max_qty,
                "unit_price_cny": tier.unit_price_cny,
                "currency": tier.currency,
            }
            for tier in part.price_tiers
        ],
        "source": "szlcsc.com",
    }


def _unique_exact_mpn(parts: list[Part], mpn: str, package_hint: str | None) -> tuple[Part | None, list[Part]]:
    exact = [part for part in parts if _norm(part.mpn) == _norm(mpn)]
    if package_hint:
        package_exact = [part for part in exact if _norm(part.package) == _norm(package_hint)]
        if len(package_exact) == 1:
            return package_exact[0], exact
    unique_codes = {str(part.lcsc_code or "").upper() for part in exact if part.lcsc_code}
    if len(exact) == 1 or (exact and len(unique_codes) == 1):
        return exact[0], exact
    return None, exact


def resolve_symbol_part(
    client: SZLCSCClient,
    symbol: dict[str, Any],
    *,
    search_limit: int = 12,
    allow_symbol_name_exact_mpn: bool = True,
) -> dict[str, Any]:
    hints = symbol.get("identity_hints") or {}
    c_code = str(hints.get("lcsc_code") or "").strip().upper()
    mpn = str(hints.get("mpn") or "").strip()
    package_hint = str(hints.get("package") or "").strip() or None

    if c_code:
        try:
            part = client.get_part(c_code)
        except SZLCSCError as exc:
            return {"status": "lookup_error", "method": "exact_c_code", "error": str(exc), "candidates": []}
        return {"status": "resolved", "method": "exact_c_code", **_part_payload(part), "candidates": []}

    query = mpn
    method = "exact_mpn_parameter"
    if not query and allow_symbol_name_exact_mpn:
        symbol_name = str(symbol.get("name") or "").strip()
        if _MPNISH_RE.fullmatch(symbol_name):
            query = symbol_name
            method = "exact_mpn_symbol_name"

    if not query:
        return {
            "status": "unresolved",
            "method": "insufficient_identity",
            "reason": "no C-code or trustworthy MPN hint; fuzzy symbol-name search is not auto-bound",
            "candidates": [],
        }

    try:
        candidates = client.search(query, max_results=max(1, min(search_limit, 50)), enrich=True)
    except SZLCSCError as exc:
        return {"status": "lookup_error", "method": method, "error": str(exc), "candidates": []}

    chosen, exact = _unique_exact_mpn(candidates, query, package_hint)
    candidate_payloads = [
        {
            "lcsc_code": part.lcsc_code,
            "mpn": part.mpn,
            "brand": part.brand,
            "package": part.package,
            "category": part.category,
            "stock": part.stock,
            "small_batch_price_cny": part.small_batch_price_cny,
            "product_url": part.product_url,
        }
        for part in exact[:20]
    ]
    if chosen and chosen.lcsc_code:
        try:
            chosen = client.get_part(chosen.lcsc_code)
        except SZLCSCError:
            pass
        return {"status": "resolved", "method": method, **_part_payload(chosen), "candidates": candidate_payloads}
    if exact:
        return {
            "status": "ambiguous",
            "method": method,
            "reason": "multiple exact-MPN SZLCSC records remain; package/C-code confirmation required",
            "candidates": candidate_payloads,
        }
    return {
        "status": "unresolved",
        "method": method,
        "reason": "SZLCSC search returned no exact MPN match; broad search results were not auto-bound",
        "candidates": [],
    }


def enrich_ad_catalog(
    client: SZLCSCClient,
    catalog_root: str | Path,
    *,
    symbol_ids: Iterable[str] | None = None,
    force: bool = False,
    search_limit: int = 12,
) -> dict[str, Any]:
    catalog = load_ad_catalog(catalog_root)
    wanted = {str(item) for item in (symbol_ids or []) if str(item)}
    rows: list[dict[str, Any]] = []
    for asset_id, symbol in sorted(catalog.get("symbols", {}).items()):
        if wanted and asset_id not in wanted:
            continue
        existing = symbol.get("enrichment") or {}
        if not force and existing.get("status") == "resolved" and existing.get("lcsc_code"):
            rows.append({"asset_id": asset_id, "status": "cached", "lcsc_code": existing.get("lcsc_code"), "mpn": existing.get("mpn")})
            continue
        result = resolve_symbol_part(client, symbol, search_limit=search_limit)
        result["last_checked_at"] = _now()
        old_identity = (_norm(existing.get("lcsc_code")), _norm(existing.get("mpn")), _norm(existing.get("package")))
        new_identity = (_norm(result.get("lcsc_code")), _norm(result.get("mpn")), _norm(result.get("package")))
        verification = symbol.get("verification") or {}
        previous_source = verification.get("datasheet_url") or existing.get("datasheet_url")
        source_changed = bool(previous_source) and previous_source != result.get("datasheet_url")
        verification_current = verification.get("status") in VERIFIED_EVIDENCE_STATUSES
        if verification_current:
            if existing and old_identity != new_identity:
                verification = {
                    **verification,
                    "status": "stale_identity_changed",
                    "stale_at": _now(),
                    "reason": "resolved C-code/MPN/package changed during enrichment",
                }
                symbol["verification"] = verification
            elif source_changed:
                symbol["verification"] = {
                    **verification,
                    "status": "stale_datasheet_changed",
                    "stale_at": _now(),
                    "reason": "datasheet URL changed during enrichment",
                }
        # Preserve local datasheet path across a metadata refresh when URL/identity is unchanged.
        same_source = existing.get("datasheet_url") == result.get("datasheet_url")
        path_source = existing.get("datasheet_path_url")
        path_source_matches = not path_source or path_source == result.get("datasheet_url")
        verification_source_changed = verification_current and source_changed
        if existing.get("datasheet_path") and old_identity == new_identity and same_source and path_source_matches and not verification_source_changed:
            result["datasheet_path"] = existing.get("datasheet_path")
            if existing.get("datasheet_path_url"):
                result["datasheet_path_url"] = existing["datasheet_path_url"]
        if existing.get("datasheet_download_required") or verification_source_changed or (existing and (not same_source or not path_source_matches)):
            result["datasheet_download_required"] = True
        symbol["enrichment"] = result
        rows.append({
            "asset_id": asset_id,
            "symbol": symbol.get("name"),
            "library_path": symbol.get("library_path"),
            "status": result.get("status"),
            "method": result.get("method"),
            "lcsc_code": result.get("lcsc_code"),
            "mpn": result.get("mpn"),
            "candidate_count": len(result.get("candidates") or []),
        })
    save_ad_catalog(catalog_root, catalog)
    return {
        "ok": all(row.get("status") not in {"lookup_error"} for row in rows),
        "count": len(rows),
        "resolved": sum(1 for row in rows if row.get("status") in {"resolved", "cached"}),
        "ambiguous": sum(1 for row in rows if row.get("status") == "ambiguous"),
        "unresolved": sum(1 for row in rows if row.get("status") == "unresolved"),
        "lookup_errors": sum(1 for row in rows if row.get("status") == "lookup_error"),
        "results": rows,
        "binding_policy": "auto-bind only exact C-code or exact MPN; fuzzy symbol-name results never become identity automatically",
    }


def sync_ad_catalog_datasheets(
    client: SZLCSCClient,
    catalog_root: str | Path,
    *,
    symbol_ids: Iterable[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    catalog = load_ad_catalog(catalog_root)
    root = Path(catalog_root).expanduser().resolve()
    out = root / "datasheets"
    out.mkdir(parents=True, exist_ok=True)
    wanted = {str(item) for item in (symbol_ids or []) if str(item)}
    downloaded_by_source: dict[tuple[str, str], str] = {}
    results: list[dict[str, Any]] = []

    for asset_id, symbol in sorted(catalog.get("symbols", {}).items()):
        if wanted and asset_id not in wanted:
            continue
        enrichment = symbol.get("enrichment") or {}
        if enrichment.get("status") != "resolved" or not enrichment.get("lcsc_code"):
            continue
        code = str(enrichment["lcsc_code"]).upper()
        mpn = str(enrichment.get("mpn") or "unknown").replace("/", "_").replace("\\", "_")
        legacy_target = out / f"{code}-{mpn}.pdf"
        url = enrichment.get("datasheet_url")
        source_was_missing = not url
        if not url:
            try:
                part = client.get_part(code)
                url = part.datasheet_url
                enrichment["datasheet_url"] = url
            except SZLCSCError as exc:
                results.append({"asset_id": asset_id, "lcsc_code": code, "status": "error", "error": str(exc)})
                continue
        if not url:
            results.append({"asset_id": asset_id, "lcsc_code": code, "status": "no_datasheet"})
            continue
        url = str(url)
        verification = symbol.get("verification") or {}
        verified_source = verification.get("datasheet_url")
        verification_current = verification.get("status") in VERIFIED_EVIDENCE_STATUSES
        source_conflict = verification_current and verified_source and verified_source != url
        path_source = enrichment.get("datasheet_path_url")
        if source_conflict:
            symbol["verification"] = {
                **verification,
                "status": "stale_datasheet_changed",
                "stale_at": _now(),
                "reason": "datasheet URL changed before download",
            }
        if source_conflict or (path_source and path_source != url):
            enrichment.pop("datasheet_path", None)
            enrichment.pop("datasheet_path_url", None)
            enrichment["datasheet_download_required"] = True
        source_key = (code, url)
        if source_key in downloaded_by_source:
            downloaded_path = downloaded_by_source[source_key]
            enrichment["datasheet_path"] = downloaded_path
            enrichment["datasheet_path_url"] = url
            enrichment.pop("datasheet_download_required", None)
            results.append({"asset_id": asset_id, "lcsc_code": code, "status": "shared", "path": downloaded_path})
            continue
        path_url = enrichment.get("datasheet_path_url")
        legacy_trusted = (
            enrichment.get("datasheet_path") == str(legacy_target)
            and (not path_url or path_url == url)
            and not source_was_missing
            and not enrichment.get("datasheet_download_required")
        )
        source_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        target = legacy_target if legacy_trusted else out / f"{code}-{mpn}-{source_hash}.pdf"
        try:
            download_required = force or source_was_missing or enrichment.get("datasheet_download_required")
            _download(client.session, url, target, client.timeout, pdf=True, force=bool(download_required))
        except Exception as exc:
            results.append({"asset_id": asset_id, "lcsc_code": code, "status": "error", "error": str(exc)})
            continue
        path_str = str(target)
        enrichment["datasheet_path"] = path_str
        enrichment["datasheet_path_url"] = url
        enrichment.pop("datasheet_download_required", None)
        enrichment["datasheet_downloaded_at"] = _now()
        enrichment["datasheet_evidence_status"] = "pending_agent_review"
        downloaded_by_source[source_key] = path_str
        results.append({"asset_id": asset_id, "lcsc_code": code, "status": "ready", "path": path_str})

    save_ad_catalog(catalog_root, catalog)
    return {
        "ok": not any(row.get("status") == "error" for row in results),
        "count": len(results),
        "ready": sum(1 for row in results if row.get("status") in {"ready", "shared"}),
        "no_datasheet": sum(1 for row in results if row.get("status") == "no_datasheet"),
        "errors": sum(1 for row in results if row.get("status") == "error"),
        "results": results,
        "authority_policy": "downloaded Datasheet is the sole authority for pin/package/electrical verification; SZLCSC/EasyEDA CAD remains auxiliary",
    }


def _pin_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        number = str(row.get("number") or "").strip()
        if number:
            result[number.casefold()] = str(row.get("name") or "").strip()
    return result


def build_verification_queue(
    catalog_root: str | Path,
    *,
    symbol_ids: Iterable[str] | None = None,
    include_verified: bool = False,
) -> dict[str, Any]:
    catalog = load_ad_catalog(catalog_root)
    wanted = {str(item) for item in (symbol_ids or []) if str(item)}
    queue: list[dict[str, Any]] = []
    for asset_id, symbol in sorted(catalog.get("symbols", {}).items()):
        if wanted and asset_id not in wanted:
            continue
        enrichment = symbol.get("enrichment") or {}
        verification = symbol.get("verification") or {}
        if enrichment.get("status") != "resolved":
            continue
        if not include_verified and verification.get("status") == "verified":
            continue
        linked: list[dict[str, Any]] = []
        for link in symbol.get("resolved_footprint_links") or []:
            candidates = link.get("candidates") or []
            linked.append({
                "name": link.get("name"),
                "status": link.get("status"),
                "footprints": [
                    {
                        "asset_id": fp_id,
                        "name": (catalog.get("footprints", {}).get(fp_id) or {}).get("name"),
                        "library_path": (catalog.get("footprints", {}).get(fp_id) or {}).get("library_path"),
                        "pads": (catalog.get("footprints", {}).get(fp_id) or {}).get("pads") or [],
                        "geometry": (catalog.get("footprints", {}).get(fp_id) or {}).get("geometry") or {},
                    }
                    for fp_id in candidates
                ],
            })
        queue.append({
            "asset_id": asset_id,
            "symbol": symbol.get("name"),
            "schlib": symbol.get("library_path"),
            "lcsc_code": enrichment.get("lcsc_code"),
            "mpn": enrichment.get("mpn"),
            "brand": enrichment.get("brand"),
            "szlcsc_package": enrichment.get("package"),
            "datasheet_url": enrichment.get("datasheet_url"),
            "datasheet_path": enrichment.get("datasheet_path"),
            "symbol_pins": symbol.get("pins") or [],
            "footprint_links": linked,
            "current_verification": verification,
            "required_evidence": {
                "source": "datasheet",
                "manufacturer": "manufacturer stated by Datasheet",
                "mpn": "exact orderable/model number covered by Datasheet",
                "package": "Datasheet package name/code",
                "pins": [{"number": "1", "name": "..."}],
                "terminal_numbers": ["1", "2"],
                "mechanical_checks": [
                    {"name": "pin_count/pad_count/pitch/body/EP/land_pattern", "expected": "Datasheet value", "actual": "measured local footprint value", "pass": True, "page": "Datasheet page"}
                ],
                "pages": ["page numbers used as evidence"],
            },
        })
    return {
        "count": len(queue),
        "queue": queue,
        "authority_policy": "only Datasheet evidence may set final verified/pass; SZLCSC/EasyEDA metadata can never do so alone",
    }


def record_datasheet_verification(
    catalog_root: str | Path,
    *,
    asset_id: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    catalog = load_ad_catalog(catalog_root)
    symbol = (catalog.get("symbols") or {}).get(asset_id)
    if not symbol:
        raise LibraryError(f"unknown symbol asset_id: {asset_id}")
    enrichment = symbol.get("enrichment") or {}
    if enrichment.get("status") != "resolved":
        raise LibraryError("symbol must have resolved SZLCSC identity before Datasheet verification")
    if _norm(evidence.get("source")) != "datasheet":
        raise LibraryError("verification evidence source must be 'datasheet'")
    pages = evidence.get("pages") or []
    if not pages:
        raise LibraryError("Datasheet verification requires source page references")

    issues: list[dict[str, Any]] = []
    resolved_mpn = _norm(enrichment.get("mpn"))
    evidence_mpn = _norm(evidence.get("mpn"))
    resolved_package = _norm(enrichment.get("package"))
    evidence_package = _norm(evidence.get("package"))
    if not resolved_mpn or not evidence_mpn:
        issues.append({"kind": "insufficient_identity_mpn_evidence"})
    if not resolved_package or not evidence_package:
        issues.append({"kind": "insufficient_package_evidence"})
    if resolved_mpn and evidence_mpn and resolved_mpn != evidence_mpn:
        issues.append({"kind": "identity_mpn_conflict", "resolved": enrichment.get("mpn"), "datasheet": evidence.get("mpn")})
    if resolved_package and evidence_package and resolved_package != evidence_package:
        issues.append({"kind": "identity_package_conflict", "resolved": enrichment.get("package"), "datasheet": evidence.get("package")})
    hint_package = (symbol.get("identity_hints") or {}).get("package")
    if _norm(hint_package) and evidence_package and _norm(hint_package) != evidence_package:
        issues.append({"kind": "hint_package_conflict", "hint": hint_package, "datasheet": evidence.get("package")})

    datasheet_pins = _pin_map(list(evidence.get("pins") or []))
    symbol_pins = _pin_map(list(symbol.get("pins") or []))
    pin_numbers_match = bool(datasheet_pins) and set(datasheet_pins) == set(symbol_pins)
    if not datasheet_pins:
        issues.append({"kind": "insufficient_datasheet_pin_evidence"})
    elif not pin_numbers_match:
        issues.append({
            "kind": "symbol_pin_number_mismatch",
            "symbol_numbers": sorted(symbol_pins),
            "datasheet_numbers": sorted(datasheet_pins),
        })

    # Pin names are advisory because library naming can use aliases (VSS/GND, GPIO0/IO0).
    pin_name_differences = [
        {"number": number, "symbol": symbol_pins.get(number), "datasheet": datasheet_pins.get(number)}
        for number in sorted(set(datasheet_pins) & set(symbol_pins))
        if _norm(symbol_pins.get(number)) != _norm(datasheet_pins.get(number))
    ]

    terminal_numbers = {
        str(value).strip().casefold()
        for value in (evidence.get("terminal_numbers") or [row.get("number") for row in evidence.get("pins") or []])
        if str(value or "").strip()
    }
    linked = [link for link in symbol.get("resolved_footprint_links") or [] if link.get("status") == "resolved"]
    footprint_checks: list[dict[str, Any]] = []
    for link in linked:
        fp_id = (link.get("candidates") or [None])[0]
        footprint = (catalog.get("footprints") or {}).get(fp_id) or {}
        footprint_package = _norm(footprint.get("package"))
        if footprint_package and evidence_package and footprint_package != evidence_package:
            issues.append({"kind": "footprint_package_conflict", "footprint_asset_id": fp_id, "footprint": footprint.get("package"), "datasheet": evidence.get("package")})
        pads = {
            str(row.get("number") or "").strip().casefold()
            for row in footprint.get("pads") or []
            if str(row.get("number") or "").strip()
        }
        match = bool(terminal_numbers) and pads == terminal_numbers
        footprint_checks.append({
            "footprint_asset_id": fp_id,
            "name": footprint.get("name"),
            "library_path": footprint.get("library_path"),
            "pad_numbers_match": match,
            "pad_numbers": sorted(pads),
            "datasheet_terminal_numbers": sorted(terminal_numbers),
        })
        if not match:
            issues.append({"kind": "footprint_pad_number_mismatch", "footprint_asset_id": fp_id})
    if not linked:
        issues.append({"kind": "no_uniquely_resolved_footprint_link"})

    mechanical_checks = list(evidence.get("mechanical_checks") or [])
    if any(not isinstance(row, dict) for row in mechanical_checks):
        raise LibraryError("mechanical_checks must contain evidence objects")
    failed_mechanical = [row for row in mechanical_checks if row.get("pass") is False]
    missing_page_checks = [row for row in mechanical_checks if row.get("page") in (None, "")]
    if failed_mechanical:
        issues.append({"kind": "mechanical_datasheet_check_failed", "count": len(failed_mechanical)})
    if mechanical_checks and missing_page_checks:
        issues.append({"kind": "mechanical_check_missing_page", "count": len(missing_page_checks)})

    incomplete_mechanical = [row for row in mechanical_checks
                             if row.get("pass") is not True
                             or row.get("expected") in (None, "")
                             or row.get("actual") in (None, "")]
    if incomplete_mechanical:
        issues.append({"kind": "mechanical_evidence_incomplete", "count": len(incomplete_mechanical)})
    mechanical_complete = bool(mechanical_checks) and not incomplete_mechanical and not missing_page_checks
    final_verified = (
        not any(item["kind"] in {
            "identity_mpn_conflict", "identity_package_conflict", "hint_package_conflict", "footprint_package_conflict",
            "insufficient_identity_mpn_evidence", "insufficient_package_evidence", "insufficient_datasheet_pin_evidence", "symbol_pin_number_mismatch",
            "footprint_pad_number_mismatch", "no_uniquely_resolved_footprint_link",
            "mechanical_datasheet_check_failed", "mechanical_check_missing_page",
        } for item in issues)
        and pin_numbers_match
        and bool(footprint_checks)
        and all(row["pad_numbers_match"] for row in footprint_checks)
        and mechanical_complete
    )
    if final_verified:
        status = "verified"
    elif any(item["kind"] in {"symbol_pin_number_mismatch", "footprint_pad_number_mismatch", "mechanical_datasheet_check_failed", "identity_mpn_conflict", "identity_package_conflict", "hint_package_conflict", "footprint_package_conflict"} for item in issues):
        status = "failed"
    elif any(item["kind"] in {"insufficient_identity_mpn_evidence", "insufficient_package_evidence"} for item in issues):
        status = "insufficient_evidence"
    elif pin_numbers_match and footprint_checks and all(row["pad_numbers_match"] for row in footprint_checks):
        status = "mechanical_review_required"
    else:
        status = "insufficient_evidence"

    verification = {
        "status": status,
        "authority": "datasheet",
        "verified_at": _now(),
        "datasheet_url": enrichment.get("datasheet_url"),
        "datasheet_path": enrichment.get("datasheet_path"),
        "datasheet_pages": pages,
        "datasheet_mpn": evidence.get("mpn"),
        "datasheet_package": evidence.get("package"),
        "pin_numbers_match": pin_numbers_match,
        "pin_name_differences": pin_name_differences,
        "footprint_checks": footprint_checks,
        "mechanical_checks": mechanical_checks,
        "issues": issues,
        "evidence": evidence,
        "note": "SZLCSC/EasyEDA CAD was not used as final truth; final status is derived from supplied Datasheet evidence and local Altium assets",
    }
    symbol["verification"] = verification
    save_ad_catalog(catalog_root, catalog)
    return {"asset_id": asset_id, **verification}
