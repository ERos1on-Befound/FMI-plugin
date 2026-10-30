"""Network-free fixtures shared by the Phase 3 tests.

Nothing in this module touches the network: bars, financial statements and the
market service are built deterministically so chart and report tests can assert
on real numbers instead of "it did not raise".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Sequence

from financial_intelligence.config import Settings, load_settings
from financial_intelligence.models.financial_statements import (
    FinancialStatement,
    FinancialStatements,
)
from financial_intelligence.providers.base import FinancialDataProvider
from financial_intelligence.schemas import (
    AssetType,
    DataStatus,
    HistoricalBar,
    HistoricalData,
    Market,
    Quote,
)

NOW = "2026-10-30T12:00:00+00:00"
START = date(2026, 6, 1)


def make_bars(
    count: int = 80,
    *,
    base: float = 100.0,
    volume: bool = True,
    symbol: str = "AAPL",
) -> list[HistoricalBar]:
    """Deterministic OHLCV series: a slow up-trend with a 5-day cycle."""
    bars: list[HistoricalBar] = []
    for index in range(count):
        day = START + timedelta(days=index)
        close = base + index * 0.4 + (index % 5) * 0.9
        bars.append(
            HistoricalBar(
                date=day.isoformat(),
                open=round(close - 0.6, 4),
                high=round(close + 1.2, 4),
                low=round(close - 1.1, 4),
                close=round(close, 4),
                volume=1_000_000 + index * 1_000 if volume else None,
                symbol=symbol,
                currency="USD",
                source="fixture",
            )
        )
    return bars


def make_history(
    symbol: str = "AAPL",
    *,
    count: int = 80,
    currency: str = "USD",
    name: str = "Apple Inc.",
    volume: bool = True,
    bars: Sequence[HistoricalBar] | None = None,
    error: str | None = None,
) -> HistoricalData:
    series = list(bars) if bars is not None else make_bars(count, volume=volume, symbol=symbol)
    return HistoricalData(
        symbol=symbol,
        name=name,
        market=Market.US,
        asset_type=AssetType.STOCK,
        currency=currency,
        period="6mo",
        interval="1d",
        source="fixture",
        data_status=DataStatus.DELAYED,
        retrieved_at=NOW,
        bars=series,
        count=len(series),
        error=error,
    )


def make_statement(
    *,
    fiscal_period: str = "FY2025",
    period_end: date = date(2025, 9, 27),
    revenue: float = 1000.0,
    net_income: float = 200.0,
    **overrides: Any,
) -> FinancialStatement:
    income: dict[str, float | None] = {
        "revenue": revenue,
        "cost_of_revenue": revenue * 0.4,
        "gross_profit": revenue * 0.6,
        "operating_income": revenue * 0.3,
        "pretax_income": revenue * 0.26,
        "net_income": net_income,
        "net_income_to_parent": net_income,
        "eps_basic": 2.0,
        "eps_diluted": 1.98,
        "interest_expense": 20.0,
        "income_tax_expense": revenue * 0.06,
        "shares_basic": 100.0,
        "shares_diluted": 101.0,
    }
    balance: dict[str, float | None] = {
        "total_assets": 2000.0,
        "total_liabilities": 1200.0,
        "total_equity": 800.0,
        "equity_to_parent": 780.0,
        "current_assets": 900.0,
        "current_liabilities": 500.0,
        "inventory": 100.0,
        "cash_and_equivalents": 300.0,
        "accounts_receivable": 150.0,
        "prepayments": 10.0,
        "total_debt": 600.0,
        "shares_outstanding": 100.0,
    }
    cash_flow: dict[str, float | None] = {
        "operating_cash_flow": 260.0,
        "capital_expenditure": 60.0,
        "investing_cash_flow": -80.0,
        "financing_cash_flow": -100.0,
        "dividends_paid": 40.0,
    }
    income.update(overrides.pop("income", {}) or {})
    balance.update(overrides.pop("balance", {}) or {})
    cash_flow.update(overrides.pop("cash_flow", {}) or {})
    payload: dict[str, Any] = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "market": Market.US,
        "currency": "USD",
        "unit": "USD",
        "unit_scale": 1.0,
        "fiscal_period": fiscal_period,
        "fiscal_year": int(fiscal_period[2:]) if fiscal_period[2:].isdigit() else None,
        "period_type": "annual",
        "cumulative": False,
        "period_start": date(period_end.year - 1, 9, 28),
        "period_end": period_end,
        "filing_date": date(period_end.year + 1, 1, 15),
        "source": "fixture",
        "income_statement": income,
        "balance_sheet": balance,
        "cash_flow_statement": cash_flow,
        "raw_items": {},
        "matched_items": {},
        "derived_fields": [],
        "missing_fields": [],
        "data_status": DataStatus.HISTORICAL,
        "retrieved_at": NOW,
        "notes": [],
    }
    payload.update(overrides)
    return FinancialStatement(**payload)


def make_statements(count: int = 3) -> list[FinancialStatement]:
    """Three annual statements, newest first, with shrinking revenue."""
    result: list[FinancialStatement] = []
    for index in range(count):
        year = 2025 - index
        result.append(
            make_statement(
                fiscal_period=f"FY{year}",
                period_end=date(year, 9, 27),
                revenue=1000.0 - index * 50.0,
                net_income=200.0 - index * 10.0,
            )
        )
    return result


def make_bundle(
    symbol: str = "AAPL", *, count: int = 3, statements: Sequence[FinancialStatement] | None = None
) -> FinancialStatements:
    series = list(statements) if statements is not None else make_statements(count)
    return FinancialStatements(
        symbol=symbol,
        company_name="Apple Inc.",
        market=Market.US,
        currency="USD",
        unit="USD",
        period_type="annual",
        source="fixture",
        statements=series,
        data_status=DataStatus.HISTORICAL,
        retrieved_at=NOW,
        notes=[],
    )


def make_quote(symbol: str = "AAPL", *, name: str = "Apple Inc.", price: float = 100.0) -> Quote:
    return Quote(
        symbol=symbol,
        name=name,
        market=Market.US,
        asset_type=AssetType.STOCK,
        currency="USD",
        price=price,
        previous_close=price - 1.0,
        timestamp=NOW,
        retrieved_at=NOW,
        source="fixture",
        data_status=DataStatus.DELAYED,
    )


class FakeMarket:
    """Minimal stand-in for MarketService used by chart/report tests."""

    def __init__(self, *, history_map: dict[str, Any] | None = None) -> None:
        self._history = history_map or {}
        self.calls: list[tuple[str, str, str]] = []

    def get_historical_data(self, symbol: str, period: str = "6mo", interval: str = "1d") -> Any:
        self.calls.append((symbol, period, interval))
        found = self._history.get(symbol)
        if found is not None:
            return found
        currency = "CNY" if symbol.endswith((".SS", ".SZ")) else "USD"
        return make_history(symbol, currency=currency)

    def get_quote(self, symbol: str, market: str | None = None, asset_type: str | None = None) -> Any:
        return make_quote(symbol)


class FakeFinancialProvider(FinancialDataProvider):
    """Financial provider returning fixture statements, never touching the network."""

    name = "fixture"

    def __init__(self, statements: Sequence[FinancialStatement] | None = None) -> None:
        self.settings = load_settings()
        self.statements = list(statements) if statements is not None else make_statements()
        self.calls = 0

    def supports_financials(self, spec: Any) -> bool:
        return True

    def get_financials(self, spec: Any, *, period_type: str = "annual", limit: int = 5) -> list[FinancialStatement]:
        self.calls += 1
        return [
            item.model_copy(update={"symbol": spec.symbol})
            for item in self.statements[:limit]
        ]

    def close(self) -> None:
        return None


def make_settings(outputs_dir: Any, *, png: bool = False) -> Settings:
    """Settings pointed at a temporary outputs folder (PNG off by default)."""
    return replace(load_settings(), outputs_dir=str(outputs_dir), enable_png_export=png)
