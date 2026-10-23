from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from .altium_links import LibraryLinkError, link_generated_libraries, write_link_report


class AltiumImportError(RuntimeError):
    pass


_C_CODE_RE = re.compile(r"^C\d+$", re.IGNORECASE)
_PIN_VISIBILITY = {"source", "show", "hide"}
_PIN_TEXT_ORIENTATION = {"default", "vertical", "source"}


def managed_cruncher_path() -> Path:
    root = Path(user_data_dir("szlcsc-mcp", appauthor=False)) / "altium-cruncher"
    return root / ("Scripts/altium-cruncher.exe" if os.name == "nt" else "bin/altium-cruncher")


def find_altium_cruncher() -> str | None:
    explicit = os.getenv("SZLCSC_ALTIUM_CRUNCHER")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path.resolve())
        found = shutil.which(explicit)
        return found
    managed = managed_cruncher_path()
    if managed.is_file():
        return str(managed.resolve())
    return shutil.which("altium-cruncher")


def _run_probe(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _probe_version(exe: str) -> tuple[str | None, list[str] | None]:
    """Prefer the current public `version` command, with `--version` fallback."""
    for args in ([exe, "version"], [exe, "--version"]):
        proc = _run_probe(args)
        if proc is not None and proc.returncode == 0:
            text = (proc.stdout or proc.stderr or "").strip()
            if text:
                return text[:500], args
    return None, None


def altium_status() -> dict[str, Any]:
    exe = find_altium_cruncher()
    uv_exe = shutil.which("uv")
    install_command = ("powershell -ExecutionPolicy Bypass -File scripts/install-altium-cruncher.ps1"
                       if os.name == "nt" else "uv tool install --index-url https://pypi.org/simple altium-cruncher==2026.9.7")
    probe_exe = f'"{exe or managed_cruncher_path()}"'
    result: dict[str, Any] = {
        "installed": bool(exe),
        "available": bool(exe),
        "ready": False,
        "status": "missing" if not exe else "checking",
        "executable": exe,
        "converter": "altium-cruncher",
        "third_party": True,
        "license_note": "altium-cruncher is a separate third-party CLI; SZLCSC MCP does not vendor it.",
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "uv": {"installed": bool(uv_exe), "executable": uv_exe},
        "install_hint": install_command,
        "update_hint": install_command,
        "setup_commands": [
            install_command,
            f"{probe_exe} version",
            f"{probe_exe} easyeda-import --help",
            f"{probe_exe} merge --help",
        ],
        "hint": install_command if not exe else None,
        "managed_executable": str(managed_cruncher_path()),
        "windows_repair_command": "powershell -ExecutionPolicy Bypass -File scripts/install-altium-cruncher.ps1",
        "easyeda_import_available": False,
        "merge_available": False,
        "model_3d_download_supported": False,
        "model_3d_placement_supported": False,
    }
    if not exe:
        result["blocked_by"] = "altium-cruncher_not_installed"
        result["message"] = "altium-cruncher is not installed; Altium import/merge tests are NOT runnable."
        return result

    version_text, version_cmd = _probe_version(exe)
    result["version"] = version_text
    result["version_probe_command"] = version_cmd

    import_proc = _run_probe([exe, "easyeda-import", "--help"])
    import_help = "" if import_proc is None else (import_proc.stdout or import_proc.stderr or "")
    result["easyeda_import_available"] = bool(import_proc is not None and import_proc.returncode == 0)
    result["help_excerpt"] = import_help[:1600]
    result["model_3d_download_supported"] = "--no-3d-model-download" in import_help
    result["model_3d_placement_supported"] = "--no-3d-model-placement" in import_help

    merge_proc = _run_probe([exe, "merge", "--help"])
    merge_help = "" if merge_proc is None else (merge_proc.stdout or merge_proc.stderr or "")
    result["merge_available"] = bool(merge_proc is not None and merge_proc.returncode == 0)
    result["merge_help_excerpt"] = merge_help[:1000]

    result["ready"] = bool(result["easyeda_import_available"] and result["merge_available"])
    result["status"] = "ready" if result["ready"] else "partial"
    if result["ready"]:
        result["message"] = "altium-cruncher is ready for EasyEDA import and library merge."
    else:
        missing = []
        if not result["easyeda_import_available"]:
            missing.append("easyeda-import")
        if not result["merge_available"]:
            missing.append("merge")
        result["blocked_by"] = "missing_subcommands"
        result["missing_subcommands"] = missing
        result["message"] = "altium-cruncher was found, but required subcommands are unavailable: " + ", ".join(missing)
        if "uv trampoline" in import_help.casefold():
            result["blocked_by"] = "broken_uv_launcher"
            result["message"] = "The uv launcher points to a missing environment. Run scripts/install-altium-cruncher.ps1 to create a managed installation."
    return result


def _normalize_c_code(value: str) -> str:
    code = value.strip().upper()
    if not _C_CODE_RE.fullmatch(code):
        raise AltiumImportError(f"Altium EasyEDA import requires an LCSC C-code, got {value!r}")
    return code


def _collect_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [str(path) for path in sorted(root.rglob("*")) if path.is_file()]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def inspect_altium_output(
    c_code: str, output_root: str | Path = "altium"
) -> dict[str, Any]:
    """Inspect generated EasyEDA->Altium files/reports without parsing proprietary libraries."""
    code = _normalize_c_code(c_code)
    out = Path(output_root).expanduser().resolve()
    case_dir = out / code
    files = _collect_files(case_dir)
    schlibs = [path for path in files if path.casefold().endswith(".schlib")]
    pcblibs = [path for path in files if path.casefold().endswith(".pcblib")]
    steps = [path for path in files if path.casefold().endswith((".step", ".stp"))]

    schematic_report_path = case_dir / "easyeda-import-report.json"
    footprint_report_path = case_dir / "easyeda-footprint-report.json"
    schematic = _load_json(schematic_report_path)
    footprint = _load_json(footprint_report_path)
    links = _load_json(case_dir / "szlcsc-footprint-links.json")
    concerns: list[str] = []

    schematic_summary = None
    if schematic is not None:
        schematic_summary = {
            "pin_count": schematic.get("pin_count"),
            "unsupported_count": schematic.get("unsupported_count"),
            "warnings": schematic.get("warnings") or [],
            "grid": schematic.get("grid") or {},
            "policy": schematic.get("policy") or {},
        }
        if int(schematic.get("unsupported_count") or 0) > 0:
            concerns.append("schematic report contains unsupported graphic items")
        if schematic.get("warnings"):
            concerns.append("schematic report contains warnings")
    elif schlibs:
        concerns.append("SchLib exists but schematic mapping report is missing/unreadable")

    footprint_summary = None
    if footprint is not None:
        source_pads = footprint.get("source_pad_count")
        generated_pads = footprint.get("generated_pad_count")
        generated_holes = footprint.get("generated_hole_pad_count")
        electrical_generated_pads = (
            generated_pads - generated_holes
            if isinstance(generated_pads, int) and isinstance(generated_holes, int)
            else generated_pads
        )
        pad_count_match = (
            source_pads == electrical_generated_pads
            if isinstance(source_pads, int) and isinstance(electrical_generated_pads, int)
            else None
        )
        footprint_summary = {
            "source_pad_count": source_pads,
            "generated_pad_count": generated_pads,
            "generated_hole_pad_count": generated_holes,
            "generated_electrical_pad_count": electrical_generated_pads,
            "pad_count_match": pad_count_match,
            "unsupported_count": footprint.get("unsupported_count"),
            "warnings": footprint.get("warnings") or [],
            "model_3d_attached": footprint.get("model_3d_attached"),
            "model_3d_centered_fallback": footprint.get("model_3d_centered_fallback"),
            "model_3d_placement_verdict": footprint.get("model_3d_placement_verdict"),
            "model_3d": footprint.get("model_3d") or {},
        }
        if pad_count_match is False:
            concerns.append("source and generated footprint pad counts differ")
        if int(footprint.get("unsupported_count") or 0) > 0:
            concerns.append("footprint report contains unsupported items")
        if footprint.get("warnings"):
            concerns.append("footprint report contains warnings")
        verdict = str(footprint.get("model_3d_placement_verdict") or "").casefold()
        if verdict and verdict != "ok":
            concerns.append(f"3D placement verdict is {verdict}")
        if footprint.get("model_3d_centered_fallback"):
            concerns.append("3D model placement used centered fallback")
    elif pcblibs:
        concerns.append("PcbLib exists but footprint mapping report is missing/unreadable")

    return {
        "lcsc_code": code,
        "output_root": str(out),
        "case_dir": str(case_dir),
        "exists": case_dir.exists(),
        "files": files,
        "schlibs": schlibs,
        "pcblibs": pcblibs,
        "step_files": steps,
        "reports": {
            "schematic": str(schematic_report_path) if schematic_report_path.is_file() else None,
            "footprint": str(footprint_report_path) if footprint_report_path.is_file() else None,
        },
        "quality": {
            "schematic": schematic_summary,
            "footprint": footprint_summary,
            "concerns": concerns,
            "has_schlib": bool(schlibs),
            "has_pcblib": bool(pcblibs),
            "symbol_footprint_links": links,
        },
        "verification_required": True,
    }


def run_altium_import(
    c_code: str,
    output_root: str | Path = "altium",
    *,
    input_json: str | Path | None = None,
    preview: bool = True,
    symbol_only: bool = False,
    no_3d_model_download: bool = False,
    no_3d_model_placement: bool = False,
    pin_name_visibility: str = "source",
    pin_designator_visibility: str = "source",
    pin_text_orientation: str = "default",
    use_source_pin_electrical: bool = False,
    use_source_pin_ieee_symbols: bool = False,
    timeout: int = 240,
) -> dict[str, Any]:
    code = _normalize_c_code(c_code)
    exe = find_altium_cruncher()
    if not exe:
        raise AltiumImportError(
            "altium-cruncher was not found. Install it separately with `uv tool install altium-cruncher` "
            "or set SZLCSC_ALTIUM_CRUNCHER to the executable path."
        )
    if pin_name_visibility not in _PIN_VISIBILITY:
        raise AltiumImportError(f"invalid pin_name_visibility: {pin_name_visibility}")
    if pin_designator_visibility not in _PIN_VISIBILITY:
        raise AltiumImportError(f"invalid pin_designator_visibility: {pin_designator_visibility}")
    if pin_text_orientation not in _PIN_TEXT_ORIENTATION:
        raise AltiumImportError(f"invalid pin_text_orientation: {pin_text_orientation}")

    out = Path(output_root).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "easyeda-import", code, "-o", str(out)]
    if input_json is not None:
        source = Path(input_json).expanduser().resolve()
        if not source.is_file():
            raise AltiumImportError(f"input_json does not exist: {source}")
        cmd.extend(["--input-json", str(source)])
    if preview:
        cmd.append("--preview")
    if symbol_only:
        cmd.append("--symbol-only")
    if no_3d_model_download:
        cmd.append("--no-3d-model-download")
    if no_3d_model_placement:
        cmd.append("--no-3d-model-placement")
    if use_source_pin_electrical:
        cmd.append("--use-source-pin-electrical")
    if use_source_pin_ieee_symbols:
        cmd.append("--use-source-pin-ieee-symbols")
    cmd.extend(["--pin-name-visibility", pin_name_visibility])
    cmd.extend(["--pin-designator-visibility", pin_designator_visibility])
    cmd.extend(["--pin-text-orientation", pin_text_orientation])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(10, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AltiumImportError(f"altium-cruncher timed out after {timeout}s") from exc
    except OSError as exc:
        raise AltiumImportError(f"failed to launch altium-cruncher: {exc}") from exc

    inspected = inspect_altium_output(code, out)
    if proc.returncode == 0 and not symbol_only:
        try:
            links = link_generated_libraries(inspected["schlibs"], inspected["pcblibs"])
            write_link_report(Path(inspected["case_dir"]), links)
            inspected = inspect_altium_output(code, out)
        except LibraryLinkError as exc:
            raise AltiumImportError(str(exc)) from exc
    inspected.update(
        {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
            "verification_note": (
                "Generated SchLib/PcbLib/3D must be checked in Altium against the manufacturer datasheet "
                "before production use."
            ),
        }
    )
    return inspected


def run_altium_import_batch(
    c_codes: list[str],
    output_root: str | Path = "altium",
    *,
    input_jsons: dict[str, str | Path] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source_map = {str(k).upper(): v for k, v in (input_jsons or {}).items()}
    for raw in c_codes[:50]:
        try:
            code = _normalize_c_code(raw)
            rows.append(
                run_altium_import(
                    code,
                    output_root,
                    input_json=source_map.get(code),
                    **kwargs,
                )
            )
        except AltiumImportError as exc:
            rows.append({"ok": False, "lcsc_code": raw, "error": str(exc)})
    return {
        "count": len(rows),
        "success": sum(1 for row in rows if row.get("ok")),
        "failed": sum(1 for row in rows if not row.get("ok")),
        "results": rows,
    }



def _file_nonempty(paths: list[str]) -> bool:
    return bool(paths) and all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in paths)


def run_altium_smoke_test(
    c_codes: list[str],
    output_root: str | Path = "altium-smoke",
    *,
    preview: bool = True,
    require_3d: bool = False,
    test_merge: bool = True,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run a bounded EasyEDA->Altium smoke test and return an explicit check matrix."""
    status = altium_status()
    if not status.get("ready"):
        return {
            "ok": False,
            "status": "blocked",
            "blocked_by": status.get("blocked_by") or "altium_toolchain_not_ready",
            "toolchain": status,
            "results": [],
            "merge": None,
            "manual_verification_required": True,
        }

    codes = []
    for raw in c_codes[:5]:
        code = _normalize_c_code(raw)
        if code not in codes:
            codes.append(code)
    if not codes:
        raise AltiumImportError("smoke test requires at least one C-code")

    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for code in codes:
        row = run_altium_import(code, root, preview=preview, timeout=timeout)
        quality = row.get("quality") or {}
        schematic = quality.get("schematic") or {}
        footprint = quality.get("footprint") or {}
        verdict = str(footprint.get("model_3d_placement_verdict") or "").casefold()
        checks = {
            "import_returncode_zero": row.get("returncode") == 0,
            "schlib_nonempty": _file_nonempty(row.get("schlibs") or []),
            "pcblib_nonempty": _file_nonempty(row.get("pcblibs") or []),
            "symbol_footprint_link_verified": (quality.get("symbol_footprint_links") or {}).get("ok") is True,
            "schematic_report_present": bool(schematic),
            "footprint_report_present": bool(footprint),
            "pad_count_not_mismatched": footprint.get("pad_count_match") is not False,
            "schematic_unsupported_zero": int(schematic.get("unsupported_count") or 0) == 0 if schematic else False,
            "footprint_unsupported_zero": int(footprint.get("unsupported_count") or 0) == 0 if footprint else False,
            "step_nonempty": _file_nonempty(row.get("step_files") or []),
            "model_3d_attached": footprint.get("model_3d_attached") is True,
            "model_3d_verdict_ok": verdict == "ok",
        }
        required = [
            "import_returncode_zero",
            "schlib_nonempty",
            "pcblib_nonempty",
            "symbol_footprint_link_verified",
            "schematic_report_present",
            "footprint_report_present",
            "pad_count_not_mismatched",
            "schematic_unsupported_zero",
            "footprint_unsupported_zero",
        ]
        if require_3d:
            required += ["step_nonempty", "model_3d_attached", "model_3d_verdict_ok"]
        row["smoke_checks"] = checks
        row["automated_pass"] = all(checks[name] for name in required)
        row["required_checks"] = required
        results.append(row)

    merge_result: dict[str, Any] | None = None
    if test_merge and len(results) >= 2 and all(row.get("automated_pass") for row in results):
        with tempfile.TemporaryDirectory(prefix="szlcsc-altium-smoke-") as temp:
            temp_root = Path(temp)
            sch_dir = temp_root / "sch"
            pcb_dir = temp_root / "pcb"
            sch_dir.mkdir()
            pcb_dir.mkdir()
            for row in results:
                code = str(row.get("lcsc_code") or "part")
                for index, src in enumerate(row.get("schlibs") or []):
                    shutil.copy2(src, sch_dir / f"{code}_{index}.SchLib")
                for index, src in enumerate(row.get("pcblibs") or []):
                    shutil.copy2(src, pcb_dir / f"{code}_{index}.PcbLib")
            merge_out = root / "_smoke_merge"
            sch_merge = run_altium_merge(sch_dir, merge_out / "sch", conflicts="rename", timeout=timeout)
            pcb_merge = run_altium_merge(pcb_dir, merge_out / "pcb", conflicts="rename", timeout=timeout)
            merge_result = {
                "attempted": True,
                "schlib": sch_merge,
                "pcblib": pcb_merge,
                "ok": bool(sch_merge.get("ok") and pcb_merge.get("ok") and sch_merge.get("produced_files") and pcb_merge.get("produced_files")),
            }
    elif test_merge:
        merge_result = {
            "attempted": False,
            "ok": False,
            "reason": "merge smoke requires at least two parts and all imports must pass automated checks first",
        }

    automated_ok = all(row.get("automated_pass") for row in results)
    if test_merge and len(results) >= 2:
        automated_ok = automated_ok and bool(merge_result and merge_result.get("ok"))
    return {
        "ok": automated_ok,
        "status": "pass" if automated_ok else "fail",
        "toolchain": status,
        "count": len(results),
        "results": results,
        "merge": merge_result,
        "manual_verification_required": True,
        "manual_verification_note": (
            "Automated smoke PASS does not prove manufacturing correctness. Open generated SchLib/PcbLib in Altium "
            "and verify pin numbering/names, pad geometry, Pin 1, dimensions, and 3D origin/rotation against the manufacturer datasheet."
        ),
    }

def run_altium_merge(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    conflicts: str = "rename",
    timeout: int = 240,
) -> dict[str, Any]:
    """Merge a directory containing only SchLib or only PcbLib files via altium-cruncher."""
    if conflicts not in {"rename", "skip", "error"}:
        raise AltiumImportError(f"invalid merge conflicts mode: {conflicts}")
    source = Path(input_dir).expanduser().resolve()
    if not source.is_dir():
        raise AltiumImportError(f"merge input directory not found: {source}")
    schlibs = sorted(source.glob("*.SchLib"))
    pcblibs = sorted(source.glob("*.PcbLib"))
    if schlibs and pcblibs:
        raise AltiumImportError("merge input must contain only one Altium library type")
    if not schlibs and not pcblibs:
        raise AltiumImportError(f"no SchLib/PcbLib files in merge input: {source}")
    kind = "pcblib" if pcblibs else "schlib"
    if kind == "pcblib" and conflicts != "rename":
        raise AltiumImportError("PcbLib merge currently supports only conflicts='rename'")
    exe = find_altium_cruncher()
    if not exe:
        raise AltiumImportError(
            "altium-cruncher was not found. Install it separately with `uv tool install altium-cruncher` "
            "or set SZLCSC_ALTIUM_CRUNCHER to the executable path."
        )
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "merge", str(source), "-o", str(out), "--conflicts", conflicts]
    before = {p.resolve() for p in out.glob("*") if p.is_file()}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(10, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AltiumImportError(f"altium-cruncher merge timed out after {timeout}s") from exc
    except OSError as exc:
        raise AltiumImportError(f"failed to launch altium-cruncher merge: {exc}") from exc
    after = {p.resolve() for p in out.glob("*") if p.is_file()}
    produced = sorted(str(p) for p in (after - before))
    if not produced:
        suffix = ".PcbLib" if kind == "pcblib" else ".SchLib"
        produced = sorted(str(p.resolve()) for p in out.glob(f"*{suffix}"))
    return {
        "ok": proc.returncode == 0,
        "kind": kind,
        "input_dir": str(source),
        "input_count": len(pcblibs or schlibs),
        "output_dir": str(out),
        "produced_files": produced,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
        "conflicts": conflicts,
    }
