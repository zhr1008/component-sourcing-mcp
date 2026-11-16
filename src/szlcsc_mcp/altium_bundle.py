"""Build and review a single, standalone Altium component bundle."""

from __future__ import annotations

from collections import Counter
from hashlib import file_digest, sha256
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
import requests

from .altium import require_altium_parser, run_altium_import
from .altium_links import link_generated_libraries, load_altium_parser
from .downloader import _download
from .eda import EasyEDAError
from .errors import SourcingError
from .selection import _ordering_code

REPORT = "verification.json"
GROUPS = ("identity", "pins", "pads", "solder_mask", "paste_mask", "mechanical_layers", "orientation_3d")
BOARD_GROUPS = ("stackup", "board_rules")


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: Literal["identity", "pins", "pads", "solder_mask", "paste_mask", "mechanical_layers",
                   "orientation_3d", "stackup", "board_rules"]
    field: Annotated[str, Field(min_length=1, max_length=180)]
    value: str | Annotated[float, Field(allow_inf_nan=False)] | bool | list[str]
    source: Literal["datasheet", "library_standard", "fabricator", "project_standard"]
    reference: Annotated[str, Field(min_length=1, max_length=500)]
    page: Annotated[int, Field(ge=1)] | None = None
    tolerance: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0


class VisualEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: Annotated[str, Field(min_length=1, max_length=500)]
    page: Annotated[int, Field(ge=1)]
    expected: Annotated[str, Field(min_length=1, max_length=500)]
    observed: Annotated[str, Field(min_length=1, max_length=500)]
    matches: bool


def _digest(path):
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


def _stem(mpn, code):
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", mpn).strip(" .")
    if not clean or len(clean) > 120:
        raise SourcingError("完整订购型号无法用作文件名", code="INVALID_ARGUMENT")
    return f"{clean}_{code}"


def _write_report(folder, data):
    with tempfile.NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False, mode="w", encoding="utf-8") as stream:
        stage = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        except BaseException:
            stream.close()
            stage.unlink(missing_ok=True)
            raise
    try:
        stage.replace(folder / REPORT)
    finally:
        stage.unlink(missing_ok=True)


def _mm(mils):
    return round(float(mils) * 0.0254, 6)


def _observe_libraries(sch_path, pcb_path, step_path):
    AltiumPcbLib, AltiumSchLib = load_altium_parser()
    from altium_monkey.altium_pcb_enums import PcbLayer
    from altium_monkey.altium_pcb_mask_expansion import PcbMaskExpansionMode

    sch = AltiumSchLib(sch_path)
    pcb = AltiumPcbLib(pcb_path)
    symbol, = sch.symbols
    footprint, = pcb.footprints
    step = step_path.read_bytes() if step_path else b""
    bodies = list(footprint.component_bodies)
    step_digest = sha256(step).digest()
    entries = pcb.get_embedded_model_entries()
    payloads = pcb.embedded_model_payloads()
    model_ids = {str(model.id).strip("{}").casefold() for (model, _), payload in zip(entries, payloads)
                 if model.is_embedded and sha256(payload).digest() == step_digest}

    def layer(primitive):
        try:
            return PcbLayer(int(primitive.layer)).name
        except ValueError:
            return str(primitive.layer)

    pin_ids = [str(pin.designator) for pin in symbol.pins]
    pins = {"numbers": sorted(set(pin_ids))}
    for i, pin in enumerate(symbol.pins, 1):
        key = f"pin.{i}[{pin.designator}]"
        pins[f"{key}.name"] = pin.name
        pins[f"{key}.electrical_type"] = pin.electrical_name

    pad_ids = [str(pad.designator or "") for pad in footprint.pads]
    pads = {"numbers": sorted(set(pad_ids) - {""})}
    solder, paste = {}, {}
    for i, pad in enumerate(footprint.pads, 1):
        key = f"pad.{i}[{pad_ids[i - 1]}]"
        pads.update({f"{key}.{name}": value for name, value in {
            "layer": layer(pad), "shape": int(pad.shape), "x_mm": _mm(pad.x_mils),
            "y_mm": _mm(pad.y_mils), "width_mm": _mm(pad.width_mils),
            "height_mm": _mm(pad.height / 10000), "hole_mm": _mm(pad.hole_size_mils),
        }.items()})
        for values, mask in ((solder, "soldermask"), (paste, "pastemask")):
            mode = int(getattr(pad, f"{mask}_expansion_mode"))
            values[f"{key}.mode"] = PcbMaskExpansionMode(mode).name.lower() if mode in (0, 1, 2) else str(mode)
            if mode in (1, 2):
                # Rule-based expansion needs the effective rule for an actual component instance.
                values[f"{key}.expansion_mm"] = _mm(getattr(pad, f"{mask}_expansion_mils")) if mode == 2 else None

    layers = Counter(layer(item) for item in footprint.primitives if hasattr(item, "layer"))
    pose = {}
    for i, body in enumerate(bodies, 1):
        for axis in ("x", "y", "z"):
            pose[f"body.{i}.rotation_{axis}"] = float(getattr(body, f"model_3d_rot{axis}"))
        pose[f"body.{i}.standoff_mm"] = _mm(body.standoff_height_mils)
    content_ok = (step.lstrip().startswith(b"ISO-10303-21;") and step.rstrip().endswith(b"END-ISO-10303-21;")
                  and any(body.model_is_embedded and str(body.model_id).strip("{}").casefold() in model_ids
                          for body in bodies))
    return {
        "pins": pins, "pads": pads, "solder_mask": solder, "paste_mask": paste,
        "mechanical_layers": {"used_layers": sorted(layers)},
        "orientation_3d": pose,
    }, {
        "pin_count": len(pin_ids), "pad_count": len(pad_ids), "primitive_layers": dict(sorted(layers.items())),
        "body_count": len(bodies), "step_matches_embedded": content_ok,
        "embedded_bodies": [{"id": body.model_id, "embedded": body.model_is_embedded} for body in bodies],
        "embedded_model_ids": sorted(model_ids),
    }


