from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .cache import SqliteCache
from .normalize import normalize_c_code
from .http import SafeSession, safe_get
from .errors import SourcingError
from . import __version__


class EasyEDAError(SourcingError):
    """Raised when EasyEDA public component assets cannot be retrieved safely."""


_C_CODE_RE = re.compile(r"^C\d+$", re.IGNORECASE)
_ALLOWED_EDA_DOWNLOAD_SUFFIXES = ("easyeda.com", "modules.easyeda.com")


@dataclass(frozen=True)
class EasyEDA3DModelRef:
    uuid: str
    title: str | None = None
    origin: str | None = None
    z: str | None = None
    rotation: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _safe_name(value: str, fallback: str = "asset") -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip()).strip("._-")
    return value[:100] or fallback


def _walk(obj: Any):
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _model_ref_from_dict(obj: dict[str, Any]) -> EasyEDA3DModelRef | None:
    attrs = obj.get("attrs") if isinstance(obj.get("attrs"), dict) else obj
    uuid = attrs.get("uuid") or attrs.get("modelUuid") or attrs.get("model_uuid")
    if not isinstance(uuid, str) or not uuid.strip():
        return None
    # Avoid mistaking arbitrary UUID-bearing metadata for 3D model nodes.
    c_etype = str(attrs.get("c_etype") or "").strip().casefold()
    if c_etype and c_etype != "outline3d":
        return None
    has_3d_hint = c_etype == "outline3d" or any(
        key in attrs for key in ("c_origin", "c_rotation", "z", "modelUuid", "model_uuid")
    )
    if not has_3d_hint:
        return None
    return EasyEDA3DModelRef(
        uuid=uuid.strip(),
        title=str(attrs.get("title")) if attrs.get("title") not in (None, "") else None,
        origin=str(attrs.get("c_origin")) if attrs.get("c_origin") not in (None, "") else None,
        z=str(attrs.get("z")) if attrs.get("z") not in (None, "") else None,
        rotation=str(attrs.get("c_rotation")) if attrs.get("c_rotation") not in (None, "") else None,
    )


def extract_3d_models(cad_data: Any) -> list[EasyEDA3DModelRef]:
    """Extract EasyEDA 3D model UUIDs from nested CAD JSON / SVGNODE shape strings."""
    found: dict[str, EasyEDA3DModelRef] = {}
    for node in _walk(cad_data):
        if isinstance(node, str) and "SVGNODE~" in node:
            tail = node.split("SVGNODE~", 1)[1].lstrip()
            try:
                parsed, _ = json.JSONDecoder().raw_decode(tail)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                ref = _model_ref_from_dict(parsed)
                if ref:
                    found.setdefault(ref.uuid, ref)
        elif isinstance(node, dict):
            ref = _model_ref_from_dict(node)
            if ref:
                found.setdefault(ref.uuid, ref)
    return list(found.values())


