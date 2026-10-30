"""MCP-level tests for the eight phase-2 tools (in-memory client, no network).

The service singletons behind the tools are replaced with offline fakes, so these
tests pin down the MCP surface itself: discovery, input schemas, structured
output, error handling and tool isolation.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from mcp import Client

from financial_intelligence import server
from financial_intelligence.models.financial_statements import FinancialStatement
from financial_intelligence.providers.base import FinancialDataProvider
from financial_intelligence.schemas import (
    AssetType,
    DataStatus,
    HistoricalBar,
    HistoricalData,
    Market,
    Quote,
    iso,
    utc_now,
)
from financial_intelligence.services import CalculationService, FundamentalService, TechnicalService
from financial_intelligence.utils.symbol_mapper import SymbolSpec

pytestmark = pytest.mark.anyio


def make_statement(symbol: str = "AAPL", *, fiscal_period: str = "FY2025", period_end: date | None = None) -> FinancialStatement:
    end = period_end or date(2025, 9, 27)
    income = {
        "revenue": 1000.0,
        "cost_of_revenue": 600.0,
        "gross_profit": 400.0,
        "operating_income": 200.0,
        "net_income": 100.0,
        "eps_diluted": 1.0,
        "eps_basic": 1.02,
    }
    balance = {
        "total_assets": 5000.0,
        "total_liabilities": 3000.0,
        "total_equity": 2000.0,
        "current_assets": 1500.0,
        "current_liabilities": 1000.0,
        "inventory": 300.0,
        "shares_outstanding": 100.0,
    }
    cash = {"operating_cash_flow": 300.0, "capital_expenditure": 100.0}
    return FinancialStatement(
        symbol=symbol,
        company_name="Apple Inc.",
        market=Market.US,
        currency="USD",
        unit="currency",
        unit_scale=1.0,
        fiscal_period=fiscal_period,
        period_type="annual",
        period_start=end - timedelta(days=365),
        period_end=end,
        filing_date=end + timedelta(days=30),
        source="fake",
        income_statement=income,
        balance_sheet=balance,
        cash_flow_statement=cash,
        raw_items={},
        matched_items={},
        derived_fields=[],
        missing_fields=[],
        retrieved_at=iso(utc_now()),
        notes=[],
    )


class FakeFinancialProvider(FinancialDataProvider):
    name = "fake"

    def __init__(self, settings: object | None = None) -> None:
        # The base class only stores settings; the fake needs nothing from it.
        self.settings = settings
        self.name = "fake"

    def supports_financials(self, spec: SymbolSpec) -> bool:
        return True

    def get_financials(
        self,
        spec: SymbolSpec,
        *,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[FinancialStatement]:
        return [make_statement(spec.symbol), make_statement(spec.symbol, fiscal_period="FY2024", period_end=date(2024, 9, 28))][:limit]


class FakeMarket:
    def get_quote(self, symbol: str, **_kwargs: object) -> Quote:
        return Quote(
            symbol=symbol,
            name="Apple Inc.",
            market=Market.US,
            asset_type=AssetType.STOCK,
            currency="USD",
            price=120.0,
            previous_close=110.0,
            timestamp="2026-10-29T20:00:00+00:00",
            retrieved_at=iso(utc_now()),
            source="fake",
            data_status=DataStatus.DELAYED,
        )

    def get_historical_data(self, symbol: str, period: str = "6mo", interval: str = "1d") -> HistoricalData:
        bars: list[HistoricalBar] = []
        start = date(2026, 5, 1)
        price = 100.0
        for index in range(130):
            price += 0.4 if index % 3 else -0.5
            day = start + timedelta(days=index)
            bars.append(
                HistoricalBar(
                    date=day.isoformat(),
                    open=price - 0.3,
                    high=price + 0.8,
                    low=price - 0.9,
                    close=price,
                    volume=1_000_000.0 + index,
                    symbol=symbol,
                    currency="USD",
                    source="fake",
                )
            )
        return HistoricalData(
            symbol=symbol,
            name="Apple Inc.",
            market=Market.US,
            asset_type=AssetType.STOCK,
            currency="USD",
            period=period,
            interval=interval,
            source="fake",
            data_status=DataStatus.DELAYED,
            retrieved_at=iso(utc_now()),
            bars=bars,
            count=len(bars),
        )


@pytest.fixture()
def wired_server(monkeypatch: pytest.MonkeyPatch):
    market = FakeMarket()
    monkeypatch.setattr(server, "_fundamental_service", FundamentalService(providers=[FakeFinancialProvider()], market=market))
    monkeypatch.setattr(server, "_technical_service", TechnicalService(market=market))
    monkeypatch.setattr(server, "_calculation_service", CalculationService())
    return server.build_server()


async def test_all_fourteen_tools_are_discoverable(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    expected = {
        "get_server_status",
        "get_supported_markets",
        "get_stock_quote",
        "get_historical_data",
        "get_market_overview",
        "get_asset_quote",
        "get_company_financials",
        "calculate_financial_metrics",
        "calculate_financial_formula",
        "get_technical_indicators",
        "analyze_fundamentals",
        "analyze_technicals",
        "compare_companies",
        "get_supported_indicators",
    }
    assert names == expected
    for tool in tools:
        assert tool.description and len(tool.description) > 20
        assert tool.input_schema.get("type") == "object"


async def test_financials_tool_schema_and_output(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        result = await client.call_tool("get_company_financials", {"symbol": "AAPL", "period_type": "annual"})
    schema = tools["get_company_financials"].input_schema
    assert {"symbol", "period_type", "limit"} <= set(schema["properties"])
    payload = result.structured_content
    assert payload["symbol"] == "AAPL"
    assert payload["currency"] == "USD"
    assert payload["statements"]
    first = payload["statements"][0]
    assert first["income_statement"]["revenue"] == 1000.0
    assert first["balance_sheet"]["total_assets"] == 5000.0
    assert first["cash_flow_statement"]["operating_cash_flow"] == 300.0
    assert first["fiscal_period"] == "FY2025"
    assert first["filing_date"] == "2025-10-27"


async def test_metrics_tool_computes_requested_metrics(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "calculate_financial_metrics",
            {"symbol": "AAPL", "metrics": "gross_margin,net_margin,roe", "period_type": "annual"},
        )
    payload = result.structured_content
    names = {item["name"] for item in payload["metrics"]}
    assert names == {"gross_margin", "net_margin", "roe"}
    gross = next(item for item in payload["metrics"] if item["name"] == "gross_margin")
    assert gross["points"][0]["value"] == pytest.approx(40.0)
    assert gross["unit"] == "%"


async def test_formula_tool_runs_registered_formula(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "calculate_financial_formula",
            {"formula_name": "cagr", "inputs": {"beginning": 100, "ending": 133.1, "years": 3}},
        )
    payload = result.structured_content
    assert payload["calculation_status"] == "ok"
    assert payload["value"] == pytest.approx(10.0, rel=1e-6)
    assert payload["display"] == "10.00%"


async def test_formula_tool_rejects_unknown_formula_with_reason(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("calculate_financial_formula", {"formula_name": "magic_alpha"})
    payload = result.structured_content
    assert payload["calculation_status"] == "unavailable"
    assert "未注册" in payload["explanation"]
    assert "cagr" in payload["explanation"]


async def test_formula_tool_blocks_arbitrary_code(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "calculate_financial_formula",
            {
                "formula_name": "custom_expression",
                "inputs": {"expression": "__import__('os').system('echo pwned')"},
            },
        )
    payload = result.structured_content
    assert payload["calculation_status"] != "ok"
    assert payload["value"] is None


async def test_technical_tool_returns_numeric_indicators(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "get_technical_indicators",
            {"symbol": "AAPL", "period": "6mo", "interval": "1d"},
        )
    payload = result.structured_content
    names = {item["name"] for item in payload["indicators"]}
    assert {"sma_5", "sma_20", "ema_12", "rsi_14", "macd", "macd_signal", "macd_histogram", "atr_14"} <= names
    sma20 = next(item for item in payload["indicators"] if item["name"] == "sma_20")
    assert sma20["value"] is not None
    assert sma20["status"] == "ok"
    assert payload["window_start"] and payload["window_end"]
    assert payload["bars"] >= 120


async def test_fundamentals_tool_returns_dimensions(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("analyze_fundamentals", {"symbol": "AAPL", "analysis_scope": "full"})
    payload = result.structured_content
    dimensions = {item["dimension"] for item in payload["dimensions"]}
    assert "profitability" in dimensions
    assert payload["limitations"]
    assert any("行业基准" in note for note in payload["limitations"])


async def test_technicals_tool_returns_summary(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("analyze_technicals", {"symbol": "AAPL"})
    payload = result.structured_content
    assert payload["summary"]
    assert all("买卖建议" not in line or "不构成" in line for line in payload["summary"])


async def test_compare_tool_reports_comparability(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("compare_companies", {"symbols": "AAPL,MSFT", "metrics": "gross_margin"})
    payload = result.structured_content
    assert len(payload["rows"]) == 1
    assert len(payload["rows"][0]["values"]) == 2
    assert payload["requested_symbols"] == ["AAPL", "MSFT"]


async def test_supported_indicators_tool(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_supported_indicators", {})
    payload = result.structured_content
    assert len(payload["metrics"]) == 21
    assert len(payload["technical"]) == 6
    assert len(payload["formulas"]) >= 25
    assert {item["name"] for item in payload["data_sources"]} >= {"sina", "akshare", "sec"}


async def test_invalid_symbol_returns_structured_unavailable(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_company_financials", {"symbol": "!!!bad!!!"})
    assert not result.is_error
    payload = result.structured_content
    assert payload["data_status"] == "unavailable"
    assert payload["error"]


async def test_missing_required_argument_is_reported(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_company_financials", {})
    assert result.is_error is True
    assert "symbol" in result.content[0].text


async def test_server_stays_usable_after_a_failed_call(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        bad = await client.call_tool("calculate_financial_formula", {"formula_name": "nope"})
        good = await client.call_tool("get_supported_indicators", {})
    assert bad.structured_content["calculation_status"] == "unavailable"
    assert good.structured_content["metrics"]