def _observe_board(target):
    if target is None:
        return None, {}, None
    path = Path(target).expanduser().resolve()
    if path.suffix.casefold() != ".pcbdoc" or not path.is_file():
        return str(path), {}, "需要一个已保存的 .PcbDoc。先从 .PrjPcb 中选定目标板。"
    try:
        from altium_monkey import AltiumPcbDoc
        from altium_monkey.altium_resolved_layer_stack import resolved_layer_stack_from_pcbdoc

        board = AltiumPcbDoc.from_file(path)
        stack = resolved_layer_stack_from_pcbdoc(board)
        rows = [layer for layer in stack.layers if layer.physical_row and layer.stack_index is not None]
        rows.sort(key=lambda layer: layer.stack_index)
        stackup = {"layer_order": [layer.display_name for layer in rows]} if rows else {}
        for index, layer in enumerate(rows, 1):
            stackup[f"layer.{index}.thickness_mm"] = _mm(layer.thickness_mils)
            stackup[f"layer.{index}.material"] = layer.material
        rules = {f"rule.{i}": [rule.name, rule.rule_kind, str(rule.enabled), str(rule.priority),
                               rule.scope1_expression, rule.scope2_expression]
                 for i, rule in enumerate(board.rules, 1)}
        for i, rule in enumerate(board.rules, 1):
            for key, value in rule.raw_record.items():
                if key not in {"NAME", "RULEKIND", "ENABLED", "PRIORITY", "SCOPE1EXPRESSION",
                               "SCOPE2EXPRESSION", "UNIQUEID"} and isinstance(value, (int, float, str, bool)):
                    rules[f"rule.{i}.{key}"] = value
        return str(path), {"stackup": stackup, "board_rules": rules}, None
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        return str(path), {}, f"离线读取板文件失败（{type(exc).__name__}）"


def _review(report, expected, orientation):
    observations = report["observations"]
    by_field = {}
    for item in expected:
        key = (item.group, item.field)
        if key in by_field or item.field not in observations.get(item.group, {}):
            raise SourcingError(f"证据字段重复或不存在: {item.group}/{item.field}", code="INVALID_ARGUMENT")
        if (not item.reference.strip() or (item.source == "datasheet" and item.page is None)
                or (item.group in {"identity", "pins", "pads"} and item.source != "datasheet")):
            raise SourcingError("原厂核对需要明确的 PDF 章节和页码", code="INVALID_ARGUMENT")
        by_field[key] = item

    groups = {}
    for group in (*GROUPS, *BOARD_GROUPS):
        facts = observations.get(group, {})
        rows = {}
        for field, actual in facts.items():
            item = by_field.get((group, field))
            if item is None or actual is None or actual == []:
                rows[field] = {"status": "pending", "actual": actual}
                continue
            value = item.value
            numeric = type(actual) in (float, int) and type(value) in (float, int)
            match = (math.isfinite(value) and abs(actual - value) <= item.tolerance if numeric
                     else type(actual) is type(value) and actual == value)
            rows[field] = {"status": "pass" if match else "fail", "actual": actual,
                           "evidence": item.model_dump()}
        if group == "orientation_3d":
            rows["pin1_alignment"] = {"status": "pending"} if orientation is None else {
                "status": "pass" if orientation.matches else "fail", "method": "reviewer",
                "evidence": orientation.model_dump(),
            }
        if group == "board_rules" and report["target_board"]:
            rows["native_drc"] = {"status": "pending", "reason": "离线列出已保存规则，AD 原生 DRC 尚待执行"}
        states = {row["status"] for row in rows.values()}
        status = "fail" if "fail" in states else "pass" if states == {"pass"} else "pending"
        if group in BOARD_GROUPS and not report["target_board"]:
            status = "not_requested"
        groups[group] = {"status": status, "checks": rows}
    report["review"] = groups
    states = {group["status"] for group in groups.values()}
    report["review_status"] = "fail" if "fail" in states else "pending" if "pending" in states else "pass"
    return report


