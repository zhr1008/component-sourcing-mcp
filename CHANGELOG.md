# Changelog

## Unreleased

- Correct package and electrical constraint matching: package pin counts stay textual, engineering decimals/scientific notation and micro symbols are recognized, and tiny capacitance values no longer compare equal through an oversized absolute tolerance.
- Preserve SI prefixes in shorthand voltage ranges and prefer explicit minimum/maximum limits over a generic range. Ambiguous multi-value strings and nonfinite scalars are not treated as numeric evidence.
- Reapply stock, price, package and brand filters after parametric detail enrichment; expose enrichment failures in candidate warnings. JLCPCB results are also checked against the requested package.
- Check optional Altium parser imports in status and before full imports. Missing parsers return `DEPENDENCY_MISSING` before network lookups, output writes or converter launch.

## 0.7.0-rc.4

Initial public snapshot of Component Sourcing MCP, with a fresh Git history.

- 45 SZLCSC/JLCPCB sourcing, datasheet/CAD, existing Altium-library and managed-library tools.
- stdio, Streamable HTTP and legacy SSE; structured errors, TTL caching and bounded upstream requests.
- Read-only multi-supplier feasibility and architecture notes for Mouser, Huaqiu and DigiKey; those adapters are not implemented in this release.
- GitHub-hosted CI, full-history privacy checks, immutable normal release publishing and an inactive private-maintainer validation template.
- Third-party AGPL dependency disclosure; Python module and legacy CLI/tool names retained for compatibility.
- Altium parsing/writing moved to an explicit `[altium]` extra; known vulnerable upstream lxml/Pillow pins disclosed and excluded from default sourcing installations. Missing parsers provide an installation hint.

This is a pre-release. Full manual Altium/Datasheet manufacturing acceptance is not complete.
