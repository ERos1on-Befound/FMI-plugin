"""Tests for the metric layer that turns statements into financial ratios."""

from __future__ import annotations

import pytest

from financial_intelligence.analysis.financial_ratios import (
    DIMENSION_ORDER,
    DIMENSIONS,
    METRIC_NAMES,
    METRIC_SPECS,
    QuoteContext,
    compute_metric,
    compute_metrics,
    compute_series,
    metric_catalog,
    resolve_metric_name,
    table_for_series,
)
from financial_intelligence.models.financial_statements import FinancialStatement

BASE_INCOME = {
    "revenue": 1000.0,
    "cost_of_revenue": 600.0,
    "gross_profit": 400.0,
    "operating_income": 200.0,
    "pretax_income": 180.0,
    "net_income": 150.0,
    "eps_basic": 3.0,
    "eps_diluted": 2.9,
    "interest_expense": 20.0,
    "income_tax_expense": 30.0,
    "shares_diluted": 50.0,
}
BASE_BALANCE = {
    "total_assets": 2000.0,
    "total_liabilities": 1200.0,
    "total_equity": 800.0,
    "current_assets": 600.0,
    "current_liabilities": 400.0,
    "inventory": 100.0,
    "prepayments": 20.0,
    "shares_outstanding": 50.0,
    "accounts_receivable": 120.0,
}
BASE_CASH = {
    "operating_cash_flow": 250.0,
    "capital_expenditure": 70.0,
    "dividends_paid": 25.0,
}


def make_statement(**overrides: object) -> FinancialStatement:
    data: dict[str, object] = {
        "symbol": "TEST",
        "company_name": "Test Co",
        "market": "US",
        "currency": "USD",
        "unit": "USD",
        "unit_scale": 1.0,
        "fiscal_period": "FY2025",
        "fiscal_year": 2025,
        "period_type": "annual",
        "period_start": "2024-01-01",
        "period_end": "2025-12-31",
        "filing_date": "2026-02-01",
        "source": "fixture",
        "income_statement": dict(BASE_INCOME),
        "balance_sheet": dict(BASE_BALANCE),
        "cash_flow_statement": dict(BASE_CASH),
    }
    data.update(overrides)
    return FinancialStatement(**data)  # type: ignore[arg-type]


def previous_year() -> FinancialStatement:
    return make_statement(
        fiscal_period="FY2024",
        fiscal_year=2024,
        income_statement={**BASE_INCOME, "revenue": 900.0, "net_income": 120.0, "eps_diluted": 2.4},
        balance_sheet={**BASE_BALANCE, "total_assets": 1800.0, "total_equity": 700.0, "inventory": 80.0,
                       "accounts_receivable": 100.0},
        cash_flow_statement={**BASE_CASH, "operating_cash_flow": 200.0},
    )


USD_QUOTE = QuoteContext(price=100.0, currency="USD", timestamp="2026-10-30T01:00:00+00:00", source="sina")


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("gross_margin", 40.0),
        ("operating_margin", 20.0),
        ("net_margin", 15.0),
        ("debt_ratio", 60.0),
        ("current_ratio", 1.5),
        ("quick_ratio", 1.2),
        ("interest_coverage", 10.0),
        ("pe", 100.0 / 2.9),
        ("pb", 100.0 / (800.0 / 50.0)),
        ("ps", (100.0 * 50.0) / 1000.0),
        ("dividend_yield", 0.5),
    ],
)
def test_single_period_metrics(metric: str, expected: float) -> None:
    value = compute_metric(metric, [make_statement()], 0, quote=USD_QUOTE)
    assert value.calculation_status == "ok", value.explanation
    assert value.value == pytest.approx(expected, rel=1e-6)


def test_average_balance_metrics_use_previous_period() -> None:
    statements = [make_statement(), previous_year()]
    roe = compute_metric("roe", statements, 0)
    # 150 / average(700, 800) = 20%
    assert roe.value == pytest.approx(20.0)
    assert "简化口径" not in roe.explanation

    roa = compute_metric("roa", statements, 0)
    # 150 / average(1800, 2000) = 7.8947368%
    assert roa.value == pytest.approx(150.0 / 1900.0 * 100.0, rel=1e-6)

    turnover = compute_metric("asset_turnover", statements, 0)
    assert turnover.value == pytest.approx(1000.0 / 1900.0, rel=1e-6)


def test_simplified_average_note_without_previous_period() -> None:
    roe = compute_metric("roe", [make_statement()], 0)
    assert roe.value == pytest.approx(150.0 / 800.0 * 100.0)
    assert "简化口径" in roe.explanation


def test_growth_needs_two_matching_periods() -> None:
    statements = [make_statement(), previous_year()]
    revenue_growth = compute_metric("revenue_growth", statements, 0)
    assert revenue_growth.value == pytest.approx((1000.0 - 900.0) / 900.0 * 100.0, rel=1e-6)

    no_prior = compute_metric("revenue_growth", [make_statement()], 0)
    assert no_prior.value is None
    assert no_prior.calculation_status == "insufficient_data"


def test_cash_flow_metrics_and_currency_unit() -> None:
    fcf = compute_metric("free_cash_flow", [make_statement()], 0)
    assert fcf.value == pytest.approx(180.0)
    assert fcf.unit == "USD"
    assert fcf.display == "180 USD"

    ratio = compute_metric("cfo_to_net_income", [make_statement()], 0)
    assert ratio.value == pytest.approx(250.0 / 150.0, rel=1e-6)


