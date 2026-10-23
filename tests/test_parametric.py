from szlcsc_mcp.models import Part
from szlcsc_mcp.parametric import evaluate_constraints, parse_number


def test_engineering_value_parser():
    assert parse_number("200mA").value == 0.2
    assert parse_number("10kΩ").value == 10000
    assert parse_number("0.2%").value == 0.2
    assert parse_number("20ppm/℃").value == 20


def test_parametric_aliases_and_comparisons():
    p = Part(
        package="SOT-23-3",
        parameters={"输出电压": "3.3V", "初始精度": "±0.15%", "温漂": "20ppm/℃"},
    )
    report = evaluate_constraints(
        p,
        {"output_voltage": "=3.3V", "accuracy": "<=0.2%", "tempco": "<=20ppm/°C", "package": "SOT-23"},
    )
    assert report["all_matched"] is True
    assert report["matched"] == 4


def test_input_voltage_range_derives_min_and_max():
    p = Part(parameters={"输入电压": "2.5V~5.5V"})
    report = evaluate_constraints(p, {"input_voltage_min": "<=3V", "input_voltage_max": ">=5V"})
    assert report["all_matched"] is True
    assert all("derived_from_range" in x for x in report["details"])
