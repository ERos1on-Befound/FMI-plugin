"""Tests for the canonical financial model and the fundamental service.

Everything here is offline: providers are faked, so the assertions pin down the
mapping rules, the multi-period logic and the comparability checks instead of
depending on a live data source.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pytest

from financial_intelligence.models.financial_statements import (
    CANONICAL_FIELD_DOCS,
    FIELD_ALIASES,
    FIELD_HOME,
    FinancialStatement,
    FinancialStatements,
    _to_float,
    canonicalize,
    derive_balance,
    derive_income,
    fiscal_label,
    normalise_capex,
)
from financial_intelligence.providers.base import FinancialDataProvider, ProviderError
from financial_intelligence.schemas import AssetType, DataStatus, Market, Quote, iso, utc_now
from financial_intelligence.services import CalculationService, FundamentalService, TechnicalService
from financial_intelligence.utils import InputError
from financial_intelligence.utils.symbol_mapper import SymbolSpec


def spec_for(symbol: str = "AAPL", market: Market = Market.US) -> SymbolSpec:
    return SymbolSpec(
        original=symbol,
        symbol=symbol,
        market=market,
        asset_type=AssetType.STOCK,
        currency="USD" if market is Market.US else "CNY",
        yahoo_symbol=symbol,
        akshare_code=None,
        akshare_exchange=None,
        name_hint=None,
    )


def make_statement(
    *,
    fiscal_period: str = "FY2025",
    period_end: date = date(2025, 9, 27),
    period_start: date | None = date(2024, 9, 29),
    filing_date: date | None = date(2025, 10, 31),
    period_type: str = "annual",
    symbol: str = "AAPL",
    market: Market = Market.US,
    currency: str = "USD",
    company_name: str | None = "Apple Inc.",
    revenue: float | None = 1000.0,
    cost_of_revenue: float | None = 600.0,
    gross_profit: float | None = None,
    operating_income: float | None = 200.0,
    net_income: float | None = 100.0,
    eps_basic: float | None = 1.0,
    eps_diluted: float | None = 0.98,
    total_assets: float | None = 5000.0,
    total_liabilities: float | None = 3000.0,
    total_equity: float | None = 2000.0,
    current_assets: float | None = 1500.0,
    current_liabilities: float | None = 1000.0,
    inventory: float | None = 300.0,
    cash: float | None = 400.0,
    accounts_receivable: float | None = 200.0,
    short_term_debt: float | None = 100.0,
    long_term_debt: float | None = 400.0,
    shares_outstanding: float | None = 100.0,
    operating_cash_flow: float | None = 300.0,
    capex: float | None = 100.0,
    **overrides: object,
) -> FinancialStatement:
    values: dict[str, object] = {
        "symbol": symbol,
        "company_name": company_name,
        "market": market,
        "currency": currency,
        "unit": "currency",
        "unit_scale": 1.0,
        "fiscal_period": fiscal_period,
        "period_type": period_type,
        "period_start": period_start,
        "period_end": period_end,
        "filing_date": filing_date,
        "source": "fake",
        "income_statement": {
            "revenue": revenue,
            "cost_of_revenue": cost_of_revenue,
            "gross_profit": gross_profit,
            "operating_income": operating_income,
            "net_income": net_income,
            "eps_basic": eps_basic,
            "eps_diluted": eps_diluted,
        },
        "balance_sheet": {
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "inventory": inventory,
            "cash": cash,
            "accounts_receivable": accounts_receivable,
            "short_term_debt": short_term_debt,
            "long_term_debt": long_term_debt,
            "shares_outstanding": shares_outstanding,
        },
        "cash_flow_statement": {
            "operating_cash_flow": operating_cash_flow,
            "capital_expenditure": capex,
        },
        "raw_items": {},
        "matched_items": {},
        "derived_fields": [],
        "missing_fields": [],
        "retrieved_at": iso(utc_now()),
        "notes": [],
    }
    values.update(overrides)
    return FinancialStatement(**values)  # type: ignore[arg-type]


class FakeFinancialProvider(FinancialDataProvider):
    """Returns canned statements so the service layer can be tested offline."""

    def __init__(
        self,
        name: str = "fake",
        statements: Sequence[FinancialStatement] | None = None,
        error: ProviderError | None = None,
        supported: bool = True,
        by_symbol: dict[str, list[FinancialStatement]] | None = None,
    ) -> None:
        self.name = name
        self._statements = list(statements or [])
        self._error = error
        self._supported = supported
        self._by_symbol = by_symbol or {}
        self.calls = 0

    def supports_financials(self, spec: SymbolSpec) -> bool:
        return self._supported

    def get_financials(
        self,
        spec: SymbolSpec,
        *,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[FinancialStatement]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._by_symbol:
            return self._by_symbol.get(spec.symbol, [])[:limit]
        return self._statements[:limit]


class FakeMarket:
    """Minimal stand-in for :class:`MarketService` (no network)."""

    def __init__(self, price: float | None = 100.0, currency: str = "USD", name: str | None = None) -> None:
        self.price = price
        self.currency = currency
        self.name = name

    def get_quote(self, symbol: str, **_kwargs: object) -> Quote:
        return Quote(
            symbol=symbol,
            name=self.name,
            market=Market.US,
            asset_type=AssetType.STOCK,
            currency=self.currency,
            price=self.price,
            previous_close=90.0,
            timestamp="2026-10-29T20:00:00+00:00",
            retrieved_at=iso(utc_now()),
            source="fake",
            data_status=DataStatus.DELAYED,
        )


def build_service(
    *statements: FinancialStatement,
    provider_name: str = "fake",
    market: FakeMarket | None = None,
) -> tuple[FundamentalService, FakeFinancialProvider]:
    provider = FakeFinancialProvider(provider_name, statements)
    service = FundamentalService(providers=[provider], market=market or FakeMarket())
    return service, provider


# --------------------------------------------------------------- canonical model
def test_canonicalize_sec_aliases_picks_semantic_items() -> None:
    raw = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": 1000.0,
        "CostOfGoodsAndServicesSold": 600.0,
        "Revenues": 62_900_000_000.0,
    }
    result = canonicalize("income", raw, "sec")
    assert result.values["revenue"] == 1000.0
    assert result.values["cost_of_revenue"] == 600.0
    assert result.matched_items["revenue"].startswith("RevenueFromContract")


def test_canonicalize_cn_sina_aliases() -> None:
    raw = {"营业收入": 500.0, "营业成本": 300.0, "归属于母公司所有者的净利润": 120.0}
    result = canonicalize("income", raw, "cn_sina")
    assert result.values["revenue"] == 500.0
    assert result.values["cost_of_revenue"] == 300.0
    assert result.values["net_income_to_parent"] == 120.0


def test_canonicalize_hk_em_aliases() -> None:
    raw = {"营业额": 800.0, "毛利": 400.0, "股东应占溢利": 200.0}
    result = canonicalize("income", raw, "hk_em")
    assert result.values["revenue"] == 800.0
    assert result.values["gross_profit"] == 400.0
    assert result.values["net_income_to_parent"] == 200.0


def test_canonicalize_reports_missing_fields_without_zero_filling() -> None:
    result = canonicalize("income", {"营业总收入": 10.0}, "cn_sina")
    assert result.values["revenue"] == 10.0
    assert result.values["operating_income"] is None
    assert "operating_income" in result.missing_fields


def test_canonicalize_ignores_unrelated_source() -> None:
    result = canonicalize("income", {"营业收入": 5.0}, "sec")
    assert result.values["revenue"] is None


#: Fields genuinely absent from a source — never mapped by force into a different meaning.
EXPECTED_ABSENT = {
    ("hk_em", "income", "rd_expense"),
    ("cn_sina", "income", "shares_basic"),
    ("hk_em", "income", "shares_basic"),
    ("cn_sina", "income", "shares_diluted"),
    ("hk_em", "income", "shares_diluted"),
    ("sec", "balance", "total_debt"),
    ("cn_sina", "balance", "total_debt"),
    ("hk_em", "balance", "total_debt"),
    ("cn_sina", "balance", "shares_outstanding"),
    ("hk_em", "balance", "shares_outstanding"),
    ("cn_sina", "cashflow", "depreciation_amortization"),
}


def test_field_aliases_cover_every_documented_field() -> None:
    for source, aliases in FIELD_ALIASES.items():
        for kind, fields in FIELD_HOME.items():
            for field in fields:
                if (source, kind, field) in EXPECTED_ABSENT:
                    assert field not in aliases, f"{source} 不应硬映射 {kind}.{field}"
                    continue
                assert field in aliases, f"{source} 缺少 {kind}.{field} 的别名"


def test_expected_absent_entries_really_are_absent() -> None:
    for source, kind, field in EXPECTED_ABSENT:
        assert field not in FIELD_ALIASES[source]


def test_derive_income_computes_gross_profit() -> None:
    values = {"revenue": 100.0, "cost_of_revenue": 40.0}
    derived = derive_income(values)
    assert values["gross_profit"] == 60.0
    assert any(item.startswith("gross_profit") for item in derived)


def test_derive_income_computes_cost_when_only_gross_profit_known() -> None:
    values = {"revenue": 100.0, "gross_profit": 55.0}
    derived = derive_income(values)
    assert values["cost_of_revenue"] == 45.0
    assert any(item.startswith("cost_of_revenue") for item in derived)


def test_derive_balance_derives_equity_and_debt() -> None:
    values = {
        "total_assets": 1000.0,
        "total_liabilities": 400.0,
        "short_term_debt": 50.0,
        "long_term_debt": 150.0,
    }
    derived = derive_balance(values)
    assert values["total_equity"] == 600.0
    assert values["total_debt"] == 200.0
    assert any(item.startswith("total_equity") for item in derived)
    assert any(item.startswith("total_debt") for item in derived)


def test_derive_balance_keeps_original_when_present() -> None:
    values = {"total_assets": 1000.0, "total_liabilities": 400.0, "total_equity": 555.0}
    derived = derive_balance(values)
    assert values["total_equity"] == 555.0
    assert "total_equity" not in derived


def test_normalise_capex_uses_absolute_spending_value() -> None:
    assert normalise_capex(-123.0) == 123.0
    assert normalise_capex(123.0) == 123.0
    assert normalise_capex(None) is None


def test_to_float_handles_placeholders_and_specials() -> None:
    assert _to_float("123.5") == 123.5
    assert _to_float("-") is None
    assert _to_float("--") is None
    assert _to_float("") is None
    assert _to_float(float("nan")) is None
    assert _to_float(float("inf")) is None


def test_fiscal_label_shapes() -> None:
    assert fiscal_label(date(2025, 9, 27), "annual") == "FY2025"
    assert fiscal_label(date(2026, 6, 27), "quarterly") == "2026Q2"
    assert fiscal_label(date(2026, 6, 27), "ttm") == "TTM2026-06"


def test_statement_scaled_applies_unit_scale() -> None:
    statement = make_statement(unit_scale=1000.0)
    assert statement.scaled("revenue") == 1_000_000.0
    assert statement.scaled("operating_income") == 200_000.0
    assert statement.scaled("inventory") == 300_000.0


def test_canonical_docs_explain_every_field() -> None:
    documented = {field for fields in FIELD_HOME.values() for field in fields}
    assert set(CANONICAL_FIELD_DOCS) == documented
    for field, text in CANONICAL_FIELD_DOCS.items():
        assert text.strip(), field


# --------------------------------------------------------------------- service
def test_get_financials_returns_normalised_payload() -> None:
    service, provider = build_service(make_statement(), make_statement(fiscal_period="FY2024"))
    result = service.get_financials("AAPL", period_type="annual", limit=2)
    assert isinstance(result, FinancialStatements)
    assert result.symbol == "AAPL"
    assert result.currency == "USD"
    assert result.data_status is DataStatus.HISTORICAL
    assert [item.fiscal_period for item in result.statements] == ["FY2025", "FY2024"]
    assert result.company_name == "Apple Inc."
    assert provider.calls == 1


def test_get_financials_uses_cache_on_second_call() -> None:
    service, provider = build_service(make_statement())
    service.get_financials("AAPL")
    service.get_financials("AAPL")
    assert provider.calls == 1


def test_get_financials_falls_back_to_next_provider() -> None:
    failing = FakeFinancialProvider(
        "sec",
        error=ProviderError("sec", "boom", kind="http", symbol="AAPL"),
    )
    working = FakeFinancialProvider("akshare", [make_statement(symbol="600519.SS", market=Market.CN)])
    service = FundamentalService(providers=[failing, working], market=FakeMarket())
    result = service.get_financials("600519.SS")
    assert result.symbol == "600519.SS"
    assert result.statements and result.statements[0].market is Market.CN
    assert working.calls == 1


def test_get_financials_all_providers_failing_returns_unavailable() -> None:
    failing = FakeFinancialProvider("sec", error=ProviderError("sec", "boom", kind="http"))
    service = FundamentalService(providers=[failing], market=FakeMarket())
    result = service.get_financials("AAPL")
    assert result.data_status is DataStatus.UNAVAILABLE
    assert result.statements == []
    assert result.error and "boom" in result.error


def test_ttm_period_type_is_rejected_with_reason() -> None:
    service, _ = build_service(make_statement())
    with pytest.raises(InputError) as excinfo:
        service.get_financials("AAPL", period_type="ttm")
    assert "TTM" in str(excinfo.value)


def test_financials_reject_non_stock_assets() -> None:
    service, _ = build_service(make_statement())
    with pytest.raises(InputError):
        service.get_financials("^GSPC")


def test_company_name_falls_back_to_quote() -> None:
    service, _ = build_service(make_statement(company_name=None), market=FakeMarket(name="Apple Inc."))
    result = service.get_financials("AAPL")
    assert result.company_name == "Apple Inc."


def test_compute_metrics_defaults_to_core_metrics() -> None:
    service, _ = build_service(make_statement())
    result = service.compute_metrics("AAPL")
    names = [item.name for item in result.metrics]
    assert "gross_margin" in names
    assert "roe" in names
    assert len(names) <= 6


def test_compute_metrics_reports_unknown_metric() -> None:
    service, _ = build_service(make_statement())
    result = service.compute_metrics("AAPL", metrics=["gross_margin", "totally_unknown"])
    assert any("totally_unknown" in note for note in result.notes)


def test_analyze_full_scope_builds_six_dimensions() -> None:
    service, _ = build_service(make_statement(), make_statement(fiscal_period="FY2024", period_end=date(2024, 9, 28)))
    result = service.analyze("AAPL", scope="full", periods=2)
    dimensions = {item.dimension: item for item in result.dimensions}
    assert set(dimensions) == {"profitability", "solvency", "efficiency", "growth", "cash_flow", "valuation"}
    assert result.observations
    assert any("行业基准" in note for note in result.limitations)


def test_analyze_single_scope_filters_dimensions() -> None:
    service, _ = build_service(make_statement())
    result = service.analyze("AAPL", scope="profitability")
    assert [item.dimension for item in result.dimensions] == ["profitability"]


def test_analyze_flags_valuation_as_not_applicable_on_currency_mismatch() -> None:
    statement = make_statement(currency="CNY", symbol="0700.HK", market=Market.HK)
    service, _ = build_service(statement, market=FakeMarket(price=400.0, currency="HKD"))
    result = service.analyze("0700.HK", scope="valuation")
    statuses = {finding.status for finding in result.dimensions[0].findings}
    assert "not_applicable" in statuses or result.valuation == []
    assert any("币种" in note for note in result.limitations)


def test_compare_marks_comparable_when_period_and_currency_match() -> None:
    service, _ = build_service(make_statement(symbol="AAPL"))
    service._providers = [
        FakeFinancialProvider("fake", [make_statement(symbol="AAPL")]),
    ]
    result = service.compare(["AAPL"], metrics=["gross_margin"])
    assert result.rows and result.rows[0].comparable is True


def test_compare_flags_mismatched_fiscal_periods() -> None:
    provider = FakeFinancialProvider(
        "fake",
        by_symbol={
            "AAPL": [make_statement(symbol="AAPL")],
            "MSFT": [make_statement(symbol="MSFT", fiscal_period="FY2026", period_end=date(2026, 6, 30))],
        },
    )
    service = FundamentalService(providers=[provider], market=FakeMarket())
    result = service.compare(["AAPL", "MSFT"], metrics=["gross_margin"])
    assert len(result.rows) == 1
    assert result.rows[0].comparable is False
    assert any("报告期" in note for note in result.comparability_notes)
    assert len(result.rows[0].values) == 2


def test_compare_accepts_comma_separated_string() -> None:
    service, _ = build_service(make_statement(symbol="AAPL"))
    service._providers = [FakeFinancialProvider("fake", [make_statement(symbol="AAPL")])]
    result = service.compare("AAPL, MSFT", metrics="gross_margin")
    assert [row.metric for row in result.rows] == ["gross_margin"]


def test_compare_without_symbols_raises() -> None:
    service, _ = build_service(make_statement())
    with pytest.raises(InputError):
        service.compare([])


# ------------------------------------------------------------- service catalog
def test_calculation_service_supported_lists_all_layers() -> None:
    supported = CalculationService().supported()
    assert len(supported.formulas) >= 25
    assert len(supported.metrics) == 21
    assert len(supported.technical) == 6
    names = {item["name"] for item in supported.data_sources}
    assert {"sina", "akshare", "sec"} <= names


def test_calculation_service_custom_expression_is_safe() -> None:
    service = CalculationService()
    ok = service.calculate("custom_expression", {"expression": "(a - b) / b * 100", "variables": {"a": 120, "b": 100}})
    assert ok.calculation_status == "ok"
    assert ok.value == pytest.approx(20.0)
    bad = service.calculate("custom_expression", {"expression": "__import__('os').system('echo hi')"})
    assert bad.calculation_status != "ok"
    assert bad.value is None


def test_technical_service_requires_real_history(monkeypatch: pytest.MonkeyPatch) -> None:
    service = TechnicalService()
    from financial_intelligence.schemas import HistoricalData, Market

    def fake_history(symbol: str, period: str = "6mo", interval: str = "1d") -> HistoricalData:
        return HistoricalData(
            symbol="AAPL",
            market=Market.US,
            asset_type=AssetType.STOCK,
            period=period,
            interval=interval,
            source="fake",
            data_status=DataStatus.UNAVAILABLE,
            retrieved_at=iso(utc_now()),
            bars=[],
            error="no data",
        )

    monkeypatch.setattr(service._market, "get_historical_data", fake_history)
    result = service.get_indicators("AAPL", period="6mo")
    assert result.data_status is DataStatus.UNAVAILABLE
    assert result.indicators == []
    assert any("不构成买卖建议" in note for note in result.notes)
