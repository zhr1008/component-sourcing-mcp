# Third-party software and data

The root [MIT license](LICENSE) describes this repository's original code. It
does not relicense dependencies, upstream CAD assets, datasheets or trademarks.

## Runtime dependencies

The following licenses were checked against the installed package metadata on
2026-10-23. This is a direct-dependency inventory, not a complete transitive SBOM.
Check the exact versions and accompanying license files when redistributing an
environment, container or executable.

| Project | Version checked | License declared by upstream | Integration |
|---|---|---|---|
| [FastMCP](https://github.com/PrefectHQ/fastmcp) | 4.1.1 | Apache-2.0 | MCP runtime |
| [Pydantic](https://github.com/pydantic/pydantic) | 2.14.1 | MIT | Validation |
| [Requests](https://github.com/psf/requests) | 2.34.2 | Apache-2.0 | HTTP client |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | 4.15.0 | MIT | HTML parsing |
| [platformdirs](https://github.com/tox-dev/platformdirs) | 4.13.5 | MIT | Local storage paths |
| [altium-monkey](https://github.com/wavenumber-eng/altium_monkey) | 2026.9.22 | **AGPL-3.0-or-later** | Optional `[altium]` Python extra, imported for Altium parsing and writing |
| [altium-cruncher](https://github.com/wavenumber-eng/altium_cruncher) | 2026.9.7 | **AGPL-3.0-or-later** | Optional, separately installed CLI process |

The Altium projects publish their license texts in
[altium-monkey/LICENSE](https://github.com/wavenumber-eng/altium_monkey/blob/main/LICENSE)
and [altium-cruncher/LICENSE](https://github.com/wavenumber-eng/altium_cruncher/blob/main/LICENSE).
The root MIT notice must not be interpreted as permission to redistribute the
combined Altium-enabled application under MIT alone. The Python integration and
the separate CLI have different integration boundaries; a separate installation
does not, by itself, establish that every distribution or hosted service meets
its license obligations. Maintainers must resolve the intended combined-work
licensing and source-offer requirements before such distribution. This notice
does not change the upstream licenses or replace that review.

This repository does not bundle either project's source or executable in its
source tree or project wheel; package installers retrieve them separately.

## Data sources and engineering assets

SZLCSC/LCSC, JLCPCB, EasyEDA and Altium are third-party names and services. This
project is independent and is not an official service of those organizations.

Product data, datasheets, symbols, footprints, models and downloaded files retain
their upstream rights and applicable terms. Access through a public endpoint is
not a license grant from this repository. Do not publish an archive of downloaded
assets or a user's Altium libraries under the root MIT notice.

Test fixtures contain limited product-response samples for regression checks.
They are historical snapshots rather than current stock, pricing or engineering
approval. Do not add account cookies, tokens, customer orders, private libraries
or unrelated personal information to fixtures or public reports.
