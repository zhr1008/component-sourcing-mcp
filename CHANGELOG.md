# Changelog

## Unreleased

## 0.8.0-rc.3

- Add bounded requirement discovery with multilingual functional queries, family expansion, deduplicated package/pin variants, partial error reporting and explicit search coverage. Preserve exact lookup identity; a missing listing never proves market absence.
- Inspect a stable 30-row upstream page independently of output limits and use upstream totals for pagination. Do not exclude candidates using stale stock/price before current detail verification; parametric discovery defaults to unspecified inventory.
- Treat AEC/ISO/ASIL requirements as evidence to verify rather than mandatory MPN text; retain actual ISO1042 model anchors and the RS485/PHY relevance guard.
- Replace legacy retail procurement with validated own-product public-page stock/MOQ and quantity-scoped prices. Separate unscoped advertised prices from quantity quotes; withhold legacy tiers when current evidence is unavailable.
- Select the product's Datasheet attachment rather than generic site compliance PDFs; exclude recommendations and session/tracking state from cached page snapshots, and share concurrent cache misses.
- Add usage-driven regressions, MCP schema validation and updated selection guidance.

## 0.8.0-rc.2

- Invalidate stale CAD verification after changed symbol, footprint or source evidence; retry failed scans and respect explicit footprint-library identities.
- Apply domestic MPN manufacturer/package filters before resolving ambiguity, preserve unknown stock, and constrain library repair to selected/shared groups with complete action reporting.
- Block migrations that cannot prove each missing asset has a matching mirror. Strengthen Datasheet identity/package evidence and validate existing PDF files before reuse.
- Expose bounded synchronization pagination and request fresh stock/price metadata. Escape formula-like CSV text across inventory and catalog exports while retaining raw JSON values.
- Make both installers target their own project, stop on native-command failures, and require successful exact-commit push CI before publication.
- Add regression tests and record the comprehensive audit, including remaining optional upstream dependency limitations.

## 0.8.0-rc.1

- Add supplier-neutral component identities, original-currency decimal price breaks, packaging offers and quantity quotes. Keep manufacturer/MPN/package ambiguity, missing inventory, retail/assembly pools and currencies explicit.
- Add `component_providers`, `component_search`, `component_get` and `component_offers`, a supplier guide Resource and comparison Prompt. Existing 45 tools retain their contracts.
- Implement official Mouser Search API v1 and DigiKey Product Information v4 with two-legged OAuth, bounded read retries, per-account persistent budgets and short TTL caches. Keys/tokens are excluded from cache URLs, public errors and normal transport logs.
- Add a private JSON credential-file option, environment overrides, blank configuration template and official application instructions. Huaqiu exposes its application channel and unsupported status pending an approved adapter contract.
- Validate new adapters through official-contract mocks; authenticated Mouser/DigiKey live validation requires each user's own approved credentials.

- Reject private email addresses across domains, UTF-16 sensitive content, registry tokens and credential filenames in the publication guard. Check every commit tree to catch sensitive filenames sharing a blob with harmless files; provide a local pre-push hook and run the guard before release publication.
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
