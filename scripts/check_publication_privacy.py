"""Check reachable Git objects without printing matched private values.

This is a repository privacy guard, not a complete secret scanner or a way to
inspect/purge GitHub's hidden PR refs and caches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


PATTERNS = {
    "personal-path": re.compile(rb"(?i)(?:[A-Z]:[\\/]+Users[\\/]+(?!Public\b|runneradmin\b)[^\\/\s\"'<>]+|[EF]:[\\/]+(?:save|\xe5\xb7\xa5\xe4\xbd\x9c)[\\/]+|/home/(?!runner\b)[a-z][^/\s]+/)"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "openai-token": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google-key": re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "gitlab-token": re.compile(rb"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    "package-registry-token": re.compile(rb"\b(?:npm_[A-Za-z0-9]{30,}|pypi-[A-Za-z0-9_-]{50,})\b"),
}

EMAIL = re.compile(rb"(?i)(?<![\w])[a-z0-9][a-z0-9_.+\-]*@[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}\b")
# Immutable rc.4 test_normalize.py rejects a fabricated credential URL whose
# authority looks like an email. Accept this reviewed file only byte for byte,
# only for the email rule; every other detector still applies to its contents.
REVIEWED_EMAIL_FIXTURES = {"1f46a644d3b1258f5d97e32b76357ee64048de23a7dd6745063206381a19600c"}


def _public_email(email: bytes) -> bool:
    email = email.lower()
    domain = email.rsplit(b"@", 1)[1]
    return (
        domain == b"users.noreply.github.com"
        or email in {b"noreply@github.com", b"git@github.com"}
        or domain in {b"example.com", b"example.org", b"example.net"}
        or domain.endswith((b".invalid", b".example", b".test"))
    )


def inspect_path(path: str) -> list[str]:
    """Inspect every committed pathname, even when blobs share identical bytes."""
    file = PurePosixPath(path.lower())
    name = file.name
    rules = []
    if path.startswith(".upload/") or path.lower().endswith((".zip", ".whl", ".7z", ".tar", ".tar.gz", ".bundle")):
        rules.append("archive-must-stay-out-of-source-history")
    if (
        (name == ".env" or name.startswith(".env.")) and name not in {".env.example", ".env.template"}
        or name in {".netrc", "_netrc", ".npmrc", ".pypirc", "credentials", "credentials.json", "suppliers.json", "suppliers.local.json", "id_rsa", "id_ed25519", "kubeconfig"}
        or name.startswith("client_secret") and name.endswith(".json")
        or file.suffix in {".pem", ".key", ".p12", ".pfx"}
        or ".ssh" in file.parts
    ):
        rules.append("sensitive-file-must-stay-local")
    return rules


def inspect_object(kind: str, content: bytes) -> list[str]:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        content = content.decode("utf-16", errors="replace").encode("utf-8")
    findings = [name for name, pattern in PATTERNS.items() if pattern.search(content)]
    if (
        any(not _public_email(match.group()) for match in EMAIL.finditer(content))
        and hashlib.sha256(content).hexdigest() not in REVIEWED_EMAIL_FIXTURES
    ):
        findings.append("personal-email")
    if kind in {"commit", "tag"}:
        for email in re.findall(rb"(?m)^(?:author|committer|tagger) [^\n]*<([^>\n]+)>", content):
            if not (email.endswith(b"@users.noreply.github.com") or email == b"noreply@github.com"):
                findings.append("commit-email-must-use-github-noreply")
                break
    return findings


def audit_repository(repo: Path, extra_revisions: tuple[str, ...] = ()) -> dict:
    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60).stdout

    rows = git("rev-list", "--objects", "--all", "--end-of-options", "HEAD", *extra_revisions).decode("utf-8").splitlines()
    payload = "".join(row.split(" ", 1)[0] + "\n" for row in rows).encode()
    objects = git("cat-file", "--batch", input=payload)
    findings = []
    scanned = 0
    trees_scanned = 0
    offset = 0
    for row in rows:
        end = objects.index(b"\n", offset)
        oid, kind, size = objects[offset:end].split()
        start = end + 1
        content = objects[start:start + int(size)]
        offset = start + int(size) + 1
        if kind not in {b"blob", b"commit", b"tag"}:
            continue
        scanned += 1
        rules = inspect_object(kind.decode(), content)
        for rule in rules:
            findings.append({"object": oid.decode(), "kind": kind.decode(), "rule": rule})
        if kind == b"commit":
            # rev-list --objects emits a blob only once, under one representative
            # path. Inspect full trees so a credential filename cannot hide behind
            # a duplicate innocuously named blob.
            paths = git("ls-tree", "-r", "--name-only", "-z", oid.decode()).split(b"\0")
            trees_scanned += 1
            for raw_path in filter(None, paths):
                for rule in inspect_path(raw_path.decode("utf-8", errors="replace")):
                    findings.append({"object": oid.decode(), "kind": "path", "rule": rule})
    return {"objects_scanned": scanned, "commit_trees_scanned": trees_scanned, "findings": findings}


def outgoing_revisions(records: str) -> tuple[str, ...]:
    revisions = set()
    for record in records.splitlines():
        fields = record.split()
        if len(fields) != 4 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[1]):
            raise ValueError("Malformed pre-push input")
        if fields[1].strip("0"):
            revisions.add(fields[1])
    return tuple(sorted(revisions))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-push", action="store_true", help="Also scan the exact outgoing objects supplied by Git on stdin")
    args = parser.parse_args()
    try:
        revisions = outgoing_revisions(sys.stdin.read()) if args.pre_push else ()
    except ValueError:
        print(json.dumps({"findings": [{"rule": "invalid-pre-push-input"}]}))
        raise SystemExit(1)
    result = audit_repository(Path(__file__).resolve().parents[1], revisions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["findings"]))
