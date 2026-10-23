"""Exercise publication guards without credentials or remote mutations."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader preserves the GitHub YAML 1.2 key `on` as a string.
    path = ROOT / "examples/maintainer-private-validation.yml" if name == "antigravity-local-validation.yml" else ROOT / ".github/workflows" / name
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def release_script(name):
    return next(s["run"] for s in workflow("release.yml")["jobs"]["publish"]["steps"] if s.get("name") == name)


@pytest.fixture
def bash():
    git = shutil.which("git")
    candidates = [Path(git).resolve().parents[1] / "bin/bash.exe"] if git else []
    # Avoid the Windows System32 WSL launcher: tests need the Git for Windows shell.
    if os.name != "nt" and shutil.which("bash"):
        candidates.append(Path(shutil.which("bash")))
    found = next((str(p) for p in candidates if p.is_file()), None)
    if not found:
        pytest.skip("Git Bash or POSIX bash is required for shell guard tests")
    return found


@pytest.fixture
def candidate_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Publication fixture")
    git("config", "user.email", "fixture@example.invalid")
    commits = []
    for label in ("base", "reviewed", "unreviewed"):
        (tmp_path / "source.txt").write_text(label, encoding="utf-8")
        git("add", "source.txt")
        git("commit", "-qm", label)
        commits.append(git("rev-parse", "HEAD"))
    git("update-ref", "refs/remotes/origin/main", commits[1])
    git("tag", "v1.0.0", commits[0])
    return tmp_path, commits


def resolve(bash, folder, target, version="v1.0.0"):
    output = folder / "github-output.txt"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", release_script("Resolve release request")],
        cwd=folder,
        env={**os.environ, "DISPATCH_VERSION": version, "DISPATCH_TARGET": target,
             "DISPATCH_PRERELEASE": "true", "GITHUB_OUTPUT": output.as_posix()},
        text=True, capture_output=True, timeout=20,
    )
    return result, output.read_text(encoding="utf-8") if output.exists() else ""


def test_publication_requires_manual_main_dispatch_and_private_local_runner():
    release = workflow("release.yml")
    validation = workflow("antigravity-local-validation.yml")
    for item in (release, validation):
        assert set(item["on"]) == {"workflow_dispatch"}
        job = next(iter(item["jobs"].values()))
        assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "github.event.repository.private == true" in validation["jobs"]["validate"]["if"]
    assert release["jobs"]["publish"]["runs-on"] == "ubuntu-latest"
    assert "pull-requests" not in release["permissions"]
    ci = workflow("tests.yml")
    assert ci["permissions"] == {"contents": "read"}
    assert ci["jobs"]["test"]["runs-on"] == "windows-latest"


def test_existing_release_tag_accepts_identical_commit(bash, candidate_repo):
    folder, commits = candidate_repo
    result, output = resolve(bash, folder, commits[0])
    assert result.returncode == 0, result.stderr
    assert f"target_sha={commits[0]}" in output


def test_existing_release_tag_cannot_be_retargeted(bash, candidate_repo):
    folder, commits = candidate_repo
    result, output = resolve(bash, folder, commits[1])
    assert result.returncode != 0
    assert "choose a new version" in result.stderr
    assert not output


def test_unreviewed_commit_cannot_be_released(bash, candidate_repo):
    folder, commits = candidate_repo
    result, output = resolve(bash, folder, commits[2], "v1.0.1")
    assert result.returncode != 0
    assert not output


@pytest.mark.parametrize("target", ["missing-ref", "--help"])
def test_invalid_target_is_not_interpreted_as_git_option(bash, candidate_repo, target):
    folder, _ = candidate_repo
    result, output = resolve(bash, folder, target, "v1.0.1")
    assert result.returncode != 0
    assert not output


@pytest.mark.parametrize("missing_checksum", [False, True])
def test_publication_retry_only_uploads_missing_assets(bash, tmp_path, missing_checksum):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.txt"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [[ "$1 $2" == "release view" && "$*" == *--json* ]]; then\n'
        '  printf "%s\\n" "szlcsc-mcp-v1.0.0.zip"\n'
        '  if [[ "$MISSING_CHECKSUM" == "false" ]]; then printf "%s\\n" "szlcsc-mcp-v1.0.0.zip.sha256"; fi\n'
        'fi\n', encoding="utf-8", newline="\n",
    )
    fake_gh.chmod(0o755)
    env = {**os.environ, "FAKE_GH": fake_gh.as_posix(), "CALL_LOG": log.as_posix(),
           "MISSING_CHECKSUM": str(missing_checksum).lower(), "VERSION": "v1.0.0",
           "TARGET_SHA": "a" * 40, "PRERELEASE": "true",
           "ARCHIVE": "szlcsc-mcp-v1.0.0.zip", "CHECKSUM": "szlcsc-mcp-v1.0.0.zip.sha256"}
    result = subprocess.run(
        # A shell function intercepts every gh call; never fall through to an
        # authenticated real CLI because of Windows/POSIX PATH conversion.
        [bash, "--noprofile", "--norc", "-c", 'gh() { "$BASH" "$FAKE_GH" "$@"; }\n' + release_script("Create or promote GitHub Release")],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    uploads = [line for line in calls if line.startswith("release upload ")]
    assert uploads == (["release upload v1.0.0 szlcsc-mcp-v1.0.0.zip.sha256"] if missing_checksum else [])
    assert all("--clobber" not in line for line in calls)
