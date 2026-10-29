# Grounded improvement decisions

The subject was reliability of the current Component Sourcing MCP implementation.
The selected work addressed concrete observed behavior rather than a new product
direction. Its four axes were engineering evidence, catalog recovery, local data
integrity and procurement semantics.

| Priority | Candidate | Decision and reason |
|---|---|---|
| 1 | Invalidate stale verification and require identity/package evidence | Implemented: an old pass could survive changed CAD or a changed Datasheet source |
| 2 | Preserve assets during migration and respect targeted repair scope | Implemented: local reproductions showed missing references being dropped and unreported changes |
| 3 | Recover failed scans and respect explicit library identities | Implemented: parser failures became apparent success and name collisions resolved incorrectly |
| 4 | Preserve unknown limits/stock and allow safe exact MPN filters | Implemented: unknown evidence was accepted as a specific bound or stock state |
| 5 | Expose complete sync progress, fresh data and safe spreadsheet text | Implemented: bounded batches hid remaining work and text cells could become formulas |
| 6 | Replace architecture, vendor private protocols or broad compatibility layers | Rejected: no verified need or protocol, and the changes would exceed the evidence |
| 7 | Override Altium dependency pins or silently claim a fixed environment | Rejected: a compatible upstream package is unavailable; keep the limitation explicit |
| 8 | Merge every similar parser or remove safety checks for shorter code | Rejected: strictness and completeness semantics differ; no proven equivalence |

The implementation, regressions and remaining limits are in
[the repair record](../audits/comprehensive-2026-10-29.md).