def build_bundle(client, eda, code, output_root, target_board=None, datasheet_url=None, timeout=240):
    require_altium_parser()
    part = client.get_part(code, include_procurement=False)
    mpn = _ordering_code(part)
    if part.lcsc_code != code or not mpn:
        raise SourcingError("未核实国内 C 编号对应的完整型号", code="PART_NOT_FOUND")
    stem = _stem(mpn, code)
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / stem
    if final.exists():
        raise SourcingError("同名产物已存在。可核验现有产物，或指定另一目录重新生成。", code="OUTPUT_EXISTS")

    with tempfile.TemporaryDirectory(prefix=".component-build-", dir=root) as temp:
        work = Path(temp)
        try:
            source = eda.write_altium_source(code, work / f"{stem}.easyeda.json")
        except EasyEDAError as exc:
            raise SourcingError(f"无法下载立创 EDA 源资产: {exc}", code="EDA_DOWNLOAD_FAILED") from exc
        imported = run_altium_import(code, work, input_json=source, preview=False, timeout=timeout)
        if not imported.get("ok"):
            raise SourcingError("转换未成功，请检查转换器和上游资产", code="ALTIUM_CONVERSION_FAILED")
        folder = work / "bundle"
        folder.mkdir()
        renamed = {}
        for key, ext in (("schlibs", ".SchLib"), ("pcblibs", ".PcbLib"), ("step_files", ".step")):
            names = imported[key]
            if len(names) != 1:
                raise SourcingError(f"预期生成一个 {ext}，实际 {len(names)} 个", code="ALTIUM_CONVERSION_INCOMPLETE")
            renamed[key] = Path(names[0]).rename(folder / f"{stem}{ext}")
        links = link_generated_libraries([str(renamed["schlibs"])], [str(renamed["pcblibs"])], renamed=True)
        for link in links["links"]:
            link.update(schlib=Path(link["schlib"]).name, pcblib=Path(link["pcblib"]).name)
        facts, structure = _observe_libraries(renamed["schlibs"], renamed["pcblibs"], renamed["step_files"])
        if not structure["step_matches_embedded"]:
            raise SourcingError("库文件中未读回与下载 STEP 相同的嵌入模型", code="ALTIUM_3D_NOT_EMBEDDED")
        source = Path(source).rename(folder / Path(source).name)
        pdf = folder / f"{stem}.pdf"
        url = datasheet_url or part.datasheet_url
        datasheet_issue = None
        if url:
            try:
                _download(client.session, url, pdf, client.timeout, pdf=True)
            except (SourcingError, OSError, requests.RequestException, ValueError) as exc:
                datasheet_issue = f"Datasheet 下载失败（{type(exc).__name__}）"
        else:
            datasheet_issue = "未取得 Datasheet 链接"
        board, board_facts, board_issue = _observe_board(target_board)
        facts.update(identity={"mpn": mpn, "package": part.package}, **board_facts)
        files = [*renamed.values(), source, *([pdf] if pdf.is_file() else [])]
        report = {
            "status": "generated", "mode": "offline_library", "lcsc_code": code,
            "part": {"mpn": mpn, "brand": part.brand, "package": part.package, "product_url": part.product_url},
            "bundle_dir": str(final), "report_path": str(final / REPORT),
            "files": {path.suffix: str(final / path.name) for path in files},
            "sha256": {path.name: _digest(path) for path in files},
            "datasheet": {"url": url, "downloaded": pdf.is_file(), "issue": datasheet_issue},
            "conversion_warnings": imported.get("quality", {}).get("concerns", []),
            "structure": {**structure, "symbol_footprint_links": links},
            "target_board": board, "target_board_issue": board_issue,
            "target_board_sha256": _digest(Path(board)) if board and not board_issue else None,
            "observations": facts,
        }
        _review(report, [], None)
        _write_report(folder, report)
        folder.rename(final)
    return report


def review_bundle(bundle_dir, expected, orientation=None):
    folder = Path(bundle_dir).expanduser().resolve()
    report = json.loads((folder / REPORT).read_text(encoding="utf-8"))
    if report.get("mode") != "offline_library" or not report.get("sha256"):
        raise SourcingError("目录不是一键流程生成的器件包", code="INVALID_ARGUMENT")
    for name, digest in report["sha256"].items():
        file = folder / name
        if (Path(name).name != name or file.resolve().parent != folder
                or not file.is_file() or _digest(file) != digest):
            raise SourcingError("产物已变更，原核验观察失效", code="BUNDLE_CHANGED")
    if report.get("target_board_sha256") and _digest(Path(report["target_board"])) != report["target_board_sha256"]:
        raise SourcingError("目标板文件已变更，层叠和规则观察失效", code="BUNDLE_CHANGED")
    _review(report, expected, orientation)
    _write_report(folder, report)
    return report


def bundle_summary(report):
    summary = {key: value for key, value in report.items() if key not in {"observations", "review"}}
    summary["observation_count"] = {key: len(group) for key, group in report["observations"].items()}
    summary["checks"] = {
        key: {"status": group["status"], **Counter(row["status"] for row in group["checks"].values())}
        for key, group in report["review"].items()
    }
    return summary
