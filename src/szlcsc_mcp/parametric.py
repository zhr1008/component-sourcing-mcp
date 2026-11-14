from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .models import Part

# Common Chinese/English parameter names encountered in distributor catalogs.
ALIASES: dict[str, tuple[str, ...]] = {
    "output_voltage": ("输出电压", "输出电压(V)", "Output Voltage", "Vout"),
    "input_voltage": ("输入电压", "输入电压(V)", "Input Voltage", "Vin"),
    "input_voltage_min": ("最小输入电压", "输入电压最小值", "Input Voltage Min", "Vin Min"),
    "input_voltage_max": ("最大输入电压", "输入电压最大值", "Input Voltage Max", "Vin Max"),
    "output_current": ("输出电流", "最大输出电流", "Output Current", "Output Current (Max)"),
    "accuracy": ("精度", "初始精度", "输出精度", "Accuracy", "Initial Accuracy"),
    "tolerance": ("精度", "容差", "误差", "Tolerance", "Accuracy"),
    "tempco": ("温漂", "温度系数", "Temperature Coefficient", "Tempco"),
    "resistance": ("阻值", "Resistance"),
    "capacitance": ("容值", "电容量", "Capacitance"),
    "inductance": ("电感量", "电感", "Inductance"),
    "voltage_rating": ("额定电压", "耐压", "Voltage Rated", "Voltage Rating"),
    "dielectric": ("介质", "电容介质", "介电材料", "温度特性", "Dielectric", "Temperature Characteristic"),
    "initial_accuracy": ("初始精度", "Initial Accuracy"),
    "operating_grade": ("等级", "应用等级", "Quality Grade", "Operating Grade"),
    "qualification": ("资质", "认证", "车规认证", "Qualification", "Automotive Standard"),
    "isolation_voltage": ("隔离电压", "隔离耐压", "Isolation Voltage", "Withstand Isolation Voltage"),
    "reference_type": ("电压基准类型", "基准类型", "Reference Type"),
    "can_fd": ("CAN FD", "CAN-FD", "协议", "Protocol"),
    "supply_voltage": ("电源电压", "供电电压", "工作电压", "Supply Voltage"),
    "data_rate": ("数据速率", "传输速率", "Data Rate"),
    "operating_temperature": ("工作温度", "Operating Temperature"),
    "quiescent_current": ("静态电流", "静态电流(Iq)", "Quiescent Current", "Iq"),
    "drain_source_voltage": ("漏源电压", "漏源耐压", "漏源电压(Vdss)", "Drain to Source Voltage", "Vdss"),
    "drain_current": ("连续漏极电流", "漏极电流", "Continuous Drain Current", "Id"),
    "on_resistance": ("导通电阻", "导通电阻(RDS(on))", "On Resistance", "RDS(on)"),
    "channels": ("通道数", "Channels", "Number of Channels"),
    "gain_bandwidth": ("增益带宽积", "Gain Bandwidth Product", "GBW"),
    "input_offset": ("输入失调电压", "Input Offset Voltage"),
    "core": ("内核", "处理器内核", "Core", "Core Processor"),
    "flash": ("闪存", "程序存储容量", "Flash", "Program Memory Size"),
    "ram": ("RAM容量", "RAM", "RAM Size"),
    "clock_frequency": ("主频", "最大频率", "Clock Frequency", "Speed"),
    "current_rating": ("额定电流", "电流", "Current Rating", "Rated Current"),
    "power": ("功率", "额定功率", "Power", "Power Rating"),
    "package": ("封装", "Package"),
}

_PREFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}
_SCALAR = re.compile(
    r"(?:±|\+/-)?\s*(?P<number>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s*(?P<prefix>[pnumkKMGµμ]?)\s*"
    r"(?P<unit>(?i:ppm(?:\s*/\s*(?:°?C|℃))?|V|A|W|F|H|Ω|ohms?|%))?"
)


@dataclass(slots=True)
class ParsedNumber:
    value: float
    unit: str | None = None


