"""Validate and link libraries just generated for one component.

Only conversion output is edited here. Existing user libraries are never passed
to this module by the scan/enrichment/property workflows.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


class LibraryLinkError(RuntimeError):
    pass


def load_altium_parser():
    try:
        from altium_monkey import AltiumPcbLib, AltiumSchLib
    except (ImportError, OSError) as exc:
        raise LibraryLinkError(
            "Altium parsing requires component-sourcing-mcp[altium]; "
            "review docs/dependency-security.md before installing the optional extra"
        ) from exc
    return AltiumPcbLib, AltiumSchLib


def _pcb_models(symbol):
    return [model for model in symbol.implementations
            if str(model.model_type).casefold() == "pcblib"]


def link_generated_libraries(schlibs: list[str], pcblibs: list[str]) -> dict:
    AltiumPcbLib, AltiumSchLib = load_altium_parser()

    if not schlibs or not pcblibs:
        raise LibraryLinkError("conversion did not produce both SchLib and PcbLib")
    try:
        footprints = [(fp.name, Path(path)) for path in pcblibs
                      for fp in AltiumPcbLib(path).footprints]
        if not footprints:
            raise LibraryLinkError("generated PcbLib has no footprint")
        rows = []
        for raw_path in schlibs:
            path = Path(raw_path)
            library = AltiumSchLib(path)
            symbols = list(library.symbols)
            if not symbols:
                raise LibraryLinkError("generated SchLib has no symbol")
            pending, expectations = [], []
            for symbol in symbols:
                existing = _pcb_models(symbol)
                names = {str(model.model_name).casefold() for model in existing}
                if names:
                    candidates = [(name, pcb) for name, pcb in footprints if name.casefold() in names]
                    if len(candidates) != len(names):
                        raise LibraryLinkError(f"generated symbol has an unresolved/ambiguous footprint: {symbol.name}")
                else:
                    candidates = [(name, pcb) for name, pcb in footprints if name.casefold() == symbol.name.casefold()]
                    if not candidates and len(footprints) == 1:
                        candidates = footprints
                    if len(candidates) != 1:
                        raise LibraryLinkError(f"cannot uniquely link generated symbol: {symbol.name}")
                    name, pcb = candidates[0]
                    symbol.add_footprint(name, library_name=pcb.name)
                    pending.append(symbol.name)
                for name, pcb in candidates:
                    expectations.append((symbol.name, name))
                    rows.append({"symbol": symbol.name, "schlib": str(path),
                                 "footprint": name, "pcblib": str(pcb),
                                 "created": symbol.name in pending})

            if pending:
                # Validate the new binary before replacing the original output.
                with tempfile.TemporaryDirectory(prefix=".szlcsc-links-", dir=path.parent) as tmp:
                    staged = Path(tmp) / path.name
                    library.save(staged)
                    _verify_links(AltiumSchLib(staged), expectations)
                    staged.replace(path)
            _verify_links(AltiumSchLib(path), expectations)
        return {"ok": True, "verified_from_saved_libraries": True,
                "linked_symbols": len({(x["schlib"], x["symbol"]) for x in rows}),
                "created_count": sum(x["created"] for x in rows), "links": rows}
    except LibraryLinkError:
        raise
    except Exception as exc:
        raise LibraryLinkError(f"generated Altium library validation failed: {type(exc).__name__}: {exc}") from exc


def _verify_links(library, expectations):
    for symbol_name, footprint_name in expectations:
        symbol = library.get_symbol(symbol_name)
        if symbol is None or not any(str(model.model_name).casefold() == footprint_name.casefold()
                                     for model in _pcb_models(symbol)):
            raise LibraryLinkError(f"saved symbol lost its footprint implementation: {symbol_name}")


def write_link_report(case_dir: Path, report: dict) -> None:
    path = case_dir / "szlcsc-footprint-links.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
