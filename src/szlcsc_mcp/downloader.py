from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from .client import SZLCSCClient, SZLCSCError
from .models import Part
from .normalize import normalize_datasheet_url, normalize_image_url
from .http import safe_get
from .storage import archive_roots, asset_basename, mirror_file

_ALLOWED_DOWNLOAD_HOST_SUFFIXES = ("szlcsc.com", "lcsc.com")
# Datasheet URLs returned by SZLCSC sometimes point to the component vendor's
# official document host instead of a SZLCSC/LCSC CDN. Keep this allowlist
# intentionally narrow and PDF-only; product images remain SZLCSC/LCSC-only.
_OFFICIAL_DATASHEET_HOST_SUFFIXES = (
    "ti.com",
    "st.com",
    "analog.com",
    "microchip.com",
    "nxp.com",
    "infineon.com",
    "onsemi.com",
    "renesas.com",
    "nexperia.com",
    "diodes.com",
    "rohm.com",
    "silabs.com",
    "monolithicpower.com",
    "raspberrypi.com",
    "raspberrypi.org",
    "3peak.com",
    "wch.cn",
    "gigadevice.com",
)
_DEFAULT_MAX_BYTES = 80 * 1024 * 1024


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _extra_datasheet_hosts() -> tuple[str, ...]:
    raw = os.getenv("SZLCSC_EXTRA_DATASHEET_HOSTS", "")
    return tuple(
        item.strip().lower().lstrip(".")
        for item in raw.split(",")
        if item.strip()
    )


def _validate_download_url(url: str, *, pdf: bool) -> None:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise SZLCSCError(f"Refusing invalid download URL: {url!r}")
    if _host_matches(host, _ALLOWED_DOWNLOAD_HOST_SUFFIXES):
        return
    if pdf and scheme == "https" and _host_matches(
        host, _OFFICIAL_DATASHEET_HOST_SUFFIXES + _extra_datasheet_hosts()
    ):
        return
    kind = "datasheet" if pdf else "asset"
    raise SZLCSCError(f"Refusing {kind} download from unapproved host: {host}")


def _slug(value: str | None, fallback: str) -> str:
    text = (value or fallback).strip()
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:120] or fallback


def _download(
    session: requests.Session,
    url: str,
    path: Path,
    timeout: float,
    *,
    pdf: bool = False,
    force: bool = True,
    max_bytes: int | None = None,
) -> None:
    if pdf:
        url = normalize_datasheet_url(url) or url
    _validate_download_url(url, pdf=pdf)
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(os.getenv("SZLCSC_MAX_DOWNLOAD_BYTES", max_bytes or _DEFAULT_MAX_BYTES))
    tmp = path.with_suffix(path.suffix + ".part")
    total = 0
    try:
        with safe_get(session, url, validate=lambda u: _validate_download_url(u, pdf=pdf),
                      timeout=timeout, stream=True) as response:
            response.raise_for_status()
            # requests follows redirects by default. Validate the final URL too so
            # an approved source cannot bounce the downloader to an arbitrary host.
            final_url = getattr(response, "url", None) or url
            _validate_download_url(str(final_url), pdf=pdf)
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise SZLCSCError(f"Download exceeds size limit ({declared} bytes)")
            with tmp.open("wb") as file:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise SZLCSCError(f"Download exceeds size limit ({max_bytes} bytes)")
                    file.write(chunk)
        if pdf:
            with tmp.open("rb") as file:
                if file.read(5) != b"%PDF-":
                    raise SZLCSCError("Downloaded datasheet is not a PDF")
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _readme(part: Part) -> str:
    def value(x):
        return "未知" if x in (None, "") else str(x)

    params = "\n".join(f"- {k}: {val}" for k, val in sorted(part.parameters.items())) or "- 暂无"
    tiers = (
        "\n".join(
            f"- {value(t.min_qty)}+"
            + (f" ~ {t.max_qty}" if t.max_qty is not None else "")
            + f": ¥{value(t.unit_price_cny)}"
            for t in part.price_tiers
        )
        or "- 暂无/未获取"
    )
    warehouses = "、".join(f"{k}:{v}" for k, v in part.warehouse_stock.items()) or "未知"
    return f"""# {value(part.mpn)} ({value(part.lcsc_code)})

- 品牌：{value(part.brand)}
- 封装：{value(part.package)}
- 分类：{value(part.category)}
- 国内库存：{value(part.stock if part.stock is not None else part.total_stock)}
- 分仓库存：{warehouses}
- 最小起订量：{value(part.min_order_qty)}
- 订购倍数：{value(part.order_multiple)}
- 包装：{value(part.packaging)}
- 小批量参考单价：{('¥' + str(part.small_batch_price_cny)) if part.small_batch_price_cny is not None else '未知'}
- 商品页：{value(part.product_url)}
- Datasheet：{value(part.datasheet_url)}

## 阶梯价
{tiers}

## 参数
{params}

> 数据来自国内立创商城 SZLCSC。库存和价格是易变数据；缺失值表示未获取到，不代表 0。
"""


