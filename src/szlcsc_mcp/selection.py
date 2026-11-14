"""Interpret a sourcing request and carry its conditions between MCP calls."""
from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .errors import SourcingError
from .models import Part
from .parametric import find_parameter, parse_number
from .schemas import ConstraintText, Quantity, Query


# The order matters where one component name includes another.
TYPES = (
    ("电压基准", r"电压基准|基准电压|基准源|voltage\s+reference", "电压基准", "voltage reference"),
    ("CAN收发器", r"(?<![A-Za-z0-9])CAN(?:[- ]?FD)?(?:\s*(?:总线|收发器|transceiver))?(?![A-Za-z0-9])", "CAN收发器", "CAN transceiver"),
    ("RS485收发器", r"RS[- ]?485(?:\s*(?:收发器|transceiver))?|485\s*收发器", "RS485收发器", "RS485 transceiver"),
    ("线性稳压器", r"\bLDO\b|线性稳压器?|低压差稳压器?", "LDO", "线性稳压器"),
    ("运算放大器", r"运算放大器|运放|op[- ]?amp", "运算放大器", "operational amplifier"),
    ("微控制器", r"微控制器|单片机|\bMCU\b", "微控制器", "MCU"),
    ("MOS管", r"MOSFET|MOS\s*管|场效应管", "MOSFET", "场效应管"),
    ("钽电容", r"钽电容|tantalum\s+capacitor", "钽电容", "tantalum capacitor"),
    ("电解电容", r"铝电解电容|电解电容|electrolytic\s+capacitor", "电解电容", "electrolytic capacitor"),
    ("贴片电容", r"(?:贴片|陶瓷)?电容(?:器)?|\b(?:MLCC|capacitor)\b", "贴片电容", "MLCC"),
    ("贴片电阻", r"(?:贴片)?电阻(?:器)?|\bresistor\b", "贴片电阻", "chip resistor"),
    ("电感", r"(?:贴片|功率)?电感(?:器)?|\binductor\b", "电感", "inductor"),
)
_BY_TYPE = {entry[0]: entry[2:] for entry in TYPES}
_NAME = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
_PACKAGE = r"(?<![A-Za-z0-9])(?:TSOT|SOT|SOIC|TSSOP|SSOP|MSOP|SOP|QFN|DFN|LQFP|TQFP|QFP|DIP|WLCSP|BGA)[- ]?\d+(?:[-_]\d+)*"
_SMD = r"01005|0201|0402|0603|0805|1008|1206|1210|1812|2010|2512"
_OP = r"<=|>=|==|!=|[<>≤≥=≠]"
_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_PIECES = r"pcs\b|件|片|只|个|颗|粒|套"
_QTY = re.compile(
    rf"(?<![\d.])(?:采购|购买|订购|买|要|需要|用|报价)\s*(?P<ordered>\d+)\s*(?:{_PIECES})"
    rf"|(?:数量|qty|quantity)\s*(?:为|改成|改为|[：:=])?\s*(?P<count>\d+)(?=\s*(?:{_PIECES}|[，。,.！!]|$))"
    rf"|(?<![\d.])(?P<pieces>\d+)\s*(?:{_PIECES})\s*(?:的?报价|采购|订购|用量|价格|库存|多少钱)",
    re.I,
)
_QUANTITY_ONLY = re.compile(rf"\s*(?:改成|改为|这次|本次|按)?\s*(\d+)\s*(?:{_PIECES})\s*[。！.]?\s*", re.I)
_CLEAR_QTY = re.compile(r"取消数量|去掉数量|只看选型|暂时不查报价|不查(?:库存和)?报价")
_ACCURACY = r"初始精度|初始误差|initial\s+accuracy|精度|容差|公差|tolerance"
_V = r"(?:[kmunµμ]?V)(?![a-z])"
_CAPACITANCE = r"[pnuµμm]?F(?![a-z])"
_ISOLATION = re.compile(r"非隔离|non[- ]?isolat|隔离(?!(?:耐压|电压))|isolated", re.I)
_CONDITION_NAMES = {
    "output_voltage": "输出电压", "input_voltage_min": "输入下限", "input_voltage_max": "输入上限",
    "initial_accuracy": "初始精度", "accuracy": "精度", "tolerance": "容差", "tempco": "温漂",
    "output_current": "输出电流", "isolation_voltage": "隔离耐压", "voltage_rating": "耐压",
    "current_rating": "额定电流", "power": "功率", "resistance": "阻值", "capacitance": "容值",
    "inductance": "电感量", "dielectric": "介质", "operating_grade": "等级",
    "qualification": "资质", "package": "封装", "can_fd": "CAN FD",
    "supply_voltage": "供电电压", "data_rate": "数据速率", "operating_temperature": "工作温度",
    "quiescent_current": "静态电流", "drain_source_voltage": "漏源电压", "drain_current": "漏极电流",
    "on_resistance": "导通电阻", "channels": "通道数", "gain_bandwidth": "增益带宽积",
    "input_offset": "输入失调电压", "core": "内核", "flash": "闪存", "ram": "RAM", "clock_frequency": "主频",
    "reference_type": "基准类型", "input_voltage": "输入电压",
}
_CONDITION_KEYS = {label: key for key, label in _CONDITION_NAMES.items()}


