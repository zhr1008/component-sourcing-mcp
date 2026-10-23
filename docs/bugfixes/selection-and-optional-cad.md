# Selection and optional Altium bug fixes

These fixes follow the first public v0.7.0-rc.4 snapshot. The branch/PR commit
identifies the candidate; they do not change the already published release tag.
No external provider endpoints or dependency versions are changed.

| Reproduction | Root cause | Result after correction |
|---|---|---|
| `SOT-23-5` satisfied `SOT-23-3` | Numeric search extracted `-23` from both package strings | Package constraints use text with token boundaries; a family such as `SOT-23` still matches `SOT-23-5` |
| `.5V` parsed as `5V`; `1e-3V` parsed as `1` | Scalar regex found only a numeric substring | Full scalar parsing supports leading decimals, exponents and both micro characters; malformed/nonfinite scalars are rejected |
| `1pF` satisfied `=2pF` or `<=0F` | Absolute comparison tolerance was at least `1e-12` in base units | Comparisons use relative precision without an absolute floor that overwhelms pico-scale quantities |
| `1~5mV` produced bounds `1V` and `5mV` | Missing bound inherited the unit after its SI prefix had been discarded | A bare bound inherits prefix and unit; explicit prefixes are retained without double scaling |
| Explicit minimum voltage `2V` was replaced by generic `3V~7V` | Range fallback ran even when an explicit scalar limit existed | Explicit limit is retained; only applicable range values are expanded |
| A detailed candidate became unavailable or over budget but remained in results | Stock/price/package/brand filters ran only on the initial search pool | Filters run again on the final candidate; failed detail fetches remain visible in warnings |
| JLCPCB returned `0805` when `0603` was requested | The client trusted the upstream package filter | Wrong or unknown packages are excluded locally when a package is required |
| Full Altium import launched without its parser; status reported ready | Readiness checked converter subcommands or package metadata, and the parser import was deferred until output linking | Status checks actual parser imports; full imports fail with structured `DEPENDENCY_MISSING` before lookup/download/output/converter work |

`parse_number` deliberately accepts complete scalar values. A multi-value field
such as `3.3V/5V` is not converted to its first number. Voltage min/max tools
recognize the supported range syntax separately. Text `==` is exact equality;
package family matching respects token boundaries and is not a pin-compatibility
or manufacturing approval.

Regression coverage includes numeric/parser cases, final-candidate filtering,
mock JLCPCB responses, real binary Altium read/write tests, missing/broken parser
imports and actual MCP calls that verify structured errors and server recovery.
The full suite and real stdio/HTTP/SSE checks run in public CI, including the
default environment without Altium dependencies.

Local validation: 239 tests passed, including 36 added regressions; Ruff syntax
and undefined-name checks passed. Both the full development environment and the
default environment passed the eight real MCP transport groups. A separate
default-environment process also verified all 45 tools, parser readiness,
`DEPENDENCY_MISSING`, untouched conversion output and successful calls after
the error. Public CI attaches its result to the exact PR revision.

The known vulnerabilities in the optional Altium dependency pins remain as
documented in [dependency-security.md](../dependency-security.md). These fixes do
not resolve those upstream dependencies or establish manual Altium/Datasheet
manufacturing acceptance. No live upstream call is needed to reproduce the bugs.
