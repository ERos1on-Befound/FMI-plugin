"""Formula engine tests: arithmetic, edge cases and the safe expression sandbox."""

from __future__ import annotations

import math

import pytest

from financial_intelligence.analysis.financial_formulas import (
    FORMULAS,
    FormulaError,
    compute_expression,
    compute_formula,
    evaluate_expression,
    format_value,
    list_formulas,
    normalize_name,
)


def value_of(name: str, **inputs: object) -> float | None:
    result = compute_formula(name, inputs)
    assert result.calculation_status == "ok", result.explanation
    return result.value


# ---------------------------------------------------------------------------
# basic mathematics
# ---------------------------------------------------------------------------
def test_percentage() -> None:
    assert value_of("percentage", part=25, whole=200) == pytest.approx(12.5)


def test_percentage_zero_denominator() -> None:
    result = compute_formula("percentage", {"part": 1, "whole": 0})
    assert result.value is None
    assert result.calculation_status == "not_applicable"
    assert "整体值" in result.explanation


def test_percentage_change_and_yoy_agree() -> None:
    assert value_of("percentage_change", current=110, previous=100) == pytest.approx(10.0)
    assert value_of("yoy_growth", current=110, previous=100) == pytest.approx(10.0)
    assert value_of("qoq_growth", current=90, previous=100) == pytest.approx(-10.0)


def test_percentage_change_previous_zero() -> None:
    result = compute_formula("percentage_change", {"current": 10, "previous": 0})
    assert result.calculation_status == "not_applicable"


def test_percentage_change_negative_base_keeps_direction_note() -> None:
    result = compute_formula("percentage_change", {"current": -50, "previous": -100})
    assert result.value == pytest.approx(50.0)
    assert "上期值为负" in result.explanation


def test_cagr_three_years() -> None:
    # 100 → 121 over two years is exactly 10% a year.
    assert value_of("cagr", beginning=100, ending=121, years=2) == pytest.approx(10.0)


def test_cagr_rejects_negative_or_zero_base() -> None:
    negative = compute_formula("cagr", {"beginning": -100, "ending": 121, "years": 2})
    assert negative.calculation_status == "not_applicable"
    zero_years = compute_formula("cagr", {"beginning": 100, "ending": 121, "years": 0})
    assert zero_years.calculation_status == "not_applicable"


def test_compound_interest() -> None:
    assert value_of("compound_interest", present_value=1000, annual_rate=0.05, years=2) == pytest.approx(1102.5)


def test_compound_interest_rejects_rate_below_minus_one() -> None:
    result = compute_formula("compound_interest", {"present_value": 1000, "annual_rate": -1.5, "years": 2})
    assert result.calculation_status == "not_applicable"


def test_annualized_return() -> None:
    assert value_of("annualized_return", total_return=0.21, years=2) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# profitability
# ---------------------------------------------------------------------------
def test_gross_margin() -> None:
    assert value_of("gross_margin", revenue=100, cost_of_revenue=40) == pytest.approx(60.0)


def test_operating_and_net_margin() -> None:
    assert value_of("operating_margin", operating_income=25, revenue=100) == pytest.approx(25.0)
    assert value_of("net_margin", net_income=12, revenue=100) == pytest.approx(12.0)


def test_margin_revenue_zero() -> None:
    result = compute_formula("gross_margin", {"revenue": 0, "cost_of_revenue": 10})
    assert result.calculation_status == "not_applicable"


def test_roe_uses_average_equity() -> None:
    assert value_of("return_on_equity", net_income=10, equity_begin=80, equity_end=100) == pytest.approx(
        11.111111, rel=1e-6
    )


def test_roe_simplified_when_only_ending_equity() -> None:
    result = compute_formula("return_on_equity", {"net_income": 10, "equity_end": 90})
    assert result.calculation_status == "ok"
    assert result.value == pytest.approx(11.111111, rel=1e-6)
    assert "简化口径" in result.explanation


def test_roe_prefers_supplied_average() -> None:
    result = compute_formula("return_on_equity", {"net_income": 10, "equity_average": 50, "equity_end": 999})
    assert result.value == pytest.approx(20.0)


def test_roa_average_assets() -> None:
    assert value_of("return_on_assets", net_income=10, assets_begin=200, assets_end=300) == pytest.approx(4.0)


def test_roe_negative_equity_warns() -> None:
    result = compute_formula("return_on_equity", {"net_income": 10, "equity_end": -50})
    assert result.value is not None
    assert "资不抵债" in result.explanation


def test_eps_growth() -> None:
    assert value_of("eps_growth", eps_current=2.2, eps_previous=2.0) == pytest.approx(10.0)
    result = compute_formula("eps_growth", {"eps_current": 2.2, "eps_previous": 0})
    assert result.calculation_status == "not_applicable"


# ---------------------------------------------------------------------------
# solvency
# ---------------------------------------------------------------------------
def test_debt_ratio() -> None:
    assert value_of("debt_ratio", total_liabilities=60, total_assets=100) == pytest.approx(60.0)