def test_negative_net_income_blocks_cfo_ratio() -> None:
    statement = make_statement(income_statement={**BASE_INCOME, "net_income": -50.0})
    ratio = compute_metric("cfo_to_net_income", [statement], 0)
    assert ratio.value is None
    assert ratio.calculation_status == "not_applicable"
    assert "不具备盈利质量含义" in ratio.explanation


def test_negative_eps_blocks_pe() -> None:
    statement = make_statement(income_statement={**BASE_INCOME, "eps_diluted": -1.5, "eps_basic": -1.5})
    pe = compute_metric("pe", [statement], 0, quote=USD_QUOTE)
    assert pe.value is None
    assert pe.calculation_status == "not_applicable"
    assert "PE 不适用" in pe.explanation


def test_valuation_requires_a_quote() -> None:
    pe = compute_metric("pe", [make_statement()], 0)
    assert pe.value is None
    assert pe.calculation_status == "not_applicable"
    assert "股价" in pe.explanation


def test_valuation_blocks_cross_currency() -> None:
    # Tencent reports in CNY but trades in HKD; no free FX conversion exists.
    statement = make_statement(currency="CNY")
    quote = QuoteContext(price=385.4, currency="HKD")
    pe = compute_metric("pe", [statement], 0, quote=quote)
    assert pe.value is None
    assert pe.calculation_status == "not_applicable"
    assert "币种" in pe.explanation
    assert "CNY" in pe.explanation and "HKD" in pe.explanation


def test_missing_field_is_insufficient_not_zero() -> None:
    statement = make_statement(income_statement={k: v for k, v in BASE_INCOME.items() if k != "cost_of_revenue"})
    gross = compute_metric("gross_margin", [statement], 0)
    assert gross.value is None
    assert gross.calculation_status == "insufficient_data"
    assert "cost_of_revenue" in gross.explanation


def test_interest_coverage_not_applicable_without_interest() -> None:
    statement = make_statement(income_statement={**BASE_INCOME, "interest_expense": 0.0})
    coverage = compute_metric("interest_coverage", [statement], 0)
    assert coverage.calculation_status == "not_applicable"


def test_provenance_fields_propagate() -> None:
    statement = make_statement()
    metric = compute_metric("net_margin", [statement], 0)
    assert metric.period == "FY2025"
    assert metric.period_type == "annual"
    assert metric.source == "fixture"
    assert metric.filing_date == "2026-02-01"
    assert metric.currency == "USD"
    assert metric.formula


def test_derived_flag_when_inputs_were_derived() -> None:
    statement = make_statement(derived_fields=["total_liabilities"])
    metric = compute_metric("debt_ratio", [statement], 0)
    assert metric.derived is True


def test_series_is_newest_first_with_trend() -> None:
    statements = [make_statement(), previous_year()]
    series = compute_series("net_margin", statements)
    assert [point.period for point in series.points] == ["FY2025", "FY2024"]
    # 15% vs 13.33% → rising
    assert series.trend == "rising"
    assert series.latest is not None and series.latest.period == "FY2025"


def test_series_trend_falling_and_stable() -> None:
    newer = make_statement(income_statement={**BASE_INCOME, "net_income": 50.0})
    falling = compute_series("net_margin", [newer, previous_year()])
    assert falling.trend == "falling"

    flat_prev = make_statement(fiscal_period="FY2024", income_statement=dict(BASE_INCOME))
    flat = compute_series("net_margin", [make_statement(), flat_prev])
    assert flat.trend == "stable"


def test_series_insufficient_for_single_period() -> None:
    series = compute_series("roe", [make_statement()])
    assert series.trend == "insufficient_data"
    assert "少于 2" in series.trend_note


def test_periods_cap_the_series() -> None:
    older = previous_year()
    oldest = make_statement(fiscal_period="FY2023", income_statement={**BASE_INCOME, "revenue": 700.0})
    series = compute_series("revenue_growth", [make_statement(), older, oldest], periods=2)
    assert len(series.points) == 2


def test_compute_metrics_skips_unknown_and_accepts_aliases() -> None:
    statements = [make_statement(), previous_year()]
    series = compute_metrics(statements, ["ROE", "pe_ratio", "does_not_exist"], quote=USD_QUOTE)
    assert [item.name for item in series] == ["roe", "pe"]


def test_resolve_metric_name() -> None:
    assert resolve_metric_name("ROE") == "roe"
    assert resolve_metric_name("Gross Margin") == "gross_margin"
    assert resolve_metric_name("pe_ratio") == "pe"
    assert resolve_metric_name("nonsense") is None


def test_metric_catalog_is_complete() -> None:
    catalog = metric_catalog()
    names = {entry["name"] for entry in catalog}
    assert names == set(METRIC_NAMES)
    for entry in catalog:
        assert entry["label"]
        assert entry["formula"]
        assert entry["dimension"] in DIMENSION_ORDER
        assert entry["category"] == DIMENSIONS[entry["dimension"]]
        assert isinstance(entry["inputs"], list)


def test_table_for_series() -> None:
    statements = [make_statement(), previous_year()]
    series = compute_series("net_margin", statements)
    table = table_for_series([series])
    assert table[0] == ["指标", "FY2025", "FY2024"]
    assert table[1][0] == "净利率"
    assert table[1][1] == "15.00%"
    assert table[1][2] == "13.33%"


def test_every_metric_produces_a_value_or_a_reason() -> None:
    statements = [make_statement(), previous_year()]
    for name in METRIC_NAMES:
        metric = compute_metric(name, statements, 0, quote=USD_QUOTE)
        if metric.calculation_status == "ok":
            assert metric.value is not None
        else:
            assert metric.explanation, f"{name} 失败了却没有说明原因"
            assert metric.value is None
