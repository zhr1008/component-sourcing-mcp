from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import posixpath
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .export_utils import csv_cell
from .library_base import LibraryError

CATALOG_SCHEMA_VERSION = 1
CATALOG_FILENAME = "altium-catalog.json"
VERIFIED_EVIDENCE_STATUSES = frozenset({"verified", "pins_verified", "footprint_numbering_verified"})

_LCSC_RE = re.compile(r"(?<![A-Za-z0-9])C\d{3,}(?![A-Za-z0-9])", re.IGNORECASE)
_CCODE_KEYS = {
    "lcsc", "lcsc part #", "lcsc part", "lcsc code", "jlcpcb part #",
    "jlcpcb part", "jlcpcb part number", "立创编号", "立创商城编号", "c编号", "商品编号",
}
_MPN_KEYS = {
    "mpn", "manufacturer part number", "manufacturer part #", "manufacturer pn",
    "mfr part number", "part number", "订货型号", "型号",
}
_BRAND_KEYS = {"manufacturer", "brand", "mfr", "制造商", "品牌"}
_PACKAGE_KEYS = {"package", "封装", "case/package", "pcb package"}
_FOOTPRINT_PARAM_KEYS = {
    "footprint", "pcb footprint", "footprint ref", "footprint name", "pattern",
    "pcb library", "pcblib", "封装名称",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / CATALOG_FILENAME


def empty_catalog() -> dict[str, Any]:
    stamp = _now()
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "created_at": stamp,
        "updated_at": stamp,
        "revision": 0,
        "scan_roots": [],
        "files": {},
        "symbols": {},
        "footprints": {},
        "footprint_name_index": {},
    }


def load_ad_catalog(root: str | Path) -> dict[str, Any]:
    path = _catalog_path(root)
    if not path.is_file():
        return empty_catalog()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryError(f"invalid Altium catalog: {path}: {exc}") from exc
    if int(data.get("schema_version") or 0) != CATALOG_SCHEMA_VERSION:
        raise LibraryError(f"unsupported Altium catalog schema: {path}")
    for key, default in (
        ("files", {}), ("symbols", {}), ("footprints", {}),
        ("footprint_name_index", {}), ("scan_roots", []),
    ):
        data.setdefault(key, default)
    data.setdefault("revision", 0)
    return data


def save_ad_catalog(root: str | Path, catalog: dict[str, Any]) -> Path:
    root_path = Path(root).expanduser().resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    catalog["schema_version"] = CATALOG_SCHEMA_VERSION
    catalog["revision"] = int(catalog.get("revision") or 0) + 1
    catalog["updated_at"] = _now()
    path = root_path / CATALOG_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(path: Path, *, hash_file: bool) -> dict[str, Any]:
    stat = path.stat()
    result: dict[str, Any] = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if hash_file:
        result["sha256"] = _sha256(path)
    return result


