# Code Maintenance and Dependency Rules

SZLCSC MCP should grow by extending clear domain boundaries, not by accumulating parallel implementations.

This document defines the maintenance rules used from v0.6.1 onward.

## 1. General rules

1. Prefer a small, reusable internal helper over repeating `_now`, normalization, path and selection logic in multiple modules.
2. Do not create a second implementation of an existing behavior merely to add one field. Extend or replace the canonical path deliberately.
3. Do not delete an older path until callers and compatibility requirements have been checked. Refactors should be staged when binary Altium I/O is involved.
4. No automatic cleanup may delete user CAD assets, Datasheets or legacy libraries unless a future tool explicitly requires confirmation and provides a recovery path.
5. Persist metadata only after the corresponding file operation succeeds. The index/catalog must never claim an on-disk change that failed to save.
6. Avoid reparsing binary SchLib/PcbLib files when a file fingerprint proves they are unchanged.
7. Avoid a second parser pass unless the second pass has a concrete need that cannot be satisfied by the first representation.
8. Network metadata refresh and local CAD parsing have independent lifecycles.
9. Datasheet verification state is invalidated by identity/package changes, not by ordinary price/stock changes.
10. Server modules register MCP tools and translate exceptions; domain logic belongs in domain modules.

## 2. Altium catalog dependency direction

Preferred dependency direction:

```text
ad_utils
   ↓
ad_catalog          # canonical catalog persistence + base Altium parsing
   ↓
ad_scan             # resilience + link refinement; no network
   ↓
ad_enrichment       # SZLCSC/network metadata + Datasheet evidence workflow
   ↓
ad_properties       # safe SchLib property planning/writeback
ad_export            # offline catalog presentation/export
   ↓
ad_refresh           # orchestration of scan + enrichment freshness
   ↓
server_ad            # MCP boundary only
```

Rules:

- `ad_catalog` must not import `ad_scan`, `ad_enrichment`, `ad_properties`, `ad_export`, `ad_refresh` or `server_ad`.
- `ad_scan` may build on `ad_catalog` but must remain offline.
- `ad_enrichment` may use the catalog and SZLCSC client/downloader but must not write SchLib graphics.
- `ad_properties` may write SchLib parameters only after a plan/backup boundary.
- `ad_export` remains offline and read-only with respect to catalog contents.
- `ad_refresh` orchestrates existing operations instead of reimplementing their internals.
- `server_ad` should not become a second business-logic layer.

## 3. Canonical shared helpers

`src/szlcsc_mcp/ad_utils.py` is the shared home for generic Altium-catalog helpers such as:

- UTC timestamps;
- normalized text comparisons;
- resolved local paths;
- selected-ID filtering;
- safe iterable/object-attribute adapters used against `altium-monkey` object models.

Do not add a private copy of one of these helpers to a new AD module unless the semantics intentionally differ.

## 4. Scan performance rules

The normal incremental scan path should be:

```text
fingerprint files
   ↓
unchanged file? -> reuse catalog records
changed file?   -> parse once
   ↓
resolve footprint names from current catalog
   ↓
only if duplicate footprint names exist:
    inspect SchLib DATAFILE hints for those ambiguous names
   ↓
only if recovery/hints changed catalog data:
    perform second catalog save
```

A no-error scan with no ambiguous footprint names should not require a second SchLib parse or a second catalog revision.

## 5. Write consistency rules

For a SchLib property write:

```text
build plan
  ↓
backup file
  ↓
load library into memory
  ↓
apply intended parameter mutations in memory
  ↓
library.save(path)
  ↓ success only
record applied actions in catalog
```

If `library.save()` fails, `applied_action_count` must not include the unsaved changes and catalog metadata must remain unchanged for those actions.

## 6. Compatibility and dead-code cleanup

When a newer implementation supersedes an older helper:

1. search repository callers;
2. check whether the old symbol is a documented/importable compatibility surface;
3. either remove it or convert it into a thin compatibility delegate;
4. add a regression proving there is one canonical behavior;
5. avoid a large unrelated rewrite merely to remove a few lines from a binary-I/O module.

This favors deliberate staged cleanup over risky whole-file rewrites.

Known cleanup work after v0.6.1 should be handled incrementally rather than by a single broad rewrite, especially helpers embedded in the original `ad_catalog.py` and `ad_enrichment.py` implementations.

## 7. Automation boundary

Acceptance automation is infrastructure, not MCP domain logic.

Keep it under:

```text
scripts/
.github/workflows/
docs/test-plans/
docs/test-reports/
```

Antigravity validates a fixed candidate. It must not become an automatic implementation/fix agent in the release workflow. Any source edits made during validation are discarded before the report is published.
