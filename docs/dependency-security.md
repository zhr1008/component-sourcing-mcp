# Dependency security record

Review date: 2026-10-24. Next review: 2026-11-07, or on an upstream Altium update.

The default installation excludes Altium parsing/writing dependencies. Install
the `[altium]` extra explicitly only after reviewing the constraints below.
It requires Python 3.12+; the sourcing core supports Python 3.11+.

`pip-audit 2.10.1` against the initial full environment reported 49 advisory
records in three packages (the feed includes duplicate aliases): lxml 6.0.2,
Pillow 12.3.0 and the virtual environment's pip 25.0.1. Pip is an installation
tool, not a project runtime requirement; the validation environment was upgraded
to patched pip 26.2.2. Users should upgrade pip before installing this project.

A separate, fresh default sourcing environment was installed and audited after
this change: `pip-audit` reported **no known vulnerabilities**. Altium, Pillow
and lxml were confirmed absent. The unpublished project package is not indexed
by PyPI's vulnerability service; source tests and review cover it separately.

| Dependency | Constraint and risk | Resolution status |
|---|---|---|
| lxml 6.0.2 | `altium-monkey==2026.9.22` pins this version; affected by default `iterparse`/`ETCompatXMLParser` entity resolution allowing local-file reads | Upstream fix is 6.1.0+; optional Altium environment remains affected |
| Pillow 12.3.0 | The same Altium package pins this version; advisories include image decoder memory-safety and denial-of-service issues | Upstream fix is 13.0.0; optional Altium environment remains affected |
| pip 25.0.1 | Multiple installation/archive-handling advisories | Validation tooling upgraded to 26.2.2; project installation guides now upgrade pip |

Sources: [Altium package metadata](https://pypi.org/pypi/altium-monkey/2026.9.22/json),
[lxml advisory](https://github.com/lxml/lxml/security/advisories/GHSA-vfmq-68hx-4jfw),
[Pillow advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-78g4-xqwv-q3g8).

The available Altium release has exact pins that prevent a normal resolver from
using the patched versions. Overriding them with `--no-deps` would violate its
dependency contract and has not been validated. No upstream package was patched
or repackaged by this repository.

The sourcing core does not parse images or XML with Pillow/lxml. Its default
dependency graph has no Altium/Pillow/lxml packages. Altium tools remain
discoverable and report parser availability; actual parsing requires the extra.
Previously installed packages are not removed merely by upgrading the project;
use a fresh virtual environment for the default installation.

For the optional CAD environment, accept only trusted library/image fixtures,
use a separate low-privilege account and retain backups. These are exposure
limits, not vulnerability fixes or a filesystem sandbox. Full Altium development
tests use trusted disposable generated fixtures. Stable security acceptance of
the optional integration remains open until compatible patched dependencies or
a validated replacement can be used. The release remains a pre-release.
