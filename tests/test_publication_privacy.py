import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


path = Path(__file__).resolve().parents[1] / "scripts/check_publication_privacy.py"
spec = importlib.util.spec_from_file_location("privacy_guard", path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_personal_metadata_is_rejected_without_echoing_the_address():
    record = b"author Fixture <private@example.invalid> 1 +0000\n"
    rules = guard.inspect_object("commit", record)
    assert "commit-email-must-use-github-noreply" in rules
    assert "private@example.invalid" not in json.dumps(rules)


def test_github_noreply_author_and_system_committer_are_accepted():
    record = b"author Fixture <1+fixture@users.noreply.github.com> 1 +0000\ncommitter GitHub <noreply@github.com> 1 +0000\n"
    assert guard.inspect_object("commit", record) == []


def test_escaped_private_home_paths_are_detected():
    for separator in ("\\", "\\\\", "/"):
        value = separator.join(("C:", "Users", "PrivateFixture", "library.json"))
        assert "personal-path" in guard.inspect_object("blob", value.encode())


def test_portable_paths_and_hosted_runner_home_are_accepted():
    for value in ("C:/src/project", "D:/AltiumFixtures/legacy", "C:/Users/runneradmin/AppData/Local", "/home/runner/work/project/"):
        assert guard.inspect_object("blob", value.encode()) == []


def test_secret_rule_result_never_includes_the_token():
    token = b"ghp_" + b"a" * 40
    rules = guard.inspect_object("blob", token)
    assert rules == ["github-token"]
    assert token.decode() not in json.dumps(rules)


def test_email_rules_cover_arbitrary_domains_and_utf16():
    address = b"fixture@" + b"private-company.org"
    for record in (address, b"email:" + address, address.decode().encode("utf-16")):
        rules = guard.inspect_object("blob", record)
        assert "personal-email" in rules
        assert address.decode() not in json.dumps(rules)


def test_documented_placeholders_and_ssh_remote_are_accepted():
    for value in (b"fixture@example.invalid", b"help@example.com", b"git@github.com:owner/repo.git"):
        assert guard.inspect_object("blob", value) == []


def test_reviewed_historical_fixture_exception_is_limited_to_exact_bytes():
    current = (path.parents[1] / "tests/test_normalize.py").read_bytes().replace(b"\r\n", b"\n")
    old = current.replace(
        b'"https://user:secret@" + "alimg.szlcsc.com/a.jpg"',
        b'"https://user:secret@' + b'alimg.szlcsc.com/a.jpg"',
    )
    assert old != current
    assert "personal-email" not in guard.inspect_object("blob", old)
    assert "personal-email" in guard.inspect_object("blob", old + b"\n")


def test_sensitive_paths_are_detected_even_with_shared_blob_contents(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "1+fixture@users.noreply.github.com")
    # Identical bytes reproduce the rev-list representative-path blind spot.
    for name in ("README.md", ".env.production", "local.bundle"):
        (tmp_path / name).write_text("same bytes\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    report = guard.audit_repository(tmp_path)
    rules = {r["rule"] for r in report["findings"]}
    assert "sensitive-file-must-stay-local" in rules
    assert "archive-must-stay-out-of-source-history" in rules
    assert report["commit_trees_scanned"] == 1
    assert ".env.production" not in json.dumps(report)


def test_sensitive_path_policy_keeps_only_portable_templates():
    for name in (".env", "settings/.netrc", "keys/private.pem", "auth/client_secret_local.json"):
        assert guard.inspect_path(name) == ["sensitive-file-must-stay-local"]
    for name in (".env.example", ".env.template", "src/config.py"):
        assert guard.inspect_path(name) == []


def test_pre_push_blocks_private_metadata_before_upload_and_accepts_safe_history(tmp_path):
    repo = tmp_path / "working"
    remote = tmp_path / "remote.git"
    repo.mkdir()

    def git(*args, check=True):
        return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True)

    git("init", "-q")
    git("init", "--bare", "-q", str(remote))
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    root = path.parents[1]
    (repo / "scripts").mkdir()
    (repo / ".githooks").mkdir()
    shutil.copy2(path, repo / "scripts/check_publication_privacy.py")
    hook = repo / ".githooks/pre-push"
    shutil.copy2(root / ".githooks/pre-push", hook)
    hook.chmod(hook.stat().st_mode | 0o111)
    git("config", "core.hooksPath", ".githooks")
    git("add", ".")
    git("commit", "-qm", "fixture")
    rejected = git("push", str(remote), "HEAD:refs/heads/main", check=False)
    assert rejected.returncode != 0
    assert b"commit-email-must-use-github-noreply" in rejected.stdout
    assert b"fixture@example.invalid" not in rejected.stdout + rejected.stderr
    assert not git("ls-remote", str(remote)).stdout.strip()
    git("config", "user.email", "1+fixture@users.noreply.github.com")
    git("commit", "--amend", "--reset-author", "-qm", "fixture")
    assert git("push", str(remote), "HEAD:refs/heads/main").returncode == 0
    assert b"refs/heads/main" in git("ls-remote", str(remote)).stdout
    tree = git("rev-parse", "HEAD^{tree}").stdout.decode().strip()
    git("config", "user.email", "fixture@example.invalid")
    oid = git("commit-tree", tree, "-m", "unreferenced fixture").stdout.decode().strip()
    rejected = git("push", str(remote), oid + ":refs/heads/raw-object", check=False)
    assert rejected.returncode != 0
    assert b"commit-email-must-use-github-noreply" in rejected.stdout
    assert b"refs/heads/raw-object" not in git("ls-remote", str(remote)).stdout


def test_pre_push_input_handles_deletions_and_rejects_malformed_ids():
    import pytest

    oid = "a" * 40
    assert guard.outgoing_revisions(f"HEAD {oid} refs/heads/main {'0' * 40}\n") == (oid,)
    assert guard.outgoing_revisions(f"(delete) {'0' * 40} refs/heads/main {oid}\n") == ()
    with pytest.raises(ValueError):
        guard.outgoing_revisions("HEAD invalid refs/heads/main invalid")
