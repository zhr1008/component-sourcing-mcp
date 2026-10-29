# Comprehensive correctness review — 2026-10-29

Baseline: `5cb0e62` / v0.8.0-rc.1. Fix release: v0.8.0-rc.2.
The review covered sourcing, HTTP/cache boundaries, tool schemas and runtime,
local library operations, Altium catalog/evidence workflows, exports, installation
and publication. Findings were reproduced with owning tests, disposable files or
bounded public upstream calls before repair. Private libraries and API credentials
were not used.

## Applied repairs

| Trigger / defect | Corrected behavior | Regression ownership |
|---|---|---|
| Changed CAD retained an earlier verification pass | Changes to pins, bindings, resolved links or linked footprint evidence make the verification stale; equivalent rescans retain history | `test_ad_catalog_v060.py` |
| Failed parser rows were reused as successful scans | Only successful records can reuse a fingerprint; failed/stale records retry until recovery | `test_ad_catalog_v060.py` |
| Same-named footprints resolved despite conflicting library hints | Explicit normalized file/library identity restricts candidates, including DATAFILE hints | `test_ad_catalog_v060.py` |
| Domestic exact MPN ambiguity was raised before filters | Bounded exact candidates allow manufacturer/package disambiguation, with incomplete windows still rejected | `test_multi_supplier.py` |
| A real end-of-pagination response used a null product list | A null page is empty only when its reported total proves it is past the end | `test_multi_supplier.py` plus a live filtered lookup |
| Offline unknown stock appeared as out of stock | Explicit stock filters preserve the unknown state | `test_library_management.py` |
| Targeted repair changed unrelated records without reporting them | Only selected CAD groups reconcile; linked members are reported and unrelated records are preserved | `test_library_management.py` |
| A nominal input voltage proved an unprovided minimum/maximum | Alias matching no longer treats a shorter generic label as a more specific limit; ranges and explicit limits remain usable | `test_parametric.py` |
| An unrelated surviving asset excused other missing migration references | Each missing reference needs a provable opposite-view mirror; otherwise migration blocks before writes | `test_library_migration_v053.py` |
| Datasheet evidence omitted or contradicted MPN/package identity | Full verification requires matching resolved and stated MPN/package; incomplete evidence stays incomplete | `test_ad_enrichment_v060.py` |
| Changed Datasheet URL retained an old file and verification | Source changes invalidate the path and verification, preserve history, and require acquisition from the new source | `test_ad_enrichment_v060.py` |
| An existing invalid PDF was accepted without inspection | Reuse requires a regular file and PDF signature; invalid content is reacquired and failed refresh preserves valid existing data | `test_downloader.py` |
| Spreadsheet exports emitted untrusted formula text | All three CSV writers escape formula-like text through one helper; JSON and native numeric values retain their data | Export tests in `test_library_maintenance_v052.py` and `test_ad_export_v060.py` |
| Sync/preview silently stopped after target 500 | Both tools expose bounded offset/limit pagination and remaining-work metadata | `test_sync.py`, `test_server_payloads.py` |
| Sync/preview reused TTL-cached stock and prices | Both request fresh metadata, while preview still leaves the index unchanged | `test_sync.py`, `test_server_payloads.py` |
| Installers used the caller's directory; Windows native failures still printed success | Installers target their own project and exit when required commands fail | `test_install_scripts.py` |
| Release publication accepted a target with no passing CI | Publication requires successful push CI from `tests.yml` for the exact target SHA | `test_publication_safety.py` |

## Review and simplification decisions

`ce-ideate` prioritized false passes and data loss over cosmetic work.
`ce-code-review` supplied source-backed findings and separate review slices;
`ce-debug` guided failing regressions and minimal fixes. The `ce-simplify-code`
pass preserves all proof tests and trust-boundary checks. The shared CSV cell
helper removes a repeated security defect across three writers. Similar-looking
domestic parsers were kept separate because their malformed-data handling differs.
Three evidence-pass checks now share one immutable status set. Repair snapshots
copy only entry dictionaries because the inspected reconciliation helpers replace
top-level fields; this preserves unrelated records without recursively copying
their nested metadata. CAD parser deep copies remain to retain evidence isolation.
The installer proof test was strengthened with a native invocation trace so a
later failure cannot hide a missed early exit. No proof test was removed.

The review did not establish a complete line-by-line proof of all tracked files.
No independent cross-provider model review was available; no such review is
claimed. These limits do not substitute for the recorded process tests and
behavioral regressions.

## Scope limits and rejected changes

- Multiple server processes sharing one writable library root remain outside the
  supported topology documented in `SECURITY.md`. This round adds no cross-process
  transaction protocol and does not claim to solve that unsupported race.
- The optional Altium dependency still pins vulnerable lxml/Pillow versions and
  has no compatible upstream update. The default sourcing environment's fresh
  dependency audit found no known advisories. See `docs/dependency-security.md`.
- Mouser/DigiKey authentication needs approved user credentials; contract mocks
  and missing-credential handling do not establish live account connectivity.
  Huaqiu remains explicitly unsupported pending a verified API contract.
- PDF checks establish file/signature validity, not full document parsing or
  manual engineering acceptance. CAD tests use generated or synthetic fixtures.
- One full local run encountered a Windows file-replacement `PermissionError`.
  Five isolated reruns and the final full suite passed. No speculative retry or
  permission change was introduced without a repeatable cause.

Final automated results and reproduction commands are recorded in
[the rc.2 validation report](../test-reports/v0.8.0-rc.2-automated.md).