class SelectionCriteria(BaseModel):
    """Conditions carried in the tool response and passed back on refinement."""

    model_config = ConfigDict(extra="forbid")

    catalog_query: Query
    component_type: _NAME = "电子器件"
    constraints: Annotated[dict[ConstraintText, ConstraintText], Field(max_length=30)] = Field(default_factory=dict)
    quantity: Quantity | None = None


def _quantity(request: str) -> int | None:
    match = _QTY.search(request)
    quantity_only = _QUANTITY_ONLY.fullmatch(request)
    if match or quantity_only:
        count = int((match["count"] or match["ordered"] or match["pieces"]) if match else quantity_only[1])
        if not 1 <= count <= 1_000_000_000:
            raise SourcingError("采购数量须在 1 到 1000000000 之间", code="INVALID_ARGUMENT")
        return count
    return None


def _numeric_filter(request, names, units, default="=", *, required_label=True):
    label = rf"(?:{names})" if required_label else rf"(?:(?:{names})\s*)?"
    pattern = rf"{label}\s*(?:可以|可|能)?\s*(?:改成|改为|放宽到|放宽至|调整为|为|[:：])?\s*(?P<op>{_OP}|至少|不低于|不超过|至多|以内)?\s*"
    match = re.search(pattern + rf"\s*±?\s*(?P<value>{_NUMBER}\s*(?:{units}))", request, re.I)
    if match is None:
        return None
    op = {"≤": "<=", "≥": ">=", "≠": "!=", "至少": ">=", "不低于": ">=", "不超过": "<=",
          "至多": "<=", "以内": "<="}.get(match["op"], match["op"] or default)
    return op + re.sub(r"\s+", "", match["value"]).replace("％", "%")