def _fingerprint_matches(old: dict[str, Any], current: dict[str, Any]) -> bool:
    for key in ("size", "mtime_ns"):
        if old.get(key) != current.get(key):
            return False
    if "sha256" in current and old.get("sha256") != current.get("sha256"):
        return False
    return True


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _iter(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return []


def _parameter_dict(owner: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    params = _attr(owner, "parameters", default=[])
    if isinstance(params, dict):
        return {str(k): str(v) for k, v in params.items() if str(k).strip()}
    for param in _iter(params):
        name = _safe_text(_attr(param, "name", "parameter_name", "Name"))
        if not name:
            continue
        text = _safe_text(_attr(param, "text", "value", "Value", default="")) or ""
        result[name] = text
    return result


def _pin_rows(symbol: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pin in _iter(_attr(symbol, "pins", default=[])):
        designator = _safe_text(_attr(pin, "designator", "number", "pin_number", "Designator"))
        name = _safe_text(_attr(pin, "name", "pin_name", "Name"))
        electrical = _attr(pin, "electrical_type", "electrical", "Electrical")
        if hasattr(electrical, "name"):
            electrical = electrical.name
        row: dict[str, Any] = {
            "number": designator,
            "name": name,
            "electrical_type": _safe_text(electrical),
        }
        for key, attrs in {
            "x_mils": ("x_mils", "x"),
            "y_mils": ("y_mils", "y"),
            "length_mils": ("length_mils", "length"),
        }.items():
            value = _attr(pin, *attrs)
            if isinstance(value, (int, float)):
                row[key] = float(value)
        rows.append(row)
    return rows


def _object_model_links(symbol: Any) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for attr_name in ("implementations", "models", "footprints"):
        candidates.extend(_iter(_attr(symbol, attr_name)))
    # Some altium-monkey versions expose implementation records through the
    # symbol object collection rather than a dedicated typed view.
    candidates.extend(_iter(_attr(symbol, "objects", default=[])))

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        model_type = _safe_text(_attr(item, "model_type", "ModelType"))
        model_name = _safe_text(_attr(item, "model_name", "ModelName", "name"))
        library_name = _safe_text(_attr(
            item, "library_name", "datafile", "model_datafile", "ModelDatafile0"
        ))
        cls_name = item.__class__.__name__.casefold()
        if not model_name:
            continue
        if model_type and "pcb" not in model_type.casefold():
            continue
        if not model_type and "implementation" not in cls_name and "footprint" not in cls_name:
            continue
        key = (_norm(model_name), _norm(library_name))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "name": model_name,
            "library": library_name,
            "source": "altium_implementation",
            "is_current": bool(_attr(item, "is_current", "IsCurrent", default=False)),
        })
    return rows


def _footprint_links(symbol: Any, parameters: dict[str, str]) -> list[dict[str, Any]]:
    rows = _object_model_links(symbol)
    seen = {(_norm(r.get("name")), _norm(r.get("library"))) for r in rows}
    for name, value in parameters.items():
        if _norm(name) not in _FOOTPRINT_PARAM_KEYS or not str(value).strip():
            continue
        for token in re.split(r"[;,|]", str(value)):
            fp = token.strip()
            if not fp:
                continue
            key = (_norm(fp), "")
            if key in seen:
                continue
            seen.add(key)
            rows.append({"name": fp, "library": None, "source": f"parameter:{name}", "is_current": False})
    return rows


def _identity_hints(symbol_name: str, parameters: dict[str, str]) -> dict[str, Any]:
    hints: dict[str, Any] = {
        "lcsc_code": None,
        "mpn": None,
        "brand": None,
        "package": None,
        "symbol_name": symbol_name,
        "sources": {},
    }
    for name, value in parameters.items():
        key = _norm(name)
        text = str(value or "").strip()
        if not text:
            continue
        if key in _CCODE_KEYS and not hints["lcsc_code"]:
            match = _LCSC_RE.search(text)
            if match:
                hints["lcsc_code"] = match.group(0).upper()
                hints["sources"]["lcsc_code"] = f"parameter:{name}"
        if key in _MPN_KEYS and not hints["mpn"]:
            hints["mpn"] = text
            hints["sources"]["mpn"] = f"parameter:{name}"
        if key in _BRAND_KEYS and not hints["brand"]:
            hints["brand"] = text
            hints["sources"]["brand"] = f"parameter:{name}"
        if key in _PACKAGE_KEYS and not hints["package"]:
            hints["package"] = text
            hints["sources"]["package"] = f"parameter:{name}"
    if not hints["lcsc_code"]:
        match = _LCSC_RE.search(symbol_name)
        if match:
            hints["lcsc_code"] = match.group(0).upper()
            hints["sources"]["lcsc_code"] = "symbol_name"
    return hints


def _pad_row(pad: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "number": _safe_text(_attr(pad, "designator", "name", "pad_number", "Designator")),
    }
    mapping = {
        "x_mils": (("x_mils",), ("x",)),
        "y_mils": (("y_mils",), ("y",)),
        "width_mils": (("width_mils", "size_x_mils"), ("width", "top_width")),
        "height_mils": (("height_mils", "size_y_mils"), ("height", "top_height")),
        "hole_size_mils": (("hole_size_mils", "drill_size_mils"), ("hole_size",)),
    }
    for key, (mil_attrs, raw_attrs) in mapping.items():
        value = _attr(pad, *mil_attrs)
        if value is None:
            # Altium PCB dimensions are 1/10000 mil internally. In particular,
            # altium-monkey exposes width_mils but currently only raw height.
            raw = _attr(pad, *raw_attrs)
            value = raw / 10000 if isinstance(raw, (int, float)) else None
        if isinstance(value, (int, float)) and math.isfinite(value):
            row[key] = float(value)
    layer = _attr(pad, "layer", "Layer")
    if hasattr(layer, "name"):
        layer = layer.name
    if layer is not None:
        row["layer"] = str(layer)
    return row


def _footprint_geometry(pads: list[dict[str, Any]]) -> dict[str, Any]:
    points = [
        (p.get("x_mils"), p.get("y_mils"))
        for p in pads
        if isinstance(p.get("x_mils"), (int, float)) and isinstance(p.get("y_mils"), (int, float))
    ]
    result: dict[str, Any] = {"pad_count": len(pads)}
    if not points:
        return result
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]
    result.update({
        "center_span_x_mm": round((max(xs) - min(xs)) * 0.0254, 6),
        "center_span_y_mm": round((max(ys) - min(ys)) * 0.0254, 6),
    })
    distances: list[float] = []
    for i, (x1, y1) in enumerate(points):
        for x2, y2 in points[i + 1:]:
            dx = float(x2) - float(x1)
            dy = float(y2) - float(y1)
            d = (dx * dx + dy * dy) ** 0.5
            if d > 1e-9:
                distances.append(d)
    if distances:
        result["minimum_pad_center_distance_mm"] = round(min(distances) * 0.0254, 6)
    return result


