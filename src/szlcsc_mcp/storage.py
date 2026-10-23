from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from .models import Part

ARCHIVE_LAYOUT_VERSION = 2
ARCHIVE_NAMING_POLICY = "category-mpn-c-code"

_INVALID_WIN_CHARS = re.compile(r'[\\/:*?"<>|]+')
_MULTI_SPACE = re.compile(r"\s+")
# SZLCSC category labels may legitimately contain '/' inside a single name,
# e.g. "单片机(MCU/MPU/SOC)". Treat only explicit breadcrumb marks as hard
# hierarchy separators. Slash/backslash/pipe are hierarchy separators only
# when visually separated by whitespace.
_CATEGORY_HARD_SPLIT = re.compile(r"\s*(?:>|›|→)\s*")
_CATEGORY_SPACED_SPLIT = re.compile(r"\s+(?:/|\\|\|)\s+")


def safe_segment(value: str | None, fallback: str = "未分类", *, max_len: int = 120) -> str:
    """Return a Windows-safe path segment while preserving useful Chinese text."""
    text = str(value or fallback).strip() or fallback
    text = _INVALID_WIN_CHARS.sub("_", text)
    text = _MULTI_SPACE.sub(" ", text).strip(" .")
    return (text[:max_len].rstrip(" .") or fallback)


def category_segments(category: str | None) -> list[str]:
    """Preserve a SZLCSC category hierarchy without breaking category aliases.

    The domestic payload often exposes labels such as ``单片机(MCU/MPU/SOC)``.
    The slashes there are part of the category name, not breadcrumb separators,
    so they must survive until ``safe_segment`` converts them to Windows-safe
    underscores. Explicit breadcrumbs (``>``, ``›``, ``→``) are always split;
    slash/backslash/pipe separators are split only when surrounded by spaces.
    """
    raw = str(category or "未分类").strip() or "未分类"
    pieces: list[str] = []
    for hard_piece in _CATEGORY_HARD_SPLIT.split(raw):
        if not hard_piece.strip():
            continue
        pieces.extend(item for item in _CATEGORY_SPACED_SPLIT.split(hard_piece) if item.strip())
    parts = [safe_segment(item) for item in pieces]
    return parts or ["未分类"]


def asset_basename(part: Part | dict[str, Any]) -> str:
    """Build the human-facing `类型-型号-C编号` basename requested for archives."""
    if isinstance(part, Part):
        category = part.category
        mpn = part.mpn
        code = part.lcsc_code
    else:
        category = part.get("category")
        mpn = part.get("mpn")
        code = part.get("lcsc_code")
    leaf = category_segments(category)[-1]
    mpn_text = safe_segment(str(mpn or "未知型号"), "未知型号", max_len=100)
    code_text = safe_segment(str(code or "未知编号").upper(), "未知编号", max_len=40)
    return safe_segment(f"{leaf}-{mpn_text}-{code_text}", "器件", max_len=220)


def archive_roots(library_root: str | Path, part: Part | dict[str, Any]) -> dict[str, Any]:
    root = Path(library_root).expanduser().resolve()
    segments = category_segments(part.category if isinstance(part, Part) else part.get("category"))
    basename = asset_basename(part)
    category_path = Path(*segments)
    classified = root / "classified"
    flat = root / "flat"
    return {
        "root": root,
        "basename": basename,
        "category_segments": segments,
        "category_path": category_path,
        "classified_root": classified,
        "flat_root": flat,
        "datasheet_dir": classified / "datasheets" / category_path,
        "schlib_dir": classified / "schlib" / category_path,
        "pcblib_dir": classified / "pcblib" / category_path,
        "model3d_dir": classified / "3d" / category_path,
        "report_dir": classified / "reports" / category_path,
    }


def _next_named_path(directory: Path, basename: str, suffix: str, index: int) -> Path:
    extra = "" if index == 0 else f"-{index + 1}"
    return directory / f"{basename}{extra}{suffix}"


def mirror_file(source: str | Path, target: str | Path, *, overwrite: bool = True) -> str:
    src = Path(source)
    dst = Path(target)
    if not src.is_file():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        return str(dst)
    shutil.copy2(src, dst)
    return str(dst)


def archive_existing_files(
    part: Part,
    library_root: str | Path,
    *,
    schlibs: Iterable[str | Path] = (),
    pcblibs: Iterable[str | Path] = (),
    step_files: Iterable[str | Path] = (),
    report_files: Iterable[str | Path] = (),
    overwrite: bool = True,
) -> dict[str, Any]:
    """Archive generated CAD assets in classified storage and a flat browse mirror."""
    layout = archive_roots(library_root, part)
    flat: Path = layout["flat_root"]
    flat.mkdir(parents=True, exist_ok=True)
    assets: dict[str, dict[str, list[str]]] = {}

    def copy_group(kind: str, values: Iterable[str | Path], directory: Path, default_suffix: str) -> None:
        classified_paths: list[str] = []
        flat_paths: list[str] = []
        existing = [Path(value) for value in values if Path(value).is_file()]
        for idx, src in enumerate(existing):
            suffix = src.suffix or default_suffix
            classified_target = _next_named_path(directory, layout["basename"], suffix, idx)
            flat_target = _next_named_path(flat, layout["basename"], suffix, idx)
            classified_paths.append(mirror_file(src, classified_target, overwrite=overwrite))
            flat_paths.append(mirror_file(src, flat_target, overwrite=overwrite))
        assets[kind] = {"classified": classified_paths, "flat": flat_paths}

    copy_group("schlib", schlibs, layout["schlib_dir"], ".SchLib")
    copy_group("pcblib", pcblibs, layout["pcblib_dir"], ".PcbLib")
    copy_group("step", step_files, layout["model3d_dir"], ".step")

    report_paths: list[str] = []
    for idx, src_raw in enumerate(report_files):
        src = Path(src_raw)
        if not src.is_file():
            continue
        suffix = src.suffix or ".json"
        # Preserve report role in the filename while keeping deterministic v2 numbering.
        role = safe_segment(src.stem, "report", max_len=80)
        extra = "" if idx == 0 else f"-{idx + 1}"
        target = layout["report_dir"] / f"{layout['basename']}-{role}{extra}{suffix}"
        report_paths.append(mirror_file(src, target, overwrite=overwrite))
    assets["reports"] = {"classified": report_paths, "flat": []}

    return {
        "layout_version": ARCHIVE_LAYOUT_VERSION,
        "naming_policy": ARCHIVE_NAMING_POLICY,
        "basename": layout["basename"],
        "category_segments": layout["category_segments"],
        "classified_root": str(layout["classified_root"]),
        "flat_root": str(layout["flat_root"]),
        "assets": assets,
    }
