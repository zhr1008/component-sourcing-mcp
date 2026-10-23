from pathlib import Path
from types import SimpleNamespace

import pytest

import szlcsc_mcp.altium as altium


def test_altium_status_when_missing(monkeypatch):
    monkeypatch.setattr(altium, "managed_cruncher_path", lambda: Path("/missing/managed-cruncher"))
    monkeypatch.setattr(altium.shutil, "which", lambda _: None)
    monkeypatch.delenv("SZLCSC_ALTIUM_CRUNCHER", raising=False)
    result = altium.altium_status()
    assert result["installed"] is False
    assert result["ready"] is False
    assert result["status"] == "missing"
    assert result["easyeda_import_available"] is False
    assert result["merge_available"] is False
    assert "NOT runnable" in result["message"]
    assert "install-altium-cruncher.ps1" in result["install_hint"] or "uv tool install" in result["install_hint"]
    assert result["hint"] == result["install_hint"]


def test_managed_converter_precedes_stale_path_launcher(monkeypatch, tmp_path):
    managed = tmp_path / "altium-cruncher.exe"
    managed.touch()
    monkeypatch.delenv("SZLCSC_ALTIUM_CRUNCHER", raising=False)
    monkeypatch.setattr(altium, "managed_cruncher_path", lambda: managed)
    monkeypatch.setattr(altium.shutil, "which", lambda value: "/stale/uv/launcher")
    assert altium.find_altium_cruncher() == str(managed.resolve())
    monkeypatch.setenv("SZLCSC_ALTIUM_CRUNCHER", str(tmp_path / "missing.exe"))
    monkeypatch.setattr(altium.shutil, "which", lambda value: None)
    assert altium.find_altium_cruncher() is None