def _parse_schlib(path: Path) -> list[dict[str, Any]]:
    try:
        from altium_monkey import AltiumSchLib
    except ImportError as exc:
        raise LibraryError(
            "Altium library scanning requires altium-monkey in the MCP Python environment; "
            "install component-sourcing-mcp[altium] after reviewing its dependency security notes"
        ) from exc
    library = AltiumSchLib(path)
    rows: list[dict[str, Any]] = []
    for symbol in _iter(_attr(library, "symbols", default=[])):
        name = _safe_text(_attr(symbol, "name", "Name")) or "unnamed"
        parameters = _parameter_dict(symbol)
        description = _safe_text(_attr(symbol, "description", "Description"))
        datafile_hints = []
        for attr_name in ("implementations", "models", "footprints", "objects"):
            for item in _iter(_attr(symbol, attr_name)):
                model_name = _safe_text(_attr(item, "model_name", "ModelName", "name"))
                model_type = _safe_text(_attr(item, "model_type", "ModelType"))
                cls_name = item.__class__.__name__.casefold()
                if not model_name or (model_type and "pcb" not in model_type.casefold()):
                    continue
                if not model_type and "implementation" not in cls_name and "footprint" not in cls_name:
                    continue
                datafile = _safe_text(_attr(item, "datafile_entity", "model_datafile_entity", "library_name", "datafile", "model_datafile", "ModelDatafileEntity0", "ModelDatafile0"))
                if datafile:
                    datafile_hints.append((model_name, datafile, _safe_text(_attr(item, "datafile_kind", "ModelDatafileKind0"))))
        rows.append({
            "name": name,
            "description": description,
            "parameters": parameters,
            "pins": _pin_rows(symbol),
            "footprint_links": _footprint_links(symbol, parameters),
            "identity_hints": _identity_hints(name, parameters),
            "_datafile_hints": datafile_hints,
        })
    return rows