def split_svg_payload(payload: Any) -> tuple[list[str], str | None]:
    """Normalize EasyEDA /svgs result: symbol unit(s) first, footprint last."""
    if isinstance(payload, dict) and "result" in payload:
        payload = payload.get("result")
    if isinstance(payload, dict):
        for key in ("svgs", "list", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return [], None

    svgs: list[str] = []
    for item in payload:
        if isinstance(item, str):
            svg = item
        elif isinstance(item, dict):
            svg = next(
                (
                    item.get(key)
                    for key in ("svg", "data", "content", "svgData")
                    if isinstance(item.get(key), str)
                ),
                None,
            )
        else:
            svg = None
        if isinstance(svg, str) and "<svg" in svg.casefold():
            svgs.append(svg)
    if not svgs:
        return [], None
    if len(svgs) == 1:
        return [], svgs[0]
    return svgs[:-1], svgs[-1]


class EasyEDAAssetClient:
    """Small read-only client for public EasyEDA/LCSC CAD asset endpoints."""

    COMPONENTS_ENDPOINT = "https://easyeda.com/api/products/{c_code}/components"
    SVGS_ENDPOINT = "https://easyeda.com/api/products/{c_code}/svgs"
    OBJ_ENDPOINT = "https://modules.easyeda.com/3dmodel/{uuid}"
    STEP_ENDPOINT = "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{uuid}"

    def __init__(
        self,
        cache: SqliteCache | None = None,
        timeout: float = 20.0,
        request_delay: float | None = None,
        retries: int = 2,
    ):
        self.cache = cache or SqliteCache(os.getenv("SZLCSC_CACHE_PATH"))
        self.timeout = float(os.getenv("SZLCSC_EDA_TIMEOUT", timeout))
        self.request_delay = float(
            os.getenv(
                "SZLCSC_EDA_REQUEST_DELAY",
                request_delay if request_delay is not None else 0.5,
            )
        )
        self.retries = int(os.getenv("SZLCSC_EDA_RETRIES", retries))
        self.max_download_bytes = int(
            os.getenv("SZLCSC_EDA_MAX_DOWNLOAD_BYTES", str(80 * 1024 * 1024))
        )
        if self.max_download_bytes <= 0:
            raise ValueError("SZLCSC_EDA_MAX_DOWNLOAD_BYTES must be positive")
        self.session = SafeSession(delay=self.request_delay, retries=self.retries, timeout=self.timeout)
        self.session.headers.update(
            {
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                "User-Agent": f"szlcsc-mcp/{__version__} (+read-only EasyEDA asset fetch; respectful caching)",
            }
        )
        self._rate_lock = threading.Lock()
        self._last_request = 0.0

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def normalize_c_code(value: str) -> str:
        code = normalize_c_code(value.strip()).upper()
        if not _C_CODE_RE.fullmatch(code):
            raise EasyEDAError(f"EasyEDA assets require an LCSC C-code, got {value!r}")
        return code

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self.request_delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if payload.get("success") is False:
            raise EasyEDAError(
                f"EasyEDA API returned failure: {payload.get('message') or payload.get('msg')}"
            )
        if "result" in payload:
            return payload.get("result")
        return payload

    def _get_json(self, url: str, *, ttl: int = 86400) -> Any:
        key = self.cache.make_key("GET", url, None)
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached

        try:
            with self.session.get(url, timeout=self.timeout) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise EasyEDAError("EasyEDA API unexpectedly redirected", code="INVALID_RESPONSE")
                result = self._unwrap(response.json())
            self.cache.set(key, url, result)
            return result
        except SourcingError as exc:
            raise EasyEDAError(str(exc), code=exc.code, retryable=exc.retryable,
                               retry_after=exc.retry_after) from exc
        except (requests.RequestException, ValueError) as exc:
            raise EasyEDAError("EasyEDA request failed or returned invalid JSON", code="INVALID_RESPONSE") from exc

    def get_cad_data(self, c_code: str) -> Any:
        code = self.normalize_c_code(c_code)
        url = self.COMPONENTS_ENDPOINT.format(c_code=code)
        return self._get_json(url, ttl=86400)

    def get_svgs(self, c_code: str) -> tuple[list[str], str | None]:
        code = self.normalize_c_code(c_code)
        url = self.SVGS_ENDPOINT.format(c_code=code)
        return split_svg_payload(self._get_json(url, ttl=86400))

    @staticmethod
    def _validate_download_url(url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in _ALLOWED_EDA_DOWNLOAD_SUFFIXES
        ):
            raise EasyEDAError(f"Refusing non-EasyEDA asset URL: {url}")

    def _download_binary(self, url: str) -> bytes:
        self._validate_download_url(url)
        try:
            with safe_get(self.session, url, validate=self._validate_download_url,
                          timeout=self.timeout, stream=True) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_download_bytes:
                        raise EasyEDAError(
                            f"EDA asset exceeds size limit ({self.max_download_bytes} bytes)"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except SourcingError as exc:
            raise EasyEDAError(str(exc), code=exc.code, retryable=exc.retryable,
                               retry_after=exc.retry_after) from exc
        except requests.RequestException as exc:
            raise EasyEDAError("EDA binary download failed", code="NETWORK_ERROR", retryable=True) from exc

    @staticmethod
    def _validate_asset_content(data: bytes, kind: str) -> list[str]:
        head = data[:1024].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            raise EasyEDAError(f"{kind} endpoint returned HTML instead of a CAD asset")
        warnings: list[str] = []
        if kind == "step" and b"iso-10303-21" not in data[:4096].lower():
            warnings.append("STEP header ISO-10303-21 was not found; verify the downloaded model manually")
        if kind == "obj" and not data.strip():
            raise EasyEDAError("OBJ asset is empty")
        return warnings

    def write_altium_source(
        self, c_code: str, output_path: str | Path
    ) -> Path:
        """Write a converter-compatible EasyEDA API response with the original result wrapper."""
        code = self.normalize_c_code(c_code)
        cad_data = self.get_cad_data(code)
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"success": True, "result": cad_data},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return path

    def download_bundle(
        self,
        c_code: str,
        output_root: str | Path = "eda-assets",
        *,
        include_svgs: bool = True,
        include_obj: bool = True,
        include_step: bool = True,
        force: bool = True,
    ) -> dict[str, Any]:
        code = self.normalize_c_code(c_code)
        root = Path(output_root).expanduser().resolve() / code
        root.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        files: dict[str, Any] = {}

        cad_data = self.get_cad_data(code)
        raw_path = root / "easyeda_raw.json"
        if force or not raw_path.exists():
            raw_path.write_text(
                json.dumps(
                    {"success": True, "result": cad_data},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        files["raw_json"] = str(raw_path)

        if include_svgs:
            try:
                symbols, footprint = self.get_svgs(code)
                symbol_paths: list[str] = []
                for index, svg in enumerate(symbols, 1):
                    path = root / ("symbol.svg" if len(symbols) == 1 else f"symbol_{index}.svg")
                    if force or not path.exists():
                        path.write_text(svg, encoding="utf-8")
                    symbol_paths.append(str(path))
                files["symbols"] = symbol_paths
                if footprint:
                    path = root / "footprint.svg"
                    if force or not path.exists():
                        path.write_text(footprint, encoding="utf-8")
                    files["footprint"] = str(path)
            except EasyEDAError as exc:
                warnings.append(f"SVG fetch failed: {exc}")

        refs = extract_3d_models(cad_data)
        model_files: list[dict[str, Any]] = []
        if include_obj or include_step:
            model_dir = root / "3d"
            model_dir.mkdir(exist_ok=True)
            for ref in refs:
                stem = _safe_name(ref.title or "model") + "__" + _safe_name(ref.uuid[:12])
                row: dict[str, Any] = {"model": ref.to_dict()}
                if include_obj:
                    obj_path = model_dir / f"{stem}.obj"
                    try:
                        if force or not obj_path.exists():
                            data = self._download_binary(self.OBJ_ENDPOINT.format(uuid=ref.uuid))
                            warnings.extend(self._validate_asset_content(data, "obj"))
                            obj_path.write_bytes(data)
                        row["obj"] = str(obj_path)
                    except EasyEDAError as exc:
                        row["obj_error"] = str(exc)
                if include_step:
                    step_path = model_dir / f"{stem}.step"
                    try:
                        if force or not step_path.exists():
                            data = self._download_binary(self.STEP_ENDPOINT.format(uuid=ref.uuid))
                            warnings.extend(self._validate_asset_content(data, "step"))
                            step_path.write_bytes(data)
                        row["step"] = str(step_path)
                    except EasyEDAError as exc:
                        row["step_error"] = str(exc)
                model_files.append(row)
        files["models"] = model_files

        asset_summary = {
            "symbol_count": len(files.get("symbols") or []),
            "footprint_available": bool(files.get("footprint")),
            "model_ref_count": len(refs),
            "has_3d_model_refs": bool(refs),
            "obj_downloaded": sum(1 for row in model_files if row.get("obj")),
            "step_downloaded": sum(1 for row in model_files if row.get("step")),
            "obj_errors": sum(1 for row in model_files if row.get("obj_error")),
            "step_errors": sum(1 for row in model_files if row.get("step_error")),
            "step_available": any(bool(row.get("step")) for row in model_files),
            "obj_available": any(bool(row.get("obj")) for row in model_files),
        }

        manifest = {
            "schema": 2,
            "lcsc_code": code,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "EasyEDA public component asset endpoints",
            "files": files,
            "model_refs": [ref.to_dict() for ref in refs],
            "asset_summary": asset_summary,
            "warnings": warnings,
            "verification_required": True,
        }
        manifest_path = root / "eda_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        manifest["manifest"] = str(manifest_path)
        return manifest
