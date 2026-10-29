# Selection recall and procurement evidence

This review reproduces public product/model cases from actual use. Private
engineering requirements, project records and conversation transcripts are excluded.

| Defect or limitation | Cause | Change |
|---|---|---|
| Package/pin variants omitted in a narrow comparison | Only a few suffix-specific queries were considered; the tools did not expose a discovery workflow | Add bounded requirement/family discovery, package diversity and explicit coverage; preserve exact identity |
| Small output limits hide relevant candidates | The requested output count was also the upstream retrieval size | Inspect a stable upstream page before ranking/filtering; report raw and inspected counts |
| Pagination stops early | The live upstream caps a page at 30 despite a larger requested size | Use 30-row pages and upstream totals; discovery follows its configured page budget |
| `AEC-Q100` becomes a missing MPN anchor | Alphanumeric tokens were treated as manufacturer models | Recognize qualification/standard tokens; retain real model anchors such as ISO1042; keep qualifications unknown pending manufacturer evidence |
| Relevant candidates excluded by old stock/price | Commercial filters were sent to a stale legacy endpoint before detail verification | Retrieve first, verify current details, then apply explicit filters; discovery defaults to no stock/price exclusion |
| Fresh requests still return conflicting stock, MOQ and price | Freshness described HTTP retrieval; legacy procurement took precedence over the current product page | Validate C-code, MPN and product ID; replace the entire commercial snapshot with own-product page evidence |
| Wrong Datasheet | The first PDF could be a site compliance certificate or a recommended product's document | Read the exact product's `pdf_property` attachment and reject generic PDF anchors |
| Duplicate current-page fetches | Concurrent cache misses did not recheck after waiting | Share the validated snapshot under the client fetch lock |
| Skipped window remainder | Advancing the upstream page after a small output limit skipped other candidates | Add an opt-in within-window cursor; map supplier pagination across complete 30-row windows |
| Later-page exact matches ignored | A second page was probed for existence without adding its candidates | Inspect up to three candidate pages; expose unresolved incomplete windows distinctly |
| Partial quantity price falsely certified | Incomplete or invalid rows could survive or be silently dropped during normalization | Require a complete normalized set of quantity/price rows before certifying the ladder |
| Missing current-page address retains legacy data | Page refresh was conditional on a URL being present | Withhold commercial fields with PRODUCT_PAGE_MISSING; preserve unknown values |

Live reproduction on 2026-10-29: public product `C2671569`, `ISO1042QDWRQ1`,
SOIC-16-300mil had current public stock 2560, MOQ 10 and advertised CNY price
12.375. The legacy path returned zero stock, MOQ 1 and older prices. The repaired
path returned the current stock/MOQ and the manufacturer's ISO1042-Q1 Datasheet.
The page's advertised price does not itself state a quantity interval, so the
repaired quantity quote remains unknown unless explicit quantity tiers exist.

A live `AEC-Q100 isolated CAN` discovery returned both 8-pin and 16-pin ISO1042
variants, including `ISO1042QDWRQ1`. Qualification status remained unknown. The
retrieval explicitly reported uninspected candidates and did not claim to cover
every market listing.

Exact misses for other full ordering codes do not prove a catalog bug or market
absence. For example, `CW1573` can discover the listed `CW1573DLCS`, but exact
lookup must never silently substitute that suffix. Offline regressions cover this
identity boundary, later-page candidates, access-limit stopping, partial errors,
missing commercial evidence, attachment scope, cache refresh and MCP validation.

Current page snapshots have a five-minute TTL and cache only necessary own-product
fields. Refresh bypasses the cache. They exclude recommendations, resource/session
state and tracking metadata. Freshness timestamps describe fetched snapshots, not
a guarantee that an upstream advertised price is a binding quantity quote.
