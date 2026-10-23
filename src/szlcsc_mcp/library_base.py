from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Part

SCHEMA_VERSION = 1
_INDEX_NAME = "library-index.json"
_SAFE_RE = re.compile(r"[^\w.-]+", re.UNICODE)


class LibraryError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def category_slug(value: str | None) -> str:
    raw = str(value or "Uncategorized").strip() or "Uncategorized"
    raw = raw.replace("/", "_").replace("\\", "_")
    slug = _SAFE_RE.sub("_", raw).strip("._-")
    return slug[:96] or "Uncategorized"


def cad_key(part: Part | dict[str, Any]) -> str | None:
    if isinstance(part, Part):
        mpn, package = part.mpn, part.package
    else:
        mpn, package = part.get("mpn"), part.get("package")
    mpn_n = _norm_text(mpn)
    package_n = _norm_text(package)
    if not mpn_n:
        return None
    return f"{mpn_n}|{package_n}"


def _index_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / _INDEX_NAME


def empty_index() -> dict[str, Any]:
    stamp = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": stamp,
        "updated_at": stamp,
        "revision": 0,
        "parts": {},
        "mpn_index": {},
        "cad_index": {},
    }


def load_index(root: str | Path) -> dict[str, Any]:
    path = _index_path(root)
    if not path.is_file():
        return empty_index()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryError(f"invalid library index: {path}: {exc}") from exc
    if not isinstance(data, dict) or int(data.get("schema_version") or 0) != SCHEMA_VERSION:
        raise LibraryError(f"unsupported library index schema in {path}")
    data.setdefault("revision", 0)
    data.setdefault("parts", {})
    data.setdefault("mpn_index", {})
    data.setdefault("cad_index", {})
    return data


