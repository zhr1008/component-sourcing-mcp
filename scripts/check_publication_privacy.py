"""Check reachable Git objects without printing matched private values.

This is a repository privacy guard, not a complete secret scanner or a way to
inspect/purge GitHub's hidden PR refs and caches.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


PATTERNS = {
    "personal-path": re.compile(rb"(?i)(?:[A-Z]:[\\/]+Users[\\/]+(?!Public\b|runneradmin\b)[^\\/\s\"'<>]+|[EF]:[\\/]+(?:save|\xe5\xb7\xa5\xe4\xbd\x9c)[\\/]+|/home/(?!runner\b)[a-z][^/\s]+/)"),
    "personal-email": re.compile(rb"(?i)\b[\w.+-]+@(?:qq\.com|gmail\.com|outlook\.com|hotmail\.com|163\.com|126\.com)\b"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "openai-token": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google-key": re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}


def inspect_object(kind: str, content: bytes) -> list[str]:
    findings = [name for name, pattern in PATTERNS.items() if pattern.search(content)]
    if kind in {"commit", "tag"}:
        for email in re.findall(rb"(?m)^(?:author|committer|tagger) [^\n]*<([^>\n]+)>", content):
            if not (email.endswith(b"@users.noreply.github.com") or email == b"noreply@github.com"):
                findings.append("commit-email-must-use-github-noreply")
                break
    return findings


def audit_repository(repo: Path) -> dict:
    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60).stdout

    rows = git("rev-list", "--objects", "--all", "HEAD").decode("utf-8").splitlines()
    payload = "".join(row.split(" ", 1)[0] + "\n" for row in rows).encode()
    objects = git("cat-file", "--batch", input=payload)
    findings = []
    scanned = 0
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
        path = row.partition(" ")[2]
        if path.startswith(".upload/") or path.lower().endswith((".zip", ".whl", ".7z", ".tar", ".tar.gz")):
            rules.append("archive-must-stay-out-of-source-history")
        for rule in rules:
            findings.append({"object": oid.decode(), "kind": kind.decode(), "rule": rule})
    return {"objects_scanned": scanned, "findings": findings}


if __name__ == "__main__":
    result = audit_repository(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["findings"]))