def _parse_pcblib(path: Path) -> list[dict[str, Any]]:
    try:
        from altium_monkey import AltiumPcbLib
    except ImportError as exc:
        raise LibraryError(
            "Altium library scanning requires altium-monkey in the MCP Python environment; "
            "install component-sourcing-mcp[altium] after reviewing its dependency security notes"
        ) from exc
    factory = getattr(AltiumPcbLib, "from_file", None)
    library = factory(path) if callable(factory) else AltiumPcbLib(path)
    rows: list[dict[str, Any]] = []
    for footprint in _iter(_attr(library, "footprints", default=[])):
        name = _safe_text(_attr(footprint, "name", "Name")) or "unnamed"
        parameters = _parameter_dict(footprint)
        pads = [_pad_row(pad) for pad in _iter(_attr(footprint, "pads", default=[]))]
        rows.append({
            "name": name,
            "description": _safe_text(_attr(footprint, "description", "Description")) or parameters.get("DESCRIPTION"),
            "parameters": parameters,
            "pads": pads,
            "geometry": _footprint_geometry(pads),
            "component_body_count": len(_iter(_attr(footprint, "component_bodies", default=[]))),
        })
    return rows


def _library_files(paths: Iterable[str | Path], *, recursive: bool) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        candidates: Iterable[Path]
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = path.rglob("*") if recursive else path.iterdir()
        else:
            continue
        for item in candidates:
            if not item.is_file() or item.suffix.casefold() not in {".schlib", ".pcblib"}:
                continue
            key = str(item.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(item.resolve())
    return sorted(result, key=lambda p: str(p).casefold())


def _asset_id(kind: str, library_path: str, name: str) -> str:
    raw = f"{kind}\0{library_path}\0{name}".encode("utf-8", errors="surrogatepass")
    return f"{kind}:{hashlib.sha1(raw).hexdigest()}"


def _library_hint_matches(hint: str, library_path: str) -> bool:
    def normalize(value: str) -> str:
        value = posixpath.normpath(value.strip().replace("\\", "/")).casefold()
        return value.removesuffix(".pcblib")

    wanted = normalize(hint)
    actual = normalize(library_path)
    if wanted in {"", "."} or actual in {"", "."}:
        return False
    if wanted.startswith("/") or re.match(r"^[a-z]:", wanted):
        return actual == wanted
    return actual == wanted or actual.endswith("/" + wanted)


def _mark_cad_verification_stale(symbol: dict[str, Any]) -> None:
    verification = symbol.get("verification") or {}
    if verification.get("status") in VERIFIED_EVIDENCE_STATUSES:
        symbol["verification"] = {
            **verification,
            "status": "stale_cad_changed",
            "stale_at": _now(),
            "reason": "symbol or linked footprint CAD evidence changed or is stale",
        }


def _cad_evidence(symbol: dict[str, Any], footprints: dict[str, Any]) -> tuple[Any, ...]:
    links = symbol.get("resolved_footprint_links")
    linked = {}
    for link in links or []:
        for asset_id in link.get("candidates") or []:
            footprint = footprints.get(asset_id)
            linked[asset_id] = None if footprint is None else {
                key: footprint.get(key)
                for key in ("name", "library_path", "pads", "geometry", "component_body_count", "catalog_stale")
            }
    return (symbol.get("pins"), symbol.get("footprint_links"), symbol.get("identity_hints"), links, linked)


def _resolve_footprint_links(catalog: dict[str, Any]) -> None:
    index: dict[str, list[str]] = {}
    for asset_id, row in catalog.get("footprints", {}).items():
        index.setdefault(_norm(row.get("name")), []).append(asset_id)
    catalog["footprint_name_index"] = {k: sorted(v) for k, v in sorted(index.items())}

    for symbol in catalog.get("symbols", {}).values():
        resolved: list[dict[str, Any]] = []
        issues = [item for item in symbol.get("issues", []) if item.get("kind") != "footprint_link"]
        for link in symbol.get("footprint_links", []):
            if not link.get("library"):
                hint = next((hint for hint in symbol.get("_datafile_hints", [])
                             if _norm(hint[0]) == _norm(link.get("name"))), None)
                if hint:
                    link = {**link, "library": hint[1], "library_kind": hint[2], "source": "altium_implementation_datafile"}
            candidates = list(index.get(_norm(link.get("name"))) or [])
            library_hint = str(link.get("library") or "").strip()
            if library_hint:
                candidates = [
                    asset_id for asset_id in candidates
                    if _library_hint_matches(library_hint, str(catalog["footprints"][asset_id].get("library_path") or ""))
                ]
            status = "resolved" if len(candidates) == 1 else ("missing" if not candidates else "ambiguous")
            resolved.append({**link, "status": status, "candidates": candidates})
            if status != "resolved":
                issues.append({
                    "kind": "footprint_link",
                    "status": status,
                    "name": link.get("name"),
                    "candidate_count": len(candidates),
                })
        previous_links = symbol.get("resolved_footprint_links")
        if (previous_links is not None and previous_links != resolved) or symbol.get("catalog_stale") or any(
            (catalog["footprints"].get(asset_id) or {}).get("catalog_stale")
            for link in resolved for asset_id in link.get("candidates", [])
        ):
            _mark_cad_verification_stale(symbol)
        symbol["resolved_footprint_links"] = resolved
        symbol["issues"] = issues


def scan_altium_libraries(
    paths: Iterable[str | Path],
    *,
    catalog_root: str | Path = "altium-catalog",
    recursive: bool = True,
    hash_files: bool = False,
    reuse_unchanged: bool = True,
) -> dict[str, Any]:
    root = Path(catalog_root).expanduser().resolve()
    old = load_ad_catalog(root)
    files = _library_files(paths, recursive=recursive)
    old_files = old.get("files") or {}
    old_symbols = old.get("symbols") or {}
    old_footprints = old.get("footprints") or {}

    catalog = empty_catalog()
    catalog["created_at"] = old.get("created_at") or catalog["created_at"]
    catalog["revision"] = int(old.get("revision") or 0)
    catalog["scan_roots"] = [str(Path(p).expanduser().resolve()) for p in paths]
    parse_errors: list[dict[str, Any]] = []
    reused_files = 0
    parsed_files = 0

    for path in files:
        path_str = str(path)
        kind = "SchLib" if path.suffix.casefold() == ".schlib" else "PcbLib"
        fingerprint = _fingerprint(path, hash_file=hash_files)
        old_file = old_files.get(path_str) or {}
        unchanged = (
            reuse_unchanged and old_file.get("kind") == kind
            and old_file.get("scan_status") == "ok"
            and _fingerprint_matches(old_file.get("fingerprint") or {}, fingerprint)
        )
        file_row = {"path": path_str, "kind": kind, "fingerprint": fingerprint, "scan_status": "ok"}

        if unchanged:
            reused_files += 1
            for asset_id in old_file.get("asset_ids") or []:
                source = old_symbols.get(asset_id) if kind == "SchLib" else old_footprints.get(asset_id)
                if source:
                    target = catalog["symbols"] if kind == "SchLib" else catalog["footprints"]
                    target[asset_id] = copy.deepcopy(source)
            file_row["asset_ids"] = list(old_file.get("asset_ids") or [])
            file_row["reused"] = True
            catalog["files"][path_str] = file_row
            continue

        try:
            parsed = _parse_schlib(path) if kind == "SchLib" else _parse_pcblib(path)
            parsed_files += 1
            asset_ids: list[str] = []
            for item in parsed:
                item = copy.deepcopy(item)
                datafile_hints = item.pop("_datafile_hints", [])
                asset_id = _asset_id("symbol" if kind == "SchLib" else "footprint", path_str, item["name"])
                asset_ids.append(asset_id)
                previous = old_symbols.get(asset_id) if kind == "SchLib" else old_footprints.get(asset_id)
                row = {
                    "asset_id": asset_id,
                    "library_path": path_str,
                    "library_name": path.name,
                    "name": item["name"],
                    **item,
                    "scanned_at": _now(),
                }
                if datafile_hints:
                    row["_datafile_hints"] = datafile_hints
                if previous:
                    # The resilient scanner adds DATAFILE hints after parsing. Reapply
                    # them only when this parse confirms them, before comparing evidence.
                    for link in row.get("footprint_links") or []:
                        if link.get("library"):
                            continue
                        prior = next((old_link for old_link in previous.get("footprint_links", [])
                                      if old_link.get("source") == "altium_implementation_datafile"
                                      and _norm(old_link.get("name")) == _norm(link.get("name"))), None)
                        hint = next((hint for hint in datafile_hints if _norm(hint[0]) == _norm(link.get("name"))), None)
                        if prior and hint:
                            link.update({"library": hint[1], "library_kind": hint[2], "source": "altium_implementation_datafile"})
                    for carry in ("enrichment", "verification", "property_patch"):
                        if carry in previous:
                            row[carry] = previous[carry]
                target = catalog["symbols"] if kind == "SchLib" else catalog["footprints"]
                target[asset_id] = row
            file_row["asset_ids"] = asset_ids
            file_row["reused"] = False
        except Exception as exc:
            file_row["scan_status"] = "error"
            file_row["error"] = f"{type(exc).__name__}: {exc}"
            file_row["asset_ids"] = []
            parse_errors.append({"path": path_str, "kind": kind, "error": file_row["error"]})
        catalog["files"][path_str] = file_row

    _resolve_footprint_links(catalog)
    for asset_id, symbol in catalog["symbols"].items():
        previous = old_symbols.get(asset_id)
        if previous and _cad_evidence(previous, old_footprints) != _cad_evidence(symbol, catalog["footprints"]):
            _mark_cad_verification_stale(symbol)
    save_ad_catalog(root, catalog)
    return {
        "ok": not parse_errors,
        "catalog_root": str(root),
        "catalog_path": str(_catalog_path(root)),
        "library_file_count": len(files),
        "parsed_files": parsed_files,
        "reused_files": reused_files,
        "schlib_count": sum(1 for p in files if p.suffix.casefold() == ".schlib"),
        "pcblib_count": sum(1 for p in files if p.suffix.casefold() == ".pcblib"),
        "symbol_count": len(catalog["symbols"]),
        "footprint_count": len(catalog["footprints"]),
        "parse_errors": parse_errors,
        "incremental": True,
        "symbol_graphics_modified": False,
    }


def search_ad_catalog(
    catalog_root: str | Path,
    *,
    query: str | None = None,
    resolved_only: bool = False,
    verification_status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    catalog = load_ad_catalog(catalog_root)
    q = _norm(query)
    rows: list[dict[str, Any]] = []
    for row in catalog.get("symbols", {}).values():
        enrichment = row.get("enrichment") or {}
        verification = row.get("verification") or {}
        if resolved_only and enrichment.get("status") != "resolved":
            continue
        if verification_status and _norm(verification.get("status")) != _norm(verification_status):
            continue
        haystack = " ".join([
            str(row.get("name") or ""), str(row.get("library_name") or ""),
            str((row.get("identity_hints") or {}).get("mpn") or ""),
            str((row.get("identity_hints") or {}).get("lcsc_code") or ""),
            str(enrichment.get("mpn") or ""), str(enrichment.get("lcsc_code") or ""),
            str(enrichment.get("brand") or ""), str(enrichment.get("category") or ""),
        ])
        if q and q not in _norm(haystack):
            continue
        rows.append(row)
    rows.sort(key=lambda r: (_norm(r.get("library_name")), _norm(r.get("name"))))
    return rows[: max(1, min(int(limit), 5000))]


def _catalog_export_row(catalog: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    enrichment = row.get("enrichment") or {}
    verification = row.get("verification") or {}
    links = row.get("resolved_footprint_links") or []
    resolved_ids = [c for link in links if link.get("status") == "resolved" for c in link.get("candidates") or []]
    footprints = [catalog.get("footprints", {}).get(asset_id) or {} for asset_id in resolved_ids]
    return {
        "symbol": row.get("name"),
        "schlib": row.get("library_path"),
        "existing_lcsc_code": (row.get("identity_hints") or {}).get("lcsc_code"),
        "existing_mpn": (row.get("identity_hints") or {}).get("mpn"),
        "lcsc_code": enrichment.get("lcsc_code"),
        "mpn": enrichment.get("mpn"),
        "brand": enrichment.get("brand"),
        "category": enrichment.get("category"),
        "package": enrichment.get("package"),
        "description": enrichment.get("name") or row.get("description"),
        "small_batch_price_cny": enrichment.get("small_batch_price_cny"),
        "stock": enrichment.get("stock"),
        "datasheet_url": enrichment.get("datasheet_url"),
        "datasheet_path": enrichment.get("datasheet_path"),
        "product_url": enrichment.get("product_url"),
        "pin_count": len(row.get("pins") or []),
        "footprint_links": " | ".join(str(link.get("name") or "") for link in links),
        "resolved_pcblib": " | ".join(str(fp.get("library_path") or "") for fp in footprints),
        "resolved_footprint": " | ".join(str(fp.get("name") or "") for fp in footprints),
        "pad_count": " | ".join(str((fp.get("geometry") or {}).get("pad_count") or "") for fp in footprints),
        "identity_status": enrichment.get("status") or "not_enriched",
        "verification_status": verification.get("status") or "not_verified",
        "issues": json.dumps(row.get("issues") or [], ensure_ascii=False),
        "last_checked_at": enrichment.get("last_checked_at"),
    }


def export_ad_catalog(
    catalog_root: str | Path,
    *,
    output_dir: str | Path | None = None,
    export_format: str = "both",
) -> dict[str, Any]:
    catalog = load_ad_catalog(catalog_root)
    fmt = str(export_format or "both").casefold().strip()
    if fmt not in {"json", "csv", "both"}:
        raise LibraryError("export_format must be json, csv, or both")
    root = Path(catalog_root).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve() if output_dir else root / "exports"
    out.mkdir(parents=True, exist_ok=True)
    rows = [_catalog_export_row(catalog, row) for row in catalog.get("symbols", {}).values()]
    rows.sort(key=lambda r: (_norm(r.get("schlib")), _norm(r.get("symbol"))))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    files: dict[str, str] = {}
    if fmt in {"json", "both"}:
        path = out / f"altium-component-catalog-{stamp}.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": _now(),
            "catalog_revision": catalog.get("revision"),
            "count": len(rows),
            "components": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        files["json"] = str(path)
    if fmt in {"csv", "both"}:
        path = out / f"altium-component-catalog-{stamp}.csv"
        fieldnames = list(rows[0].keys()) if rows else ["symbol", "schlib", "mpn", "lcsc_code", "package"]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in rows)
        files["csv"] = str(path)
    return {"ok": True, "count": len(rows), "output_dir": str(out), "files": files, "offline": True}


def backup_schlib(path: str | Path, catalog_root: str | Path) -> str:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise LibraryError(f"SchLib not found: {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha1(str(source.parent).encode("utf-8", errors="surrogatepass")).hexdigest()[:10]
    target_dir = Path(catalog_root).expanduser().resolve() / ".schlib-backups" / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}-{source.name}"
    shutil.copy2(source, target)
    return str(target)