def _filters(request: str, kind: str, previous: SelectionCriteria | None) -> dict[str, str]:
    filters = {}
    capacitor = kind.endswith("电容")
    passive = capacitor or kind in {"贴片电阻", "电感"}
    if alternate := re.search(rf"(?:{_PACKAGE}|{_SMD})\s*(?:或|或者|or|/)\s*(?:{_PACKAGE}|{_SMD})", request, re.I):
        raise SourcingError(f"识别到备选封装 {alternate[0]}。请指定本轮采用的封装，或分开查两种封装。", code="AMBIGUOUS_REQUIREMENT")
    package = re.search(_PACKAGE, request, re.I)
    if package is None and passive:
        package = re.search(rf"(?<![A-Za-z0-9.])(?:{_SMD})(?![a-zA-Z0-9.])", request)
    if package and not re.search(r"封装不限|不限封装|取消封装限制", request):
        filters["package"] = "=" + re.sub(r"\s+", "-", package[0]).upper()
    dielectric = re.search(r"(?<![a-z0-9])(?:X[578][RS]|Y5V|Z5U|C0G|NP0)(?![a-z0-9])", request, re.I)
    if capacitor and dielectric:
        filters["dielectric"] = "==" + dielectric[0].upper()
    qualification = re.search(r"AEC[- ]?Q\d+", request, re.I)
    if qualification:
        filters["qualification"] = "==" + "AEC-Q" + qualification[0][qualification[0].upper().index("Q") + 1:]
    accuracy = _numeric_filter(request, _ACCURACY, r"[%％]", "<=")
    if accuracy:
        initial = bool(re.search(r"初始精度|初始误差|initial\s+accuracy", request, re.I))
        if not initial and previous is not None:
            initial = "initial_accuracy" in previous.constraints and "accuracy" not in previous.constraints
        filters["tolerance" if passive else ("initial_accuracy" if initial else "accuracy")] = accuracy
    elif passive and (tolerance := re.search(rf"±\s*({_NUMBER})\s*[%％]", request)):
        filters["tolerance"] = "<=" + tolerance[1] + "%"
    for key, names, units, default in (
        ("input_voltage_min", r"最低输入(?:电压)?|输入下限|最小输入电压", _V, "<="),
        ("input_voltage_max", r"最高输入(?:电压)?|输入上限|最大输入电压", _V, ">="),
        ("output_voltage", r"输出(?:电压)?|Vout", _V, "="),
        ("output_current", r"输出电流|Iout", r"[munµμ]?A(?![a-z])", ">="),
        ("tempco", r"温漂|温度系数|tempco", r"ppm(?:\s*/\s*(?:°?C|℃))?", "<="),
        ("isolation_voltage", r"隔离耐压|隔离电压|isolation voltage", _V, ">="),
        ("voltage_rating", r"耐压|额定电压|voltage rating", _V, ">="),
        ("current_rating", r"额定电流|电流容量|rated current", r"[munµμ]?A(?![a-z])", ">="),
        ("power", r"额定功率|功率|rated power", r"[munµμ]?W(?![a-z])", ">="),
        ("supply_voltage", r"供电电压|电源电压|供电|VCC", _V, "="),
    ):
        source = re.sub(r"隔离耐压", "隔离电压", request) if key == "voltage_rating" else request
        value = _numeric_filter(source, names, units, default)
        if value:
            filters[key] = value
    if "supply_voltage" not in filters:
        source = re.search(rf"(?<![A-Za-z0-9.])({_NUMBER}\s*{_V})\s*供电", request, re.I)
        if source:
            filters["supply_voltage"] = "=" + re.sub(r"\s+", "", source[1])
    if kind == "电压基准" and (topology := re.search(r"串联|并联", request)):
        filters["reference_type"] = "=" + topology[0]
    if capacitor and "voltage_rating" not in filters and re.search(rf"{_NUMBER}\s*{_CAPACITANCE}", request, re.I):
        if value := _numeric_filter(request, r"耐压", _V, ">=", required_label=False):
            filters["voltage_rating"] = value
    if kind in {"电压基准", "线性稳压器"} and not (
        "output_voltage" in filters or re.search(r"输入|Vin|input|供电|VCC|耐压", request, re.I)
    ):
        voltages = re.findall(rf"(?<![A-Za-z0-9.]){_NUMBER}\s*{_V}", request, re.I)
        if len(voltages) == 1:
            filters["output_voltage"] = "=" + re.sub(r"\s+", "", voltages[0])
    for key, names, units in (
        ("capacitance", r"容值|电容量", _CAPACITANCE),
        ("resistance", r"阻值|电阻值", r"[mMkK]?(?:Ω|ohms?|欧姆)(?![a-z])|[kKM](?![\w.])"),
        ("inductance", r"电感量", r"[nuµμm]?H(?![a-z])"),
    ):
        implicit = (key == "capacitance" and capacitor or key == "resistance" and kind == "贴片电阻"
                    or key == "inductance" and kind == "电感")
        if value := _numeric_filter(request, names, units, required_label=not implicit):
            if key == "resistance" and re.search(r"[kKM]$", value):
                value += "Ω"
            if key == "resistance":
                value = value.replace("欧姆", "Ω")
            filters[key] = value
    return filters


