# Contributing

Open a focused issue or pull request with the problem, expected behavior and a
minimal reproduction. For vulnerabilities, follow [SECURITY.md](SECURITY.md).
Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before contributing or
redistributing Altium integrations or upstream assets.

## Local development

Use Python 3.11+; CI currently uses Windows and Python 3.12. In a virtual
environment, run from the repository root:

```text
python -m pip install --upgrade pip
python -m pip install -e ".[dev,altium]"
python -X utf8 -m pytest -q
python -X utf8 scripts/check_publication_privacy.py
ruff check --isolated --select E9,F63,F7,F82 src tests scripts/check_publication_privacy.py scripts/verify_mcp.py scripts/verify_live.py
python -X utf8 scripts/verify_mcp.py --all-transports
```

Unit and mock tests and protocol checks do not require upstream credentials or
real libraries. Use mock responses for failure/retry cases; live upstream checks
are optional and may report access restrictions. Do not bypass upstream controls.
Full tests require the Altium extra and its known-risk dependencies; see
[dependency-security.md](docs/dependency-security.md). Run them on disposable
trusted fixtures, separately from the default sourcing environment.

Test write operations with disposable fixtures under a dedicated output root.
Do not include a contributor's `.codex/config.toml`, `.env`, cache database,
downloaded datasheets, private libraries or unsanitized logs in a PR. Examples
should use portable placeholder paths. Retain original test evidence privately
when publishing a redacted copy and label any redaction.

Use a GitHub noreply address for commit author/committer metadata. Set it in this
repository with `git config --local user.email '<your GitHub noreply address>'`.

For a fresh clone, enable the checked-in pre-push guard with
`git config --local core.hooksPath .githooks` (preserve any existing hook setup
before changing it). The guard runs before Git uploads objects and checks full
history, commit identities, email addresses, token formats and sensitive filenames.
Keep real contacts out of source; use reserved example domains for test fixtures.
The Release workflow also runs this guard before creating archives or tags.

Also enable GitHub's email-privacy setting for web-created commits/merges. This
repository starts from a reviewed source snapshot. Do not merge histories from
private predecessor repositories or publish backup mirrors and original logs.

Before opening a public PR, enable **Keep my email addresses private** in your
GitHub account's [email settings](https://github.com/settings/emails). Also enable
**Block command line pushes that expose my email**. Local Git author settings
do not control all GitHub-generated test-merge/web-merge
metadata. If this privacy behavior has not been checked for your account, first
rehearse in a private repository and inspect the generated commit metadata
without posting the original values. CI can detect such metadata after creation;
it cannot prevent the first public exposure. A failed privacy check must be
contained and investigated before further public review.

Describe the change, relevant checks and known limitations in the PR. Routine
contribution checks do not constitute the full Antigravity/Altium manufacturing
acceptance required for a stable release. That gate is defined in
[RELEASE_ACCEPTANCE.md](docs/test-plans/RELEASE_ACCEPTANCE.md).
