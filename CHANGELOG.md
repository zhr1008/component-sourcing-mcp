# Changelog

## 0.7.0-rc.4

Initial public snapshot of Component Sourcing MCP, with a fresh Git history.

- 45 SZLCSC/JLCPCB sourcing, datasheet/CAD, existing Altium-library and managed-library tools.
- stdio, Streamable HTTP and legacy SSE; structured errors, TTL caching and bounded upstream requests.
- Read-only multi-supplier feasibility and architecture notes for Mouser, Huaqiu and DigiKey; those adapters are not implemented in this release.
- GitHub-hosted CI, full-history privacy checks, immutable normal release publishing and an inactive private-maintainer validation template.
- Third-party AGPL dependency disclosure; Python module and legacy CLI/tool names retained for compatibility.
- Altium parsing/writing moved to an explicit `[altium]` extra; known vulnerable upstream lxml/Pillow pins disclosed and excluded from default sourcing installations. Missing parsers provide an installation hint.

This is a pre-release. Full manual Altium/Datasheet manufacturing acceptance is not complete.