def test_current_ratio() -> None:
    assert value_of("current_ratio", current_assets=200, current_liabilities=100) == pytest.approx(2.0)


def test_current_ratio_zero_liabilities() -> None:
    result = compute_formula("current_ratio", {"current_assets": 200, "current_liabilities": 0})
    assert result.calculation_status == "not_applicable"


def test_quick_ratio_with_and_without_prepayments() -> None:
    with_prepay = value_of("quick_ratio", current_assets=200, inventory=50, current_liabilities=100, prepayments=10)
    assert with_prepay == pytest.approx(1.4)
    result = compute_formula("quick_ratio", {"current_assets": 200, "inventory": 50, "current_liabilities": 100})
    assert result.value == pytest.approx(1.5)
    assert "简化口径" in result.explanation


def test_interest_coverage() -> None:
    assert value_of("interest_coverage", ebit=120, interest_expense=20) == pytest.approx(6.0)


@pytest.mark.parametrize("interest", [0, -5])
def test_interest_coverage_not_applicable(interest: float) -> None:
    result = compute_formula("interest_coverage", {"ebit": 120, "interest_expense": interest})
    assert result.calculation_status == "not_applicable"


# ---------------------------------------------------------------------------
# efficiency
# ---------------------------------------------------------------------------
def test_asset_turnover() -> None:
    assert value_of("asset_turnover", revenue=500, assets_begin=200, assets_end=300) == pytest.approx(2.0)


def test_receivables_turnover() -> None:
    assert value_of("receivables_turnover", revenue=500, receivables_begin=40, receivables_end=60) == pytest.approx(
        10.0
    )


def test_inventory_turnover() -> None:
    assert value_of("inventory_turnover", cost_of_revenue=300, inventory_begin=40, inventory_end=60) == pytest.approx(
        6.0
    )


def test_turnover_missing_balance() -> None:
    result = compute_formula("asset_turnover", {"revenue": 500})
    assert result.calculation_status == "insufficient_data"
    assert "assets" in result.explanation


# ---------------------------------------------------------------------------
# cash flow
# ---------------------------------------------------------------------------
def test_free_cash_flow_normalises_capex_sign() -> None:
    assert value_of("free_cash_flow", operating_cash_flow=100, capital_expenditure=30) == pytest.approx(70.0)
    assert value_of("free_cash_flow", operating_cash_flow=100, capital_expenditure=-30) == pytest.approx(70.0)


def test_cfo_to_net_income() -> None:
    assert value_of("cfo_to_net_income", operating_cash_flow=100, net_income=50) == pytest.approx(2.0)


@pytest.mark.parametrize("net_income", [0, -10])
def test_cfo_to_net_income_not_applicable_for_non_positive_profit(net_income: float) -> None:
    result = compute_formula("cfo_to_net_income", {"operating_cash_flow": 100, "net_income": net_income})
    assert result.calculation_status == "not_applicable"
    assert "不具备盈利质量含义" in result.explanation


# ---------------------------------------------------------------------------
# valuation
# ---------------------------------------------------------------------------
def test_pe_ratio() -> None:
    assert value_of("pe_ratio", price=100, eps=5) == pytest.approx(20.0)
    assert value_of("pe", price=100, eps=5) == pytest.approx(20.0)
    assert value_of("pe_ratio", price=100, eps=5) == pytest.approx(20.0)


def test_pe_negative_eps_is_not_applicable() -> None:
    result = compute_formula("pe_ratio", {"price": 100, "eps": -2})
    assert result.value is None
    assert result.calculation_status == "not_applicable"
    assert "亏损" in result.explanation
    assert "PE 不适用" in result.explanation


def test_pb_and_ps() -> None:
    assert value_of("pb_ratio", price=100, book_value_per_share=25) == pytest.approx(4.0)
    assert value_of("ps_ratio", market_cap=1000, revenue=200) == pytest.approx(5.0)


def test_pb_negative_book_value() -> None:
    result = compute_formula("pb_ratio", {"price": 100, "book_value_per_share": -3})
    assert result.calculation_status == "not_applicable"


def test_dividend_yield() -> None:
    assert value_of("dividend_yield", dividend_per_share=2, price=100) == pytest.approx(2.0)


def test_market_cap() -> None:
    assert value_of("market_cap", price=10, shares_outstanding=100) == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# plumbing: names, units, coercion, unknown formulas
# ---------------------------------------------------------------------------
def test_normalize_name() -> None:
    assert normalize_name(" Gross Margin ") == "gross_margin"
    assert normalize_name("Free-Cash Flow") == "free_cash_flow"
    assert normalize_name("CAGR") == "cagr"


def test_unknown_formula_lists_available_ones() -> None:
    result = compute_formula("ntm_dcf_valuation", {"x": 1})
    assert result.calculation_status == "unavailable"
    assert result.value is None
    assert "gross_margin" in result.explanation
    assert "custom_expression" in result.explanation