def _clean_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    cleaned = re.sub(r"\s+", "", unit).replace("℃", "°C").casefold()
    return {"ω": "ohm", "ohms": "ohm", "ppm/c": "ppm/°c"}.get(cleaned, cleaned)


def parse_number(text: Any) -> ParsedNumber | None:
    if isinstance(text, ParsedNumber):
        return text if math.isfinite(text.value) else None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        try:
            value = float(text)
        except OverflowError:
            return None
        return ParsedNumber(value, None) if math.isfinite(value) else None
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    # Only a complete scalar is numeric. Digits inside package names and ranges
    # must never become evidence of an electrical or package match.
    m = _SCALAR.fullmatch(s)
    if not m:
        return None
    value = float(m.group("number")) * _PREFIXES.get(m.group("prefix"), 1.0)
    return ParsedNumber(value, _clean_unit(m.group("unit"))) if math.isfinite(value) else None




def parse_number_range(text: Any) -> list[ParsedNumber]:
    if text is None:
        return []
    s = str(text).strip().replace(",", "")
    # Split common distributor range notations while preserving units on each side.
    parts = re.split(r"\s*(?:~|～|至|to)\s*", s, flags=re.I)
    if len(parts) != 2:
        return []
    values = [parse_number(part) for part in parts[:2]]
    if any(value is None for value in values):
        return []
    left, right = values  # type: ignore[misc]
    # A bare bound inherits both the prefix and unit: 1~5mV means 1mV~5mV.
    # An explicit prefix is retained, so 1k~5kΩ never gets scaled twice.
    def inherit(value, raw, other, other_raw):
        match = _SCALAR.fullmatch(raw.strip())
        other_match = _SCALAR.fullmatch(other_raw.strip())
        scale = 1.0
        if match and other_match and not match.group("prefix"):
            scale = _PREFIXES.get(other_match.group("prefix"), 1.0)
        return ParsedNumber(value.value * scale, other.unit)

    if left.unit is None and right.unit is not None:
        left = inherit(left, parts[0], right, parts[1])
    if right.unit is None and left.unit is not None:
        right = inherit(right, parts[1], left, parts[0])
    if left.unit != right.unit or not all(math.isfinite(v.value) for v in (left, right)):
        return []
    return [left, right]

def _normalize_key(s: str) -> str:
    return re.sub(r"[\s_()（）\-/:：]+", "", s).casefold()


def find_parameter(part: Part, name: str, *, exact_label: bool = False) -> tuple[str | None, Any]:
    if name.casefold() == "package":
        return "package", part.package
    wanted = [name]
    aliases = ALIASES.get(name.casefold())
    if aliases:
        wanted.extend(aliases)
    normalized = {_normalize_key(str(k)): (str(k), v) for k, v in part.parameters.items()}
    for candidate in wanted:
        hit = normalized.get(_normalize_key(candidate))
        if hit:
            return hit
    if exact_label:
        return None, None
    # Fuzzy fallback: an alias contained in a longer SZLCSC parameter label.
    for candidate in wanted:
        c = _normalize_key(candidate)
        if len(c) < 2:
            continue
        for nk, pair in normalized.items():
            if c in nk:
                return pair
    return None, None


def _parse_constraint(expr: str) -> tuple[str, str]:
    s = str(expr).strip().replace("≤", "<=").replace("≥", ">=").replace("≠", "!=").replace("％", "%")
    m = re.match(r"^(<=|>=|==|=|<|>|~=|~|!=)\s*(.+)$", s)
    if m:
        return m.group(1), m.group(2).strip()
    return "=", s


