"""Run installer failure paths without creating environments or using a network."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell installer")
@pytest.mark.parametrize("fail_venv", [True, False])
def test_windows_installer_stops_on_native_failure_from_another_directory(tmp_path, fail_venv):
    project = tmp_path / "project with spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/install.ps1", scripts / "install.ps1")
    binaries = project / ".venv/Scripts"
    binaries.mkdir(parents=True)
    # Record native invocations so a later failure cannot mask a missed stop.
    source = tmp_path / "NativeFixture.cs"
    source.write_text('''
using System;
using System.IO;
public class NativeFixture {
    public static int Main(string[] args) {
        File.AppendAllText(Environment.GetEnvironmentVariable("INSTALL_TRACE"),
            "python " + String.Join(" ", args) + Environment.NewLine);
        return 23;
    }
}
''', encoding="utf-8")
    compiled = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-Command", 'Add-Type -Path $env:INSTALL_SOURCE -OutputAssembly $env:INSTALL_NATIVE -OutputType ConsoleApplication'],
        env={**os.environ, "INSTALL_SOURCE":str(source), "INSTALL_NATIVE":str(binaries / "python.exe")},
        capture_output=True, timeout=30,
    )
    assert compiled.returncode == 0, "Native test fixture compilation failed"
    native = Path(os.environ["SystemRoot"]) / "System32/whoami.exe"
    shutil.copy2(native, binaries / "pip.exe")
    (binaries / "component-sourcing-mcp.exe").write_bytes(b"fixture")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "calls.txt"
    (fake_bin / "py.cmd").write_text(
        '@echo off\necho %CD%>>"%INSTALL_TRACE%"\nexit /b ' + ("23" if fail_venv else "0") + "\n",
        encoding="utf-8",
    )
    # Retain a stale launcher in the caller's directory to prove failure cannot
    # be mistaken for a successful installation of the wrong project.
    unrelated = tmp_path / "unrelated"
    shutil.copytree(project / ".venv", unrelated / ".venv")
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(scripts / "install.ps1")],
        cwd=unrelated,
        env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"], "INSTALL_TRACE": str(trace)},
        capture_output=True, timeout=20,
    )
    assert result.returncode != 0
    assert b"Installed Component Sourcing MCP" not in result.stdout
    calls = trace.read_text().splitlines()
    assert Path(calls[0]).resolve() == project.resolve()
    assert calls[1:] == ([] if fail_venv else ["python -m pip install --upgrade pip"])


def test_posix_installer_uses_its_project_when_called_elsewhere(tmp_path):
    git = shutil.which("git")
    bash = Path(git).resolve().parents[1] / "bin/bash.exe" if os.name == "nt" and git else Path(shutil.which("bash") or "missing")
    if not bash.is_file():
        pytest.skip("Git Bash or POSIX bash is required")
    project = tmp_path / "project with spaces"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/install.sh", project / "scripts/install.sh")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    trace = tmp_path / "cwd.txt"
    result = subprocess.run(
        [str(bash), "--noprofile", "--norc", "-c", 'python3() { ' + ("pwd -W" if os.name == "nt" else "pwd") + ' > "$INSTALL_TRACE"; return 23; }; export -f python3; exec "$BASH" "$INSTALL_SCRIPT"'],
        cwd=unrelated,
        env={**os.environ,"INSTALL_TRACE":trace.as_posix(),"INSTALL_SCRIPT":(project / "scripts/install.sh").as_posix()},
        capture_output=True, timeout=20,
    )
    assert result.returncode == 23
    assert b"Installed Component Sourcing MCP" not in result.stdout
    assert Path(trace.read_text().strip()).resolve() == project.resolve()