def selection_criteria(request: str, previous: SelectionCriteria | None = None, *,
                       catalog_query: str | None = None, quantity: int | None = None,
                       conditions: dict[str, str | None] | None = None) -> tuple[SelectionCriteria, list[str]]:
    known_kind = next((name for name, pattern, *_ in TYPES if re.search(pattern, request, re.I)), None)
    if not previous and known_kind is None and re.search(rf"{_NUMBER}\s*[pnuµμm]F(?![a-z])", request, re.I):
        known_kind = "贴片电容"
    if previous and known_kind and known_kind != previous.component_type:
        raise SourcingError("本次请求指定了新器件类目，请以本次需求重新开始选型", code="NEW_SELECTION_REQUIRED")
    kind = known_kind or (previous.component_type if previous else "电子器件")
    if catalog_query:
        query = catalog_query
    elif previous:
        query = previous.catalog_query
    elif known_kind:
        query = _BY_TYPE[kind][0]
    elif re.fullmatch(r"[A-Za-z0-9_.+/-]{2,200}", request):
        query = request
    else:
        raise SourcingError("请提供器件类目或完整型号。也可通过 catalog_query 提供简短的目录检索词。", code="CATEGORY_REQUIRED")
    parsed = _filters(request, kind, previous)
    filters = dict(previous.constraints) if previous else {}
    filters.update(parsed)
    if re.search(r"封装不限|不限封装|取消封装限制", request):
        filters.pop("package", None)
    isolation = _ISOLATION.search(request) if kind == "CAN收发器" else None
    if isolation and not catalog_query:
        query = "非隔离CAN收发器" if re.match(r"非隔离|non[- ]?isolat", isolation[0], re.I) else "隔离式CAN收发器"
    if re.search(r"(?<![A-Za-z])CAN[- ]?FD(?![A-Za-z])", request, re.I):
        filters["can_fd"] = "=CAN FD"
    if re.search(r"车规|automotive", request, re.I) and "qualification" not in filters:
        filters["qualification"] = "=车规"
    for name, value in (conditions or {}).items():
        name = _CONDITION_KEYS.get(name, name.casefold())
        if value is None:
            filters.pop(name, None)
        else:
            filters[name] = value
    clear_quantity = bool(_CLEAR_QTY.search(request))
    count = quantity
    if count is None and not clear_quantity:
        count = _quantity(request)
    new_quantity = count
    if count is None and previous and not clear_quantity:
        count = previous.quantity
    if (previous and filters == previous.constraints and query == previous.catalog_query
            and not parsed and not conditions and not catalog_query and new_quantity is None and not clear_quantity):
        raise SourcingError("未识别到本轮改动，请说出条件和值，或用 conditions 更新选型条件", code="REFINEMENT_REQUIRED")

    notes = []
    if "can_fd" in filters:
        notes.append("CAN FD 能力需核对厂家资料，可用 conditions 提供目录参数名和值。")
    if filters.get("qualification") == "=车规":
        notes.append("车规资格待核实，请给出具体要求，如 AEC-Q100。")
    if re.search(r"便宜|低价|好买|有货|现货|交期", request) and count is None:
        notes.append("提供采购数量后可查询立创库存和对应数量报价。")
    if re.search(r"(?:预算|交期|寿命|工作温度|压差|PSRR)", request, re.I) and not conditions:
        notes.append("请通过 conditions 补充未列入选型条件的硬性指标。")
    if len(filters) > 30:
        raise SourcingError("选型条件最多支持 30 项", code="INVALID_ARGUMENT")
    return SelectionCriteria(catalog_query=query, component_type=kind, constraints=filters, quantity=count), notes


def _status(detail: dict) -> str:
    if detail["matched"]:
        return "matched"
    reason = detail["reason"]
    if reason in {"missing", "not_numeric", "unit_unknown"} or reason.startswith("unit_mismatch:"):
        return "unknown"
    return "not_matched"


