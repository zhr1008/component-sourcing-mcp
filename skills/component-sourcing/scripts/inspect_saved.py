"""Summarize a saved Altium project, schematic or PCB without opening Altium."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys


def inspect(path: Path, *, designator: str | None, limit: int) -> dict:
    result = {"source": "saved_file", "file": path.name}
    kind = path.suffix.lower()
    if kind == ".prjpcb":
        from altium_monkey.altium_prjpcb import AltiumPrjPcb

        documents = AltiumPrjPcb(path).documents
        return {**result, "document_count": len(documents),
                "documents": [doc["path"] for doc in documents[:limit]],
                "truncated": len(documents) > limit}

    if kind == ".schdoc":
        from altium_monkey import AltiumSchDoc

        sheet = AltiumSchDoc(path)
        components = sheet.get_components()
        result.update(wire_count=len(sheet.wires), port_count=len(sheet.ports))
    else:
        from altium_monkey import AltiumPcbDoc

        board = AltiumPcbDoc.from_file(path)
        components = board.components
        result.update(net_count=len(board.nets), track_count=len(board.tracks), pad_count=len(board.pads))

    selected = components if designator is None else [
        comp for comp in components if comp.designator.casefold() == designator.casefold()
    ]
    rows = []
    for comp in selected[:limit]:
        row = {"designator": comp.designator, "footprint": comp.footprint}
        if kind == ".schdoc":
            row.update(comment=comp.comment, pin_count=len(comp.pins))
        else:
            row.update(layer=comp.layer, x=comp.x, y=comp.y, rotation=comp.rotation)
        rows.append(row)
    return {**result, "component_count": len(components), "matched_count": len(selected),
            "components": rows, "truncated": len(selected) > limit}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--designator", help="Exact component designator in a SchDoc or PcbDoc")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if not args.file.is_file() or args.file.suffix.lower() not in {".prjpcb", ".schdoc", ".pcbdoc"}:
        parser.error("需要指定一个已保存的 .PrjPcb、.SchDoc 或 .PcbDoc 文件")
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    if args.designator and args.file.suffix.lower() == ".prjpcb":
        parser.error("按位号检查时请指定具体的 .SchDoc 或 .PcbDoc")
    try:
        with redirect_stdout(sys.stderr):
            result = inspect(args.file, designator=args.designator, limit=args.limit)
    except ImportError as exc:
        print(f"解析依赖不可用。请用装有 altium-monkey 的 Python 3.12 环境运行。{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"{args.file.name} 解析失败 ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