def _numeric_match(actual: Any, expected: str, op: str) -> tuple[bool, str]:
    a = parse_number(actual)
    e = parse_number(expected)
    if not a or not e:
        return False, "not_numeric"
    # Avoid comparing obviously different physical dimensions when both are known.
    if a.unit and e.unit:
        au = a.unit.replace("ω", "ohm").replace("Ω", "ohm")
        eu = e.unit.replace("ω", "ohm").replace("Ω", "ohm")
        if au != eu:
            return False, f"unit_mismatch:{a.unit}!={e.unit}"
    av, ev = a.value, e.value
    if op in ("=", "=="):
        ok = math.isclose(av, ev, rel_tol=1e-6, abs_tol=0)
    elif op == "!=":
        ok = not math.isclose(av, ev, rel_tol=1e-6, abs_tol=0)
    elif op == "<=":
        ok = av <= ev or math.isclose(av, ev, rel_tol=1e-9, abs_tol=0)
    elif op == ">=":
        ok = av >= ev or math.isclose(av, ev, rel_tol=1e-9, abs_tol=0)
    elif op == "<":
        ok = av < ev
    elif op == ">":
        ok = av > ev
    elif op in ("~", "~="):
        ok = math.isclose(av, ev, rel_tol=0.05, abs_tol=0)
    else:
        ok = False
    return ok, f"{av:g} {op} {ev:g}"


def match_constraint(part: Part, key: str, expression: str, *, exact_label: bool = False) -> dict[str, Any]:
    label, actual = find_parameter(part, key, exact_label=exact_label)
    op, expected = _parse_constraint(expression)
    derived_from_range = None
    if key.casefold() in {"input_voltage_min", "input_voltage_max"}:
        range_label, range_value = label, actual
        if actual is None:
            range_label, range_value = find_parameter(part, "input_voltage", exact_label=exact_label)
        values = parse_number_range(range_value)
        if values:
            if range_label:
                label = range_label
            actual = min(values, key=lambda x: x.value) if key.casefold().endswith("_min") else max(values, key=lambda x: x.value)
            derived_from_range = range_value if range_value is not None else actual
    if actual is None or (exact_label and isinstance(actual, str) and actual.strip().casefold() in {"", "-", "--", "—", "n/a", "null"}):
        return {"key": key, "label": label, "expected": expression, "actual": None, "matched": False, "reason": "missing"}

    numeric = parse_number(expected)
    if numeric is not None and key.casefold() != "package":
        measured = parse_number(actual)
        if exact_label and key.casefold() == "supply_voltage" and op in {"=", "=="}:
            bounds = parse_number_range(actual)
            if bounds and all(bound.unit == numeric.unit for bound in bounds):
                return {"key": key, "label": label, "expected": expression, "actual": actual,
                        "matched": bounds[0].value <= numeric.value <= bounds[1].value,
                        "reason": "supply_range"}
        if exact_label and numeric.unit and measured and not measured.unit:
            heading = re.search(r"[（(]([^()（）]+)[)）]$", label or "")
            if heading:
                measured = parse_number(f"{actual}{heading[1]}")
            if not measured or not measured.unit:
                return {"key": key, "label": label, "expected": expression, "actual": actual,
                        "matched": False, "reason": "unit_unknown"}
        matched, reason = _numeric_match(measured, expected, op)
    else:
        a = str(actual).strip().casefold()
        e = str(expected).strip().casefold()
        contains = e in a
        if key.casefold() == "package":
            # A family may match a suffix separated by punctuation, but an
            # explicit 8-pin package must not match an 80-pin package.
            contains = re.search(r"(?<!\w)" + re.escape(e) + r"(?!\w)", a) is not None
        if op == "!=":
            matched = not contains
        elif op == "==":
            matched = e == a
        elif op in {"=", "~", "~="}:
            matched = contains
        else:
            matched = False
        reason = "text"
    actual_out: Any = actual.value if isinstance(actual, ParsedNumber) else actual
    result = {"key": key, "label": label, "expected": expression, "actual": actual_out, "matched": matched, "reason": reason}
    if derived_from_range is not None:
        result["derived_from_range"] = derived_from_range
    return result


def evaluate_constraints(part: Part, constraints: dict[str, str], *, exact_labels: bool = False) -> dict[str, Any]:
    results = [match_constraint(part, k, v, exact_label=exact_labels) for k, v in constraints.items()]
    matched = sum(1 for x in results if x["matched"])
    return {
        "matched": matched,
        "total": len(results),
        "all_matched": matched == len(results),
        "details": results,
    }