def test_run_altium_import_builds_safe_argument_list(monkeypatch, tmp_path):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        case_dir = tmp_path / "out" / "C2040"
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "part.SchLib").write_bytes(b"sch")
        (case_dir / "part.PcbLib").write_bytes(b"pcb")
        (case_dir / "easyeda-import-report.json").write_text(
            '{"pin_count":8,"unsupported_count":0,"warnings":[]}', encoding="utf-8"
        )
        (case_dir / "easyeda-footprint-report.json").write_text(
            '{"source_pad_count":8,"generated_pad_count":8,"unsupported_count":0,"warnings":[],"model_3d_attached":true,"model_3d_placement_verdict":"ok"}',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(altium.subprocess, "run", fake_run)
    # This case isolates CLI argument construction; real binary linking is
    # covered by test_generated_altium_links and the installed-server smoke.
    monkeypatch.setattr(altium, "link_generated_libraries", lambda *args: {"ok": True})
    result = altium.run_altium_import(
        "c2040",
        tmp_path / "out",
        preview=True,
        pin_name_visibility="show",
        pin_designator_visibility="hide",
        pin_text_orientation="vertical",
        no_3d_model_placement=True,
    )
    assert result["ok"] is True
    cmd, kwargs = calls[0]
    assert cmd[:3] == ["/fake/altium-cruncher", "easyeda-import", "C2040"]
    assert "--preview" in cmd
    assert "--no-3d-model-placement" in cmd
    assert cmd[cmd.index("--pin-name-visibility") + 1] == "show"
    assert kwargs["check"] is False
    assert "shell" not in kwargs
    assert any(path.endswith(".SchLib") for path in result["files"])
    assert any(path.endswith(".PcbLib") for path in result["files"])
    assert result["quality"]["schematic"]["pin_count"] == 8
    assert result["quality"]["footprint"]["pad_count_match"] is True
    assert result["quality"]["footprint"]["model_3d_placement_verdict"] == "ok"


def test_run_altium_import_rejects_invalid_enum(monkeypatch, tmp_path):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")
    with pytest.raises(altium.AltiumImportError):
        altium.run_altium_import("C2040", tmp_path, pin_name_visibility="whatever")


def test_run_altium_import_rejects_non_c_code(monkeypatch, tmp_path):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")
    with pytest.raises(altium.AltiumImportError):
        altium.run_altium_import("LM358", tmp_path)


def test_run_altium_import_batch_uses_source_map(monkeypatch, tmp_path):
    calls = []

    def fake_one(code, output_root, **kwargs):
        calls.append((code, kwargs.get("input_json")))
        return {"ok": True, "lcsc_code": code}

    monkeypatch.setattr(altium, "run_altium_import", fake_one)
    result = altium.run_altium_import_batch(
        ["C1", "c2"],
        tmp_path,
        input_jsons={"C1": tmp_path / "1.json", "C2": tmp_path / "2.json"},
        preview=True,
    )
    assert result["success"] == 2
    assert calls[0][1] == tmp_path / "1.json"
    assert calls[1][1] == tmp_path / "2.json"


def test_inspect_altium_output_flags_pad_mismatch_and_3d_warning(tmp_path):
    case_dir = tmp_path / "C9"
    case_dir.mkdir()
    (case_dir / "C9.SchLib").write_bytes(b"sch")
    (case_dir / "C9.PcbLib").write_bytes(b"pcb")
    (case_dir / "easyeda-import-report.json").write_text(
        '{"pin_count":4,"unsupported_count":1,"warnings":["x"]}', encoding="utf-8"
    )
    (case_dir / "easyeda-footprint-report.json").write_text(
        '{"source_pad_count":4,"generated_pad_count":3,"unsupported_count":0,"warnings":[],"model_3d_centered_fallback":true,"model_3d_placement_verdict":"needs_checking"}',
        encoding="utf-8",
    )
    result = altium.inspect_altium_output("C9", tmp_path)
    assert result["quality"]["footprint"]["pad_count_match"] is False
    concerns = " ".join(result["quality"]["concerns"])
    assert "pad counts differ" in concerns
    assert "needs_checking" in concerns
    assert "centered fallback" in concerns


def test_run_altium_merge_safe_args(monkeypatch, tmp_path):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")
    source = tmp_path / "Power"
    source.mkdir()
    (source / "A.SchLib").write_bytes(b"a")
    out = tmp_path / "merged"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out.mkdir(parents=True, exist_ok=True)
        (out / "Power_merged.SchLib").write_bytes(b"m")
        return SimpleNamespace(returncode=0, stdout="merged", stderr="")

    monkeypatch.setattr(altium.subprocess, "run", fake_run)
    result = altium.run_altium_merge(source, out, conflicts="skip")
    assert result["ok"] is True
    assert result["kind"] == "schlib"
    cmd, kwargs = calls[0]
    assert cmd[:2] == ["/fake/altium-cruncher", "merge"]
    assert "--conflicts" in cmd and cmd[cmd.index("--conflicts") + 1] == "skip"
    assert "shell" not in kwargs


def test_run_altium_merge_pcblib_requires_rename(monkeypatch, tmp_path):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")
    source = tmp_path / "Power"
    source.mkdir()
    (source / "A.PcbLib").write_bytes(b"a")
    with pytest.raises(altium.AltiumImportError):
        altium.run_altium_merge(source, tmp_path / "out", conflicts="skip")


def test_altium_status_ready_when_required_subcommands_exist(monkeypatch):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[-1] == "version":
            return SimpleNamespace(returncode=0, stdout="altium-cruncher 1.2.3", stderr="")
        if cmd[1:3] == ["easyeda-import", "--help"]:
            return SimpleNamespace(
                returncode=0,
                stdout="usage: ok --no-3d-model-download --no-3d-model-placement",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="usage: ok", stderr="")

    monkeypatch.setattr(altium.subprocess, "run", fake_run)
    result = altium.altium_status()
    assert result["installed"] is True
    assert result["ready"] is True
    assert result["status"] == "ready"
    assert result["easyeda_import_available"] is True
    assert result["merge_available"] is True
    assert result["version"] == "altium-cruncher 1.2.3"
    assert result["version_probe_command"][-1] == "version"
    assert result["model_3d_download_supported"] is True
    assert result["model_3d_placement_supported"] is True
    assert len(calls) == 3



def test_altium_version_falls_back_to_dash_version(monkeypatch):
    monkeypatch.setattr(altium, "find_altium_cruncher", lambda: "/fake/altium-cruncher")

    def fake_run(cmd, **kwargs):
        if cmd[-1] == "version":
            return SimpleNamespace(returncode=2, stdout="", stderr="bad command")
        if cmd[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="altium-cruncher legacy", stderr="")
        return SimpleNamespace(returncode=0, stdout="usage", stderr="")

    monkeypatch.setattr(altium.subprocess, "run", fake_run)
    result = altium.altium_status()
    assert result["version"] == "altium-cruncher legacy"
    assert result["version_probe_command"][-1] == "--version"


def test_altium_smoke_test_blocked_when_toolchain_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(altium, "altium_status", lambda: {"ready": False, "blocked_by": "altium-cruncher_not_installed"})
    result = altium.run_altium_smoke_test(["C2040"], tmp_path)
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "altium-cruncher_not_installed"
    assert result["manual_verification_required"] is True


@pytest.mark.parametrize("linked", [True, False])
def test_altium_smoke_test_import_and_merge(monkeypatch, tmp_path, linked):
    monkeypatch.setattr(altium, "altium_status", lambda: {"ready": True, "status": "ready"})

    def fake_import(code, output_root, **kwargs):
        case_dir = Path(output_root) / code
        case_dir.mkdir(parents=True, exist_ok=True)
        sch = case_dir / f"{code}.SchLib"
        pcb = case_dir / f"{code}.PcbLib"
        step = case_dir / f"{code}.step"
        sch.write_bytes(b"sch")
        pcb.write_bytes(b"pcb")
        step.write_bytes(b"STEP")
        return {
            "ok": True,
            "returncode": 0,
            "lcsc_code": code,
            "schlibs": [str(sch)],
            "pcblibs": [str(pcb)],
            "step_files": [str(step)],
            "quality": {
                "symbol_footprint_links": {"ok": linked},
                "schematic": {"unsupported_count": 0},
                "footprint": {
                    "unsupported_count": 0,
                    "pad_count_match": True,
                    "model_3d_attached": True,
                    "model_3d_placement_verdict": "ok",
                },
                "concerns": [],
            },
        }

    def fake_merge(input_dir, output_dir, **kwargs):
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        suffix = ".SchLib" if any(Path(input_dir).glob("*.SchLib")) else ".PcbLib"
        target = out / f"merged{suffix}"
        target.write_bytes(b"merged")
        return {"ok": True, "produced_files": [str(target)]}

    monkeypatch.setattr(altium, "run_altium_import", fake_import)
    monkeypatch.setattr(altium, "run_altium_merge", fake_merge)
    result = altium.run_altium_smoke_test(["C1", "C2"], tmp_path, require_3d=True, test_merge=True)
    assert result["ok"] is linked
    assert result["status"] == ("pass" if linked else "fail")
    assert all(row["automated_pass"] is linked for row in result["results"])
    assert result["merge"]["ok"] is linked
    assert result["manual_verification_required"] is True