def download_part(
    client: SZLCSCClient,
    identifier: str,
    output_root: str | Path = "components",
    *,
    force: bool = True,
) -> dict:
    part = client.get_part(identifier)
    category = _slug(part.category, "Uncategorized")
    name = asset_basename(part)
    out = Path(output_root).expanduser().resolve() / category / name
    out.mkdir(parents=True, exist_ok=True)

    (out / f"{name}.json").write_text(
        json.dumps(part.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / f"{name}.md").write_text(_readme(part), encoding="utf-8")

    downloaded: list[str] = [f"{name}.json", f"{name}.md"]
    errors: list[str] = []
    if part.datasheet_url:
        try:
            _download(
                client.session,
                part.datasheet_url,
                out / f"{name}.pdf",
                client.timeout,
                pdf=True,
                force=force,
            )
            downloaded.append(f"{name}.pdf")
        except Exception as exc:
            errors.append(f"datasheet: {exc}")
    image_url = normalize_image_url(part.image_url)
    if image_url:
        suffix = Path(urlparse(image_url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        try:
            _download(
                client.session,
                image_url,
                out / f"{name}-product{suffix}",
                client.timeout,
                force=force,
            )
            downloaded.append(f"{name}-product{suffix}")
        except Exception as exc:
            errors.append(f"image: {exc}")
    return {"ok": not errors, "complete": not errors, "directory": str(out),
            "downloaded": downloaded, "errors": errors, "part": part.to_dict()}


def download_parts(
    client: SZLCSCClient,
    identifiers: list[str],
    output_root: str | Path = "components",
    *,
    force: bool = True,
) -> dict:
    results = []
    seen: set[str] = set()
    for identifier in identifiers:
        key = identifier.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            results.append({"identifier": identifier, **download_part(client, identifier, output_root, force=force)})
        except Exception as exc:
            results.append({"identifier": identifier, "ok": False, "error": str(exc)})
    return {
        "requested": len(identifiers),
        "processed": len(results),
        "successful": sum(1 for x in results if x.get("ok")),
        "results": results,
    }


def _write_manifest_atomic(path: Path, manifest: dict) -> None:
    """Write manifest atomically so an interrupted batch cannot leave half JSON."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _refresh_manifest_views(manifest: dict, entries: dict) -> None:
    """Maintain both keyed `parts` and agent-friendly `items`/summary views."""
    items = sorted(
        [value for value in entries.values() if isinstance(value, dict)],
        key=lambda row: str(row.get("lcsc_code") or row.get("mpn") or row.get("requested_identifier") or "").casefold(),
    )
    manifest["schema_version"] = 2
    manifest["items"] = items
    manifest["total_items"] = len(items)
    manifest["status_counts"] = {
        status: sum(1 for row in items if row.get("status") == status)
        for status in ("downloaded", "cached", "no_datasheet", "error")
    }
    manifest["updated_unix"] = int(time.time())



def archive_part_assets(
    client: SZLCSCClient,
    part: Part,
    library_root: str | Path,
    *,
    review: dict | None = None,
    force: bool = False,
) -> dict:
    """Persist Datasheet + generated Altium assets into classified and flat library views."""
    from .storage import archive_existing_files

    review = review or {}
    report_values = [value for value in (review.get("reports") or {}).values() if value]
    archived = archive_existing_files(
        part,
        library_root,
        schlibs=review.get("schlibs") or [],
        pcblibs=review.get("pcblibs") or [],
        step_files=review.get("step_files") or [],
        report_files=report_values,
        overwrite=True,
    )
    roots = archive_roots(library_root, part)
    datasheet = {"classified": [], "flat": []}
    errors: list[str] = []
    if part.datasheet_url:
        classified_target = roots["datasheet_dir"] / f"{roots['basename']}.pdf"
        flat_target = roots["flat_root"] / f"{roots['basename']}.pdf"
        try:
            _download(
                client.session,
                part.datasheet_url,
                classified_target,
                client.timeout,
                pdf=True,
                force=force,
            )
            mirror_file(classified_target, flat_target, overwrite=True)
            datasheet = {
                "classified": [str(classified_target)],
                "flat": [str(flat_target)],
            }
        except Exception as exc:
            errors.append(f"datasheet: {exc}")
    archived.setdefault("assets", {})["datasheet"] = datasheet
    archived["errors"] = errors
    return archived


def sync_datasheets(
    client: SZLCSCClient,
    identifiers: list[str],
    output_dir: str | Path = "datasheets",
    *,
    force: bool = False,
) -> dict:
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except (OSError, ValueError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    entries = manifest.setdefault("parts", {})
    if not isinstance(entries, dict):
        entries = {}
        manifest["parts"] = entries
    # Migrate a possible items-only manifest produced by another client/tool.
    if not entries and isinstance(manifest.get("items"), list):
        for row in manifest["items"]:
            if not isinstance(row, dict):
                continue
            key = row.get("lcsc_code") or row.get("mpn") or row.get("requested_identifier")
            if key:
                entries[str(key)] = row
    results: list[dict] = []
    seen: set[str] = set()

    normalized_identifiers = [str(x).strip() for x in identifiers if str(x).strip()]
    if not normalized_identifiers:
        _refresh_manifest_views(manifest, entries)
        _write_manifest_atomic(manifest_path, manifest)
        return {
            "ok": False,
            "error": "No non-empty identifiers supplied",
            "directory": str(out),
            "manifest": str(manifest_path),
            "processed": 0,
            "manifest_items": len(entries),
            "results": [],
        }

    for identifier in normalized_identifiers:
        request_key = identifier.strip()
        if not request_key or request_key.casefold() in seen:
            continue
        seen.add(request_key.casefold())
        stage = "resolve_part"
        record = {
            "requested_identifier": request_key,
            "updated_unix": int(time.time()),
            "status": "error",
        }
        try:
            part = client.get_part(request_key)
            identity = part.lcsc_code or part.mpn or request_key
            filename = asset_basename(part) + ".pdf"
            target = out / filename
            resolved_datasheet_url = normalize_datasheet_url(part.datasheet_url) if part.datasheet_url else None
            datasheet_host = (urlparse(resolved_datasheet_url).hostname or "").lower() if resolved_datasheet_url else None
            record.update(
                {
                    "lcsc_code": part.lcsc_code,
                    "mpn": part.mpn,
                    "brand": part.brand,
                    "datasheet_url": part.datasheet_url,
                    "datasheet_url_resolved": resolved_datasheet_url,
                    "datasheet_host": datasheet_host,
                    "file": filename,
                }
            )
            if not part.datasheet_url:
                record["status"] = "no_datasheet"
                record["result_stage"] = "resolve_datasheet"
            else:
                stage = "download_datasheet"
                existed = target.exists()
                _download(
                    client.session,
                    resolved_datasheet_url or part.datasheet_url,
                    target,
                    client.timeout,
                    pdf=True,
                    force=force,
                )
                record["status"] = "cached" if existed and not force else "downloaded"
                record["result_stage"] = "complete"
            entries[identity] = record
            results.append(record)
        except Exception as exc:
            record["status"] = "error"
            record["error_stage"] = stage
            record["error_type"] = type(exc).__name__
            record["error_message"] = str(exc)
            # Backward-compatible short field used by older agents.
            record["error"] = str(exc)
            entries[record.get("lcsc_code") or record.get("mpn") or request_key] = record
            results.append(record)
        # Incremental + atomic manifest write: safe to interrupt a long batch and
        # easy for simple agents to count via total_items/items.
        _refresh_manifest_views(manifest, entries)
        _write_manifest_atomic(manifest_path, manifest)

    downloaded = sum(1 for x in results if x.get("status") == "downloaded")
    cached = sum(1 for x in results if x.get("status") == "cached")
    no_datasheet = sum(1 for x in results if x.get("status") == "no_datasheet")
    errors = sum(1 for x in results if x.get("status") == "error")
    _refresh_manifest_views(manifest, entries)
    _write_manifest_atomic(manifest_path, manifest)
    failed_results = [
        {
            key: row.get(key)
            for key in (
                "requested_identifier",
                "lcsc_code",
                "mpn",
                "brand",
                "datasheet_url",
                "datasheet_url_resolved",
                "datasheet_host",
                "error_stage",
                "error_type",
                "error_message",
            )
            if row.get(key) not in (None, "")
        }
        for row in results
        if row.get("status") == "error"
    ]
    failure_summary: dict[str, int] = {}
    for row in failed_results:
        key = str(row.get("error_stage") or "unknown")
        failure_summary[key] = failure_summary.get(key, 0) + 1

    return {
        "ok": len(results) > 0 and errors == 0,
        "complete": len(results) > 0 and errors == 0 and no_datasheet == 0,
        "directory": str(out),
        "manifest": str(manifest_path),
        "processed": len(results),
        "manifest_items": manifest["total_items"],
        "downloaded": downloaded,
        "cached": cached,
        "no_datasheet": no_datasheet,
        "errors": errors,
        "failed_results": failed_results,
        "failure_summary": failure_summary,
        "results": results,
    }
