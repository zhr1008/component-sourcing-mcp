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
    "current_rating": ("额定电流", "电流", "Current Rating", "Rated Current"),
    "power": ("功率", "额定功率", "Power", "Power Rating"),
    "package": ("封装", "Package"),
}

_PREFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}
_UNIT_FACTORS = {
    "v": 1.0,
    "a": 1.0,
    "w": 1.0,
    "ohm": 1.0,
    "ω": 1.0,
    "Ω": 1.0,
    "f": 1.0,
    "h": 1.0,
    "%": 1.0,
    "ppm": 1.0,
    "ppm/°c": 1.0,
    "ppm/c": 1.0,
}


@dataclass(slots=True)
class ParsedNumber:
    value: float
    unit: str | None = None


def _clean_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    return unit.strip().replace("℃", "°C").replace("ohms", "ohm").replace("ohms", "ohm")


def parse_number(text: Any) -> ParsedNumber | None:
    if isinstance(text, ParsedNumber):
        return text
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return ParsedNumber(float(text), None)
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    # ppm must be recognized before the SI-prefix grammar, otherwise the first "p"
    # in "ppm" would be interpreted as pico.
    ppm = re.search(r"([-+]?\d+(?:\.\d+)?)\s*ppm(?:\s*/\s*(?:°?C|℃))?", s, re.I)
    if ppm:
        return ParsedNumber(float(ppm.group(1)), "ppm/°c" if "/" in ppm.group(0) else "ppm")
    # First number + optional SI prefix + unit. Supports 3.3V, 200mA, 0.1%, 10kΩ.
    m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*([pnumkKMGµ]?)\s*(V|A|W|F|H|Ω|ohm|%)?", s, re.I)
    if not m:
        return None
    value = float(m.group(1))
    prefix = m.group(2) or ""
    unit = _clean_unit(m.group(3))
    if prefix:
        value *= _PREFIXES.get(prefix, _PREFIXES.get(prefix.lower(), 1.0))
    if unit:
        ul = unit.lower().replace(" ", "")
        if ul in _UNIT_FACTORS:
            value *= _UNIT_FACTORS[ul]
            unit = ul
        elif unit in _UNIT_FACTORS:
            value *= _UNIT_FACTORS[unit]
    return ParsedNumber(value, unit)




def parse_number_range(text: Any) -> list[ParsedNumber]:
    if text is None:
        return []
    s = str(text).strip().replace(",", "")
    # Split common distributor range notations while preserving units on each side.
    parts = re.split(r"\s*(?:~|～|至|to)\s*", s, flags=re.I)
    if len(parts) < 2:
        return []
    values = [parse_number(part) for part in parts[:2]]
    if any(value is None for value in values):
        return []
    left, right = values  # type: ignore[misc]
    # If only one side contains a unit, inherit it for comparison semantics.
    if left.unit is None and right.unit is not None:
        left = ParsedNumber(left.value, right.unit)
    if right.unit is None and left.unit is not None:
        right = ParsedNumber(right.value, left.unit)
    return [left, right]

def _normalize_key(s: str) -> str:
    return re.sub(r"[\s_()（）\-/:：]+", "", s).casefold()


def find_parameter(part: Part, name: str) -> tuple[str | None, Any]:
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
    # Fuzzy fallback: an alias contained in a longer SZLCSC parameter label.
    for candidate in wanted:
        c = _normalize_key(candidate)
        if len(c) < 2:
            continue
        for nk, pair in normalized.items():
            if c in nk or nk in c:
                return pair
    return None, None


def _parse_constraint(expr: str) -> tuple[str, str]:
    s = str(expr).strip()
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
    eps = max(abs(ev) * 1e-9, 1e-12)
    if op in ("=", "=="):
        ok = math.isclose(av, ev, rel_tol=1e-6, abs_tol=eps)
    elif op == "!=":
        ok = not math.isclose(av, ev, rel_tol=1e-6, abs_tol=eps)
    elif op == "<=":
        ok = av <= ev + eps
    elif op == ">=":
        ok = av + eps >= ev
    elif op == "<":
        ok = av < ev
    elif op == ">":
        ok = av > ev
    elif op in ("~", "~="):
        ok = math.isclose(av, ev, rel_tol=0.05, abs_tol=eps)
    else:
        ok = False
    return ok, f"{av:g} {op} {ev:g}"


def match_constraint(part: Part, key: str, expression: str) -> dict[str, Any]:
    label, actual = find_parameter(part, key)
    op, expected = _parse_constraint(expression)
    derived_from_range = None
    if key.casefold() in {"input_voltage_min", "input_voltage_max"}:
        range_label, range_value = find_parameter(part, "input_voltage")
        values = parse_number_range(actual) or parse_number_range(range_value)
        if values:
            if range_label:
                label = range_label
            actual = min(values, key=lambda x: x.value) if key.casefold().endswith("_min") else max(values, key=lambda x: x.value)
            derived_from_range = range_value if range_value is not None else actual
    if actual is None:
        return {"key": key, "label": label, "expected": expression, "actual": None, "matched": False, "reason": "missing"}

    numeric = parse_number(expected)
    if numeric is not None and parse_number(actual) is not None:
        matched, reason = _numeric_match(actual, expected, op)
    else:
        a = str(actual).strip().casefold()
        e = str(expected).strip().casefold()
        if op == "!=":
            matched = e not in a
        else:
            matched = e == a or e in a
        reason = "text"
    actual_out: Any = actual.value if isinstance(actual, ParsedNumber) else actual
    result = {"key": key, "label": label, "expected": expression, "actual": actual_out, "matched": matched, "reason": reason}
    if derived_from_range is not None:
        result["derived_from_range"] = derived_from_range
    return result


def evaluate_constraints(part: Part, constraints: dict[str, str]) -> dict[str, Any]:
    results = [match_constraint(part, k, v) for k, v in constraints.items()]
    matched = sum(1 for x in results if x["matched"])
    return {
        "matched": matched,
        "total": len(results),
        "all_matched": matched == len(results),
        "details": results,
    }