def _text(value: Any) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return "待核实"
    value = re.sub(r"\s+", " ", str(value).strip()).replace("/", "／")
    return value[:80] if value and value.casefold() not in {"-", "--", "—", "n/a", "null"} else "待核实"


def _parameter(part: Part, key: str) -> str:
    label, value = find_parameter(part, key, exact_label=True)
    return _display_parameter(key, label, value)


def _display_parameter(key, label, value) -> str:
    text = _text(value)
    parsed = parse_number(value)
    if parsed is not None and parsed.unit is None:
        # A unit printed in the catalog column heading is part of its evidence.
        heading = re.search(r"[（(]([^()（）]+)[)）]$", label or "")
        if heading and parse_number(f"{value}{heading[1]}"):
            text += heading[1]
        elif key in {"voltage_rating", "output_voltage", "capacitance", "resistance", "inductance",
                     "tolerance", "accuracy", "initial_accuracy", "supply_voltage", "isolation_voltage",
                     "output_current", "current_rating", "power", "quiescent_current", "on_resistance"}:
            text = "待核实"
    return text


def _ordering_code(part: Part) -> str:
    model = (part.mpn or "").strip()
    annotated = re.fullmatch(r"([^()]+)\(([^()]+)\)", model)
    brand = re.match(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", part.brand or "")
    if annotated and brand and annotated[2].casefold() == brand[0].strip().casefold():
        return annotated[1].strip()
    return model


def _ad_fields(part: Part, kind: str, specs: dict[str, str], mpn: str) -> tuple[str, str]:
    pack = _text(part.package)
    if kind.endswith("电容"):
        capacitance = specs.get("capacitance", "待核实")
        voltage = specs.get("voltage_rating", "待核实")
        tolerance = specs.get("tolerance", "待核实")
        grade = specs.get("dielectric", "待核实")
        if grade == "待核实":
            grade = specs.get("operating_grade", "待核实")
        return f"{capacitance}/{voltage}", "/".join((kind, capacitance, voltage, tolerance, grade, pack))
    if kind == "贴片电阻":
        resistance = specs.get("resistance", "待核实")
        return resistance, "/".join((kind, resistance, specs.get("power", "待核实"),
                                     specs.get("tolerance", "待核实"), specs.get("operating_grade", "待核实"), pack))
    comment = specs.get("inductance", "待核实") if kind == "电感" else mpn
    values = [f"{_CONDITION_NAMES.get(key, key)}{value}" for key, value in specs.items() if value != "待核实"]
    spec = values[0] if values else "规格待核实"
    other = "、".join(values[1:4]) or "重要参数待核实"
    return comment, "/".join((_text(kind), spec, mpn, other, pack,
                              _text(part.lcsc_code), _text(part.brand)))


def selection_row(row: dict, criteria: SelectionCriteria) -> dict:
    part: Part = row["part"]
    support = row.get("constraint_match") or {"details": []}
    checks = [{**item, "name": _CONDITION_NAMES.get(item["key"], item["key"]), "status": _status(item)}
              for item in support["details"]]
    specs = {}
    for item in checks:
        if item["key"] not in {"package", "qualification"} and item["status"] == "matched":
            specs[item["key"]] = _display_parameter(item["key"], item["label"], item["actual"])
    default_specs = {
        "电压基准": ("output_voltage", "initial_accuracy", "accuracy", "tempco"),
        "线性稳压器": ("output_voltage", "input_voltage", "output_current", "quiescent_current"),
        "CAN收发器": ("supply_voltage", "isolation_voltage", "data_rate"),
        "RS485收发器": ("supply_voltage", "data_rate", "operating_temperature"),
        "MOS管": ("drain_source_voltage", "drain_current", "on_resistance"),
        "运算放大器": ("supply_voltage", "channels", "gain_bandwidth", "input_offset"),
        "微控制器": ("core", "flash", "ram", "clock_frequency"),
        "贴片电容": ("capacitance", "voltage_rating", "tolerance", "dielectric", "operating_grade"),
        "钽电容": ("capacitance", "voltage_rating", "tolerance", "operating_grade"),
        "电解电容": ("capacitance", "voltage_rating", "tolerance", "operating_grade"),
        "贴片电阻": ("resistance", "power", "tolerance", "operating_grade"),
        "电感": ("inductance", "current_rating", "tolerance"),
    }
    kind = criteria.component_type
    if kind == "电子器件" and part.category:
        kind = next((name for name, pattern, *_ in TYPES if re.search(pattern, part.category, re.I)), part.category)
    for key in default_specs.get(kind, ()):
        if key not in specs:
            specs[key] = _parameter(part, key)
    mpn = _ordering_code(part)
    comment, description = _ad_fields(part, kind, specs, mpn)
    return {"mpn": mpn, "lcsc_code": part.lcsc_code, "brand": part.brand, "package": part.package,
            "component_type": kind, "parameters": specs,
            "ad_comment": comment, "ad_description": description,
            "match_status": ("pending_evidence" if any(c["status"] == "unknown" for c in checks)
                             or any(c["status"] == "unknown" for c in row["requirements"])
                             else ("catalog_match" if checks else "candidate")),
            "evidence": {"source": "立创商城目录或商品详情", "product_url": part.product_url,
                         "datasheet_url": part.datasheet_url, "listing_mpn": part.mpn, "checks": checks,
                         "requirements": row["requirements"], "found_by": row["found_by"],
                         "warnings": part.warnings}}


def shortlist(client, criteria: SelectionCriteria, *, refresh: bool = False) -> dict:
    primary = criteria.catalog_query
    terms = _BY_TYPE.get(criteria.component_type, ())
    aliases = [term for term in terms if term.casefold() != primary.casefold()]
    if primary == "隔离式CAN收发器":
        aliases = ["isolated CAN", *aliases]
    if primary in terms and criteria.component_type in {"电压基准", "线性稳压器", "贴片电容", "贴片电阻", "电感"}:
        values = [criteria.constraints[key][1:] for key in ("output_voltage", "capacitance", "resistance", "inductance")
                  if re.fullmatch(r"=[0-9.].*", criteria.constraints.get(key, ""))]
        if values:
            aliases.insert(0, primary)
            primary = " ".join([*values, primary])
    result = client.discover(primary, max_results=20, detail_budget=20, max_queries=6,
                             family_queries=aliases, constraints=criteria.constraints or None,
                             include_procurement=False, exact_labels=True, refresh=refresh)
    if not result.get("ok", True):
        return {**result, "selection": criteria.model_dump()}
    rows = []
    excluded: dict[str, int] = {}
    for row in result["results"]:
        part = row["part"]
        if not (part.mpn and re.fullmatch(r"[Cc]\d+", part.lcsc_code or "")):
            excluded["型号或立创料号"] = excluded.get("型号或立创料号", 0) + 1
            continue
        known_kind = next((name for name, pattern, *_ in TYPES if re.search(pattern, part.category or "", re.I)), None)
        if known_kind and criteria.component_type not in {known_kind, "电子器件"}:
            excluded["器件类目"] = excluded.get("器件类目", 0) + 1
            continue
        checks = (row["constraint_match"] or {"details": []})["details"]
        failures = [item["key"] for item in checks if _status(item) == "not_matched"]
        failures.extend(item["requirement"] for item in row["requirements"] if item["status"] == "mismatched")
        if failures:
            for key in failures:
                name = _CONDITION_NAMES.get(key, key)
                excluded[name] = excluded.get(name, 0) + 1
        else:
            rows.append(selection_row(row, criteria))
    rows.sort(key=lambda row: row["match_status"] == "pending_evidence")
    coverage = result["coverage"]
    return {"selection": criteria.model_dump(), "results": rows[:5], "count": min(len(rows), 5),
            "eligible_inspected": len(rows), "excluded_by_condition": excluded,
            "coverage": {**coverage, "returned_count": min(len(rows), 5)}}