def save_index(root: str | Path, index: dict[str, Any]) -> Path:
    root_path = Path(root).expanduser().resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    index["schema_version"] = SCHEMA_VERSION
    index["revision"] = int(index.get("revision") or 0) + 1
    index["updated_at"] = _now()
    path = root_path / _INDEX_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _altium_fingerprints(review: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not review:
        return result
    for key, paths in (("schlib", review.get("schlibs") or []), ("pcblib", review.get("pcblibs") or []), ("step", review.get("step_files") or [])):
        for raw in paths:
            digest = _sha256(Path(raw))
            if digest:
                result[key] = digest
                break
    return result


def part_entry(part: Part, review: dict[str, Any] | None = None, archive: dict[str, Any] | None = None) -> dict[str, Any]:
    if not part.lcsc_code:
        raise LibraryError("part has no LCSC C-code")
    quality = (review or {}).get("quality") or {}
    entry = {
        "lcsc_code": part.lcsc_code.upper(),
        "mpn": part.mpn,
        "brand": part.brand,
        "package": part.package,
        "category": part.category,
        "category_slug": category_slug(part.category),
        "product_url": part.product_url,
        "datasheet_url": part.datasheet_url,
        "small_batch_price_cny": part.small_batch_price_cny,
        "stock": part.stock,
        "total_stock": part.total_stock,
        "in_stock": part.in_stock,
        "min_order_qty": part.min_order_qty,
        "order_multiple": part.order_multiple,
        "packaging": part.packaging,
        "metadata_updated_at": _now(),
        "cad_key": cad_key(part),
        "cad_owner": part.lcsc_code.upper(),
        "cad_shared": False,
        "cad_review_required": False,
        "archive": archive or {},
        "altium": {
            "case_dir": (review or {}).get("case_dir"),
            "has_schlib": bool(quality.get("has_schlib")),
            "has_pcblib": bool(quality.get("has_pcblib")),
            "concerns": list(quality.get("concerns") or []),
            "fingerprints": _altium_fingerprints(review),
            "updated_at": _now() if review else None,
        },
    }
    return entry


def rebuild_indexes(index: dict[str, Any]) -> None:
    mpn_index: dict[str, list[str]] = {}
    cad_index: dict[str, list[str]] = {}
    for code, entry in (index.get("parts") or {}).items():
        mpn = _norm_text(entry.get("mpn"))
        if mpn:
            mpn_index.setdefault(mpn, []).append(code)
        key = entry.get("cad_key") or cad_key(entry)
        if key:
            entry["cad_key"] = key
            cad_index.setdefault(key, []).append(code)
    index["mpn_index"] = {k: sorted(v) for k, v in sorted(mpn_index.items())}
    index["cad_index"] = {k: sorted(v) for k, v in sorted(cad_index.items())}


def _archive_asset_paths(entry: dict[str, Any], kind: str) -> list[Path]:
    archive = entry.get("archive") or {}
    assets = archive.get("assets") or {}
    row = assets.get(kind) or {}
    return [Path(str(value)) for value in (row.get("classified") or []) if str(value).strip()]


def archived_cad_review(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Build an Altium-review-compatible view from canonical classified assets."""
    schlibs = [str(path) for path in _archive_asset_paths(entry, "schlib") if path.is_file()]
    pcblibs = [str(path) for path in _archive_asset_paths(entry, "pcblib") if path.is_file()]
    steps = [str(path) for path in _archive_asset_paths(entry, "step") if path.is_file()]
    if not schlibs and not pcblibs:
        return None
    altium = entry.get("altium") or {}
    return {
        "lcsc_code": entry.get("lcsc_code"),
        "case_dir": altium.get("case_dir"),
        "exists": True,
        "schlibs": schlibs,
        "pcblibs": pcblibs,
        "step_files": steps,
        "reports": {},
        "quality": {
            "has_schlib": bool(schlibs),
            "has_pcblib": bool(pcblibs),
            "concerns": list(altium.get("concerns") or []),
        },
        "verification_required": True,
        "source": "classified_archive",
    }


def _entry_has_cad(entry: dict[str, Any]) -> bool:
    if any(path.is_file() for kind in ("schlib", "pcblib") for path in _archive_asset_paths(entry, kind)):
        return True
    altium = entry.get("altium") or {}
    if not (altium.get("has_schlib") or altium.get("has_pcblib")):
        return False
    raw_case = str(altium.get("case_dir") or "").strip()
    if not raw_case:
        return False
    case_dir = Path(raw_case)
    if not case_dir.is_dir():
        return False
    return bool(list(case_dir.glob("*.SchLib")) or list(case_dir.glob("*.PcbLib")))



def reconcile_cad_group(index: dict[str, Any], key: str) -> str | None:
    codes = list((index.get("cad_index") or {}).get(key) or [])
    if not codes:
        return None
    actual = [code for code in codes if _entry_has_cad((index.get("parts") or {}).get(code) or {})]
    owner = sorted(actual or codes)[0]
    owner_entry = (index.get("parts") or {}).get(owner) or {}
    owner_altium = owner_entry.get("altium") or {}
    for code in codes:
        entry = (index.get("parts") or {}).get(code) or {}
        entry["cad_owner"] = owner
        entry["cad_shared"] = code != owner
        if code != owner and actual:
            entry["altium"] = {
                **(entry.get("altium") or {}),
                "case_dir": owner_altium.get("case_dir"),
                "has_schlib": bool(owner_altium.get("has_schlib")),
                "has_pcblib": bool(owner_altium.get("has_pcblib")),
                "concerns": list(owner_altium.get("concerns") or []),
                "fingerprints": dict(owner_altium.get("fingerprints") or {}),
                "updated_at": owner_altium.get("updated_at"),
            }
    return owner

def choose_cad_owner(index: dict[str, Any], entry: dict[str, Any]) -> str:
    key = entry.get("cad_key")
    if not key:
        return entry["lcsc_code"]
    candidates = list((index.get("cad_index") or {}).get(key) or [])
    for code in candidates:
        existing = (index.get("parts") or {}).get(code) or {}
        if _entry_has_cad(existing):
            return str(existing.get("cad_owner") or code)
    return candidates[0] if candidates else entry["lcsc_code"]


def upsert_part(
    root: str | Path,
    part: Part,
    review: dict[str, Any] | None = None,
    *,
    archive: dict[str, Any] | None = None,
    dedupe_cad: bool = True,
) -> dict[str, Any]:
    index = load_index(root)
    code = str(part.lcsc_code or "").upper()
    if not code:
        raise LibraryError("part has no LCSC C-code")
    old = (index.get("parts") or {}).get(code)
    new = part_entry(part, review, archive)
    if old:
        old_identity = (old.get("mpn"), old.get("package"))
        new_identity = (new.get("mpn"), new.get("package"))
        if old_identity != new_identity:
            new["cad_review_required"] = True
        if archive is None:
            new["archive"] = old.get("archive") or new.get("archive") or {}
        if review is None:
            new["altium"] = old.get("altium") or new["altium"]
            new["cad_owner"] = old.get("cad_owner") or code
            new["cad_shared"] = bool(old.get("cad_shared"))
            new["cad_review_required"] = bool(old.get("cad_review_required")) or bool(new["cad_review_required"])
    index.setdefault("parts", {})[code] = new
    rebuild_indexes(index)
    if dedupe_cad and new.get("cad_key"):
        reconcile_cad_group(index, str(new["cad_key"]))
        new = index["parts"][code]
    rebuild_indexes(index)
    path = save_index(root, index)
    return {"entry": new, "index_path": str(path), "is_update": old is not None}


def summarize_index(index: dict[str, Any]) -> dict[str, Any]:
    parts = index.get("parts") or {}
    mpn_dupes = {k: v for k, v in (index.get("mpn_index") or {}).items() if len(v) > 1}
    cad_dupes = {k: v for k, v in (index.get("cad_index") or {}).items() if len(v) > 1}
    categories: dict[str, int] = {}
    for entry in parts.values():
        cat = entry.get("category_slug") or "Uncategorized"
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "schema_version": index.get("schema_version"),
        "updated_at": index.get("updated_at"),
        "revision": int(index.get("revision") or 0),
        "part_count": len(parts),
        "cad_owner_count": sum(1 for code, e in parts.items() if (e.get("cad_owner") or code) == code),
        "cad_shared_count": sum(1 for e in parts.values() if e.get("cad_shared")),
        "cad_review_required_count": sum(1 for e in parts.values() if e.get("cad_review_required")),
        "archived_part_count": sum(1 for e in parts.values() if (e.get("archive") or {}).get("assets")),
        "categories": dict(sorted(categories.items())),
        "duplicate_mpn_groups": mpn_dupes,
        "duplicate_cad_groups": cad_dupes,
    }


def list_entries(
    index: dict[str, Any],
    *,
    query: str | None = None,
    category: str | None = None,
    only_cad_owners: bool = False,
) -> list[dict[str, Any]]:
    needle = _norm_text(query)
    category_n = _norm_text(category)
    rows: list[dict[str, Any]] = []
    for code, entry in sorted((index.get("parts") or {}).items()):
        if only_cad_owners and (entry.get("cad_owner") or code) != code:
            continue
        if category_n and _norm_text(entry.get("category")) != category_n and _norm_text(entry.get("category_slug")) != category_n:
            continue
        if needle:
            haystack = " ".join(
                str(entry.get(k) or "")
                for k in ("lcsc_code", "mpn", "brand", "package", "category")
            ).casefold()
            if needle not in haystack:
                continue
        rows.append(entry)
    return rows


def stage_for_merge(
    index: dict[str, Any],
    library_root: str | Path,
    *,
    category: str | None = None,
) -> dict[str, Any]:
    root = Path(library_root).expanduser().resolve()
    staging = root / ".merge-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staged: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, str]] = []
    seen_owner: set[str] = set()
    for code, entry in sorted((index.get("parts") or {}).items()):
        owner = str(entry.get("cad_owner") or code)
        if owner in seen_owner:
            continue
        owner_entry = (index.get("parts") or {}).get(owner) or entry
        cat = owner_entry.get("category_slug") or "Uncategorized"
        if category and _norm_text(cat) != _norm_text(category) and _norm_text(owner_entry.get("category")) != _norm_text(category):
            continue
        archived_schlibs = [path for path in _archive_asset_paths(owner_entry, "schlib") if path.is_file()]
        archived_pcblibs = [path for path in _archive_asset_paths(owner_entry, "pcblib") if path.is_file()]
        altium = owner_entry.get("altium") or {}
        case_dir = Path(str(altium.get("case_dir") or ""))
        schlibs = archived_schlibs or (sorted(case_dir.glob("*.SchLib")) if case_dir.is_dir() else [])
        pcblibs = archived_pcblibs or (sorted(case_dir.glob("*.PcbLib")) if case_dir.is_dir() else [])
        if not schlibs and not pcblibs:
            missing.append({"lcsc_code": owner, "reason": "no archived or source SchLib/PcbLib found"})
            continue
        group = staged.setdefault(cat, {"schlib_dir": None, "pcblib_dir": None, "owners": []})
        group["owners"].append(owner)
        if schlibs:
            target_dir = staging / "schlib" / cat
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{owner}.SchLib"
            shutil.copy2(schlibs[0], target)
            group["schlib_dir"] = str(target_dir)
        if pcblibs:
            target_dir = staging / "pcblib" / cat
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{owner}.PcbLib"
            shutil.copy2(pcblibs[0], target)
            group["pcblib_dir"] = str(target_dir)
        seen_owner.add(owner)
    return {"staging_root": str(staging), "groups": staged, "missing": missing}