def test_missing_input_is_insufficient_data_not_zero() -> None:
    result = compute_formula("gross_margin", {"revenue": 100})
    assert result.value is None
    assert result.calculation_status == "insufficient_data"
    assert "cost_of_revenue" in result.explanation


def test_non_numeric_input_reported() -> None:
    result = compute_formula("gross_margin", {"revenue": "abc", "cost_of_revenue": 40})
    assert result.calculation_status == "insufficient_data"
    assert "不是数值" in result.explanation


def test_numeric_strings_accepted() -> None:
    result = compute_formula("gross_margin", {"revenue": "100", "cost_of_revenue": "40"})
    assert result.value == pytest.approx(60.0)


def test_unknown_input_field_reported() -> None:
    result = compute_formula("gross_margin", {"revenue": 100, "cost_of_revenue": 40, "nonsense": 1})
    assert result.calculation_status == "insufficient_data"
    assert "未识别的输入字段" in result.explanation


def test_display_formatting() -> None:
    result = compute_formula("gross_margin", {"revenue": 100, "cost_of_revenue": 40})
    assert result.display == "60.00%"
    assert result.unit == "%"
    assert result.formula_version.startswith("fmi-formulas/")


def test_results_carry_expression_and_inputs() -> None:
    result = compute_formula("free_cash_flow", {"operating_cash_flow": 100, "capital_expenditure": 30})
    assert result.expression == "FCF = operating_cash_flow - |capital_expenditure|"
    assert result.inputs == {"operating_cash_flow": 100.0, "capital_expenditure": 30.0}
    assert any("绝对值" in note for note in result.notes)


def test_registry_is_populated_and_unique() -> None:
    names = [spec.name for spec in list_formulas()]
    assert len(names) == len(set(names))
    assert {"gross_margin", "cagr", "free_cash_flow", "pe_ratio"}.issubset(set(names))
    assert len(names) == len(FORMULAS) >= 25
    for spec in FORMULAS.values():
        assert spec.expression
        assert spec.inputs
        assert spec.category


def test_format_value_helper() -> None:
    assert format_value(None, "%") == "不可计算"
    assert format_value(12.345, "%") == "12.35%"
    assert format_value(2, "x") == "2.00x"
    assert format_value(1234.5, "USD") == "1,234 USD"


# ---------------------------------------------------------------------------
# safe expression engine
# ---------------------------------------------------------------------------
def test_expression_arithmetic() -> None:
    assert evaluate_expression("(a + b) / c", {"a": 1, "b": 2, "c": 3}) == pytest.approx(1.0)
    assert evaluate_expression("2 ** 3 + 1", {}) == pytest.approx(9.0)
    assert evaluate_expression("-a", {"a": 4}) == pytest.approx(-4.0)


def test_expression_functions() -> None:
    assert evaluate_expression("sqrt(a)", {"a": 9}) == pytest.approx(3.0)
    assert evaluate_expression("max(a, b)", {"a": 1, "b": 5}) == pytest.approx(5.0)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "a.b",
        "items[0]",
        "[x for x in (1, 2)]",
        "lambda x: x",
        "'text' + 'more'",
        "a if a else b",
        "a.__class__",
    ],
)
def test_expression_rejects_unsafe_syntax(expression: str) -> None:
    with pytest.raises(FormulaError):
        evaluate_expression(expression, {"a": 1, "b": 2, "items": [1]})


def test_expression_unknown_variable() -> None:
    with pytest.raises(FormulaError) as excinfo:
        evaluate_expression("a + missing", {"a": 1})
    assert "missing" in str(excinfo.value)


def test_expression_division_by_zero() -> None:
    with pytest.raises(FormulaError) as excinfo:
        evaluate_expression("a / b", {"a": 1, "b": 0})
    assert "除以零" in str(excinfo.value)


def test_expression_rejects_empty_and_too_long() -> None:
    with pytest.raises(FormulaError):
        evaluate_expression("   ", {})
    with pytest.raises(FormulaError):
        evaluate_expression("1 + " * 300 + "1", {})


def test_custom_expression_result_wrapper() -> None:
    result = compute_expression("(revenue - cost) / revenue", {"revenue": 100, "cost": 40})
    assert result.calculation_status == "ok"
    assert result.value == pytest.approx(0.6)
    assert result.formula_name == "custom_expression"


def test_custom_expression_failure_is_reported_not_raised() -> None:
    result = compute_expression("__import__('os')", {})
    assert result.calculation_status == "not_applicable"
    assert result.value is None
    assert "不允许" in result.explanation


def test_custom_expression_with_empty_variable() -> None:
    result = compute_expression("a / b", {"a": 1, "b": None})
    assert result.calculation_status == "not_applicable"
    assert "b" in result.explanation


def test_cagr_matches_manual_math() -> None:
    beginning, ending, years = 100.0, 250.0, 4.0
    expected = ((ending / beginning) ** (1 / years) - 1) * 100
    assert value_of("cagr", beginning=beginning, ending=ending, years=years) == pytest.approx(expected)
    assert math.isclose(expected, 25.743, rel_tol=1e-3)
