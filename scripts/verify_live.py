"""Opt-in, bounded read-only upstream probes. No auth/challenge bypass or saved cookies."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient
from szlcsc_mcp.eda import EasyEDAAssetClient, extract_3d_models
from szlcsc_mcp.jlcpcb import JLCPCBClient


def run():
    result = {"executed_at_utc": datetime.now(timezone.utc).isoformat(), "probes": {}}

    def probe(name, fn):
        started = time.monotonic()
        try:
            value = fn()
            result["probes"][name] = {
                "status": "PASS",
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            return value
        except Exception as exc:
            result["probes"][name] = {
                "status": "BLOCKED"
                if getattr(exc, "code", "") in {"ACCESS_DENIED", "RATE_LIMITED"}
                else "FAIL",
                "code": getattr(exc, "code", type(exc).__name__),
                "message": str(exc),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            return None

    with tempfile.TemporaryDirectory(prefix="szlcsc-live-") as directory:
        cache = SqliteCache(Path(directory) / "cache.sqlite3")
        client = SZLCSCClient(cache=cache, timeout=8, request_delay=0.5, retries=0)
        eda = EasyEDAAssetClient(cache=cache, timeout=8, request_delay=0.5, retries=0)
        jlc = JLCPCBClient(cache, timeout=8, delay=0.5, retries=0)
        try:
            parts = probe(
                "domestic_search",
                lambda: client.search("C2040", max_results=1, enrich=False),
            )
            if parts:
                part = parts[0]
                if part.category_id:
                    probe(
                        "domestic_category",
                        lambda: client._category_exact(part.category_id, "C2040"),
                    )
                if part.product_sign_id:
                    probe(
                        "domestic_detail",
                        lambda: client._get_json(
                            f"{client.ITEM_HOST}/phone/p/{part.product_sign_id}"
                        ),
                    )
                if part.product_url:
                    probe("domestic_html", lambda: client._enrich_from_html(part))
            probe(
                "domestic_alternatives",
                lambda: client.alternatives(
                    "C2040", max_results=1, in_stock_only=False
                ),
            )
            cad = probe("easyeda_components", lambda: eda.get_cad_data("C2040"))
            probe("easyeda_svgs", lambda: eda.get_svgs("C2040"))
            refs = extract_3d_models(cad) if cad else []
            if refs:
                probe(
                    "easyeda_obj",
                    lambda: eda._download_binary(
                        eda.OBJ_ENDPOINT.format(uuid=refs[0].uuid)
                    ),
                )
                probe(
                    "easyeda_step",
                    lambda: eda._download_binary(
                        eda.STEP_ENDPOINT.format(uuid=refs[0].uuid)
                    ),
                )
            probe(
                "jlcpcb_basic_search",
                lambda: jlc.search("100nF", library_type="basic", page_size=1),
            )
            probe("jlcpcb_categories", lambda: jlc.categories())
            for name in (
                "domestic_category",
                "domestic_detail",
                "domestic_html",
                "easyeda_obj",
                "easyeda_step",
            ):
                result["probes"].setdefault(
                    name,
                    {
                        "status": "NOT_RUN",
                        "reason": "Prerequisite part/CAD metadata was unavailable",
                    },
                )
            result["probes"]["domestic_annex"] = {
                "status": "NOT_RUN",
                "reason": "No stable annex identifier fixture for this live sample; parser path covered by mocks",
            }
            result["probes"]["authenticated_official_jlcpcb"] = {
                "status": "NOT_RUN",
                "reason": "No approved official API credentials supplied; no authenticated adapter claimed",
            }
            result["probes"]["altium_gui"] = {
                "status": "NOT_RUN",
                "reason": "This change does not replace existing Antigravity CAD acceptance",
            }
        finally:
            client.close()
            eda.close()
            jlc.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
