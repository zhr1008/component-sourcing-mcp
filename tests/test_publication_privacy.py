import importlib.util
import json
from pathlib import Path


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
