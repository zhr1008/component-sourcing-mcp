"""Group BOM records that share the same explicit selection conditions."""

from argparse import ArgumentParser
import csv
import json
from pathlib import Path
import sys


UNKNOWN = {"", "待核实", "待核对", "未知"}


def plan_groups(path, keys, reference=None):
    if len(set(keys)) != len(keys):
        raise ValueError("--key 中有重复列名")
    if path.suffix.lower() != ".csv":
        raise ValueError("输入需要使用 UTF-8 CSV")

    groups = {}
    ungrouped = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.reader(source, strict=True)
        header = next(rows, [])
        selected_columns = keys + ([reference] if reference is not None else [])
        for column in selected_columns:
            if not column or header.count(column) != 1:
                raise ValueError(f"CSV 表头中的所选列必须恰好出现一次，当前列为 {column!r}")
        indices = [header.index(column) for column in keys]
        reference_index = header.index(reference) if reference is not None else None

        for record, row in enumerate(rows, 2):
            if not row:
                row = [""] * len(header)
            if len(row) != len(header):
                raise ValueError(f"CSV 第 {record} 条逻辑记录的列数与表头不一致")

            key = tuple(row[index].strip() for index in indices)
            missing = [column for column, value in zip(keys, key) if value in UNKNOWN]
            if missing:
                item = {"input_row": record, "missing_keys": missing}
                if reference_index is not None:
                    item["reference"] = row[reference_index]
                ungrouped.append(item)
                continue

            if key not in groups:
                groups[key] = {"conditions": dict(zip(keys, key)), "input_rows": []}
                if reference_index is not None:
                    groups[key]["references"] = []
            group = groups[key]
            group["input_rows"].append(record)
            if reference_index is not None:
                group["references"].append(row[reference_index])

    grouped_count = sum(len(group["input_rows"]) for group in groups.values())
    if not grouped_count and not ungrouped:
        raise ValueError("CSV 中需要至少一条数据记录")
    return {
        "status": "partial" if ungrouped else "complete",
        "input_count": grouped_count + len(ungrouped),
        "grouped_count": grouped_count,
        "group_count": len(groups),
        "ungrouped_count": len(ungrouped),
    }, list(groups.values()), ungrouped


def main(argv=None):
    parser = ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("input", type=Path, help="UTF-8 BOM CSV")
    parser.add_argument("--key", action="append", required=True, help="选择一个必须一致的条件列，可重复")
    parser.add_argument("--reference", help="原样保留位号列，可省略")
    parser.add_argument("--output", type=Path, help="写入新的 JSON 文件")
    args = parser.parse_args(argv)

    try:
        summary, groups, ungrouped = plan_groups(args.input, args.key, args.reference)
        report = {**summary, "groups": groups, "ungrouped": ungrouped}
        if args.output is not None:
            if args.output.suffix.lower() != ".json":
                raise ValueError("--output 需要新的 .json 文件")
            document = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(document)
            report = {**summary, "output": str(args.output)}
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return 1 if ungrouped else 0
    except (OSError, ValueError, csv.Error) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
