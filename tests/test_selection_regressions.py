"""Selection must not claim matches from digits, missing units or stale filters."""
import math
from unittest.mock import Mock

import pytest

from szlcsc_mcp.cache import SqliteCache
from szlcsc_mcp.client import SZLCSCClient, SZLCSCError
from szlcsc_mcp.jlcpcb import JLCPCBClient
from szlcsc_mcp.models import Part
from szlcsc_mcp.parametric import evaluate_constraints, parse_number, parse_number_range


@pytest.mark.parametrize("text,value,unit", [
    (".5V", 0.5, "v"),
    ("1e-3V", 0.001, "v"),
    ("2.2E+3ohms", 2200, "ohm"),
    ("100μA", 0.0001, "a"),
])
def test_scalar_engineering_notation(text, value, unit):
    parsed = parse_number(text)
    assert parsed is not None
    assert parsed.value == pytest.approx(value, rel=1e-12, abs=0)
    assert parsed.unit == unit


@pytest.mark.parametrize("value", ["SOT-23-5", "1V~5V", "3.3V/5V", "1e999V", math.inf, math.nan])
def test_labels_ranges_and_nonfinite_values_are_not_scalars(value):
    assert parse_number(value) is None


@pytest.mark.parametrize("text,expected", [
    ("1~5mV", (0.001, 0.005)),
    ("1mV~5", (0.001, 0.005)),
    ("1k~5kΩ", (1000, 5000)),
    ("1V~5mV", (1, 0.005)),
])
def test_range_inherits_the_full_unit_without_double_scaling(text, expected):
    parsed = parse_number_range(text)
    assert len(parsed) == 2
    assert [p.value for p in parsed] == pytest.approx(expected, rel=1e-12, abs=0)


@pytest.mark.parametrize("actual,expected,matched", [
    ("1pF", "=2pF", False),
    ("1pF", "<=0F", False),
    ("100nF", "=0.1uF", True),
    ("1V~5V", "<=5V", False),
])
def test_numeric_comparisons_do_not_invent_matches(actual, expected, matched):
    part = Part(parameters={"capacitance": actual})
    assert evaluate_constraints(part, {"capacitance": expected})["all_matched"] is matched


def test_package_pin_counts_are_text_not_equal_numbers():
    part = Part(package="SOT-23-5")
    assert not evaluate_constraints(part, {"package": "SOT-23-3"})["all_matched"]
    assert evaluate_constraints(part, {"package": "!=SOT-23-3"})["all_matched"]
    assert evaluate_constraints(part, {"package": "SOT-23"})["all_matched"]
    assert evaluate_constraints(Part(package="0603(1608)"), {"package": "0603"})["all_matched"]


@pytest.mark.parametrize("actual,expected", [("SOIC-80", "SOIC-8"), ("TSOT-23-3", "SOT-23-3")])
def test_package_family_matching_respects_token_boundaries(actual, expected):
    assert not evaluate_constraints(Part(package=actual), {"package": expected})["all_matched"]
    assert not SZLCSCClient._matches_filters(Part(package=actual), package=expected)


def test_input_voltage_range_and_explicit_limits_keep_correct_precedence():
    part = Part(parameters={"输入电压": "1~5mV"})
    assert evaluate_constraints(part, {"input_voltage_min": "<=2mV"})["all_matched"]
    part = Part(parameters={"最小输入电压": "2V", "输入电压": "3V~7V"})
    assert evaluate_constraints(part, {"input_voltage_min": "=2V"})["all_matched"]


@pytest.mark.parametrize("filters,updated", [
    ({"in_stock": True}, {"stock": 0}),
    ({"in_stock": False}, {"stock": 20}),
    ({"max_price": 5}, {"small_batch_price_cny": 10}),
    ({"max_price": 5}, {"small_batch_price_cny": None}),
    ({"package": "SOT-23-3"}, {"package": "SOT-23-5"}),
    ({"brand": "ACME"}, {"brand": "Other"}),
])
def test_parametric_search_rechecks_filters_after_detail_enrichment(tmp_path, filters, updated):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    initial = dict(lcsc_code="C1", stock=10, small_batch_price_cny=1, package="SOT-23-3", brand="ACME")
    if filters.get("in_stock") is False:
        initial["stock"] = 0
    client.search = Mock(return_value=[Part(**initial)])
    client.get_part = Mock(return_value=Part(**{**initial, **updated}))
    try:
        assert client.parametric_search("reference", {"output_voltage": "3.3V"}, **filters) == []
    finally:
        client.close()


def test_failed_parametric_detail_is_visible_in_candidate_warnings(tmp_path):
    client = SZLCSCClient(cache=SqliteCache(tmp_path / "cache.db"), request_delay=0)
    client.search = Mock(return_value=[Part(lcsc_code="C1", stock=10)])
    client.get_part = Mock(side_effect=SZLCSCError("timeout", code="NETWORK_ERROR"))
    try:
        rows = client.parametric_search("reference", {"output_voltage": "3.3V"})
        assert "NETWORK_ERROR" in " ".join(rows[0]["part"].warnings)
        assert not rows[0]["match"]["all_matched"]
    finally:
        client.close()


def test_assembly_package_filter_rejects_wrong_or_unknown_packages(tmp_path):
    client = JLCPCBClient(SqliteCache(tmp_path / "cache.db"), delay=0)
    client._request = Mock(return_value=({"componentPageInfo": {"list": [
        {"componentCode": "C1", "componentSpecificationEn": "0805"},
        {"componentCode": "C2", "componentSpecificationEn": "0603(1608)"},
        {"componentCode": "C3"},
    ], "total": 3}}, {}))
    try:
        result = client.search("capacitor", package="0603")
        assert [p["lcsc_code"] for p in result["results"]] == ["C2"]
    finally:
        client.close()
