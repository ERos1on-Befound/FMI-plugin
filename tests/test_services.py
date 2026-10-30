"""Service-layer tests: fallback order, caching and failure reporting.

The providers here are fakes, so these tests are fully offline and deterministic.
"""

from __future__ import annotations

import pytest

from financial_intelligence.config import Settings
from financial_intelligence.providers.base import MarketDataProvider, ProviderError
from financial_intelligence.schemas import (
    DataStatus,
    HistoricalBar,
    HistoricalData,
    Market,
    Quote,
    iso,
    utc_now,
)
from financial_intelligence.services import OVERVIEW_GROUPS, MarketService
from financial_intelligence.utils.symbol_mapper import SymbolSpec
from financial_intelligence.utils.validators import InputError


def make_quote(spec: SymbolSpec, *, price: float | None, source: str) -> Quote:
    return Quote(
        symbol=spec.symbol,
        name=spec.name_hint or spec.symbol,
        market=spec.market,
        asset_type=spec.asset_type,
        currency=spec.currency,
        price=price,
        previous_close=100.0,
        timestamp="2026-10-29T20:00:00+00:00",
        retrieved_at=iso(utc_now()) or "",
        source=source,
        data_status=DataStatus.DELAYED if price is not None else DataStatus.UNAVAILABLE,
    ).with_derived()


def make_history(spec: SymbolSpec, period: str, interval: str, source: str) -> HistoricalData:
    bars = [
        HistoricalBar(
            date="2026-10-28",
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10.0,
            symbol=spec.symbol,
            currency=spec.currency,
            source=source,
        )
    ]
    return HistoricalData(
        symbol=spec.symbol,
        market=spec.market,
        asset_type=spec.asset_type,
        currency=spec.currency,
        period=period,
        interval=interval,
        source=source,
        data_status=DataStatus.HISTORICAL,
        retrieved_at=iso(utc_now()) or "",
        bars=bars,
        count=len(bars),
    )


class FakeProvider(MarketDataProvider):
    """Scriptable provider: quote/history results are supplied up-front."""

    def __init__(
        self,
        name: str,
        *,
        quote: Quote | None = None,
        error: str | None = None,
        price: float | None = 123.0,
        history: HistoricalData | None = None,
        history_error: str | None = None,
        quote_supported: bool = True,
        history_supported: bool = True,
    ) -> None:
        super().__init__(Settings())
        self.name = name
        self._quote = quote
        self._error = error
        self._price = price
        self._history = history
        self._history_error = history_error
        self._quote_supported = quote_supported
        self._history_supported = history_supported
        self.quote_calls = 0
        self.history_calls = 0

    def supports_quote(self, spec: SymbolSpec) -> bool:
        return self._quote_supported

    def supports_history(self, spec: SymbolSpec) -> bool:
        return self._history_supported

    def get_quote(self, spec: SymbolSpec) -> Quote:
        self.quote_calls += 1
        if self._error:
            raise ProviderError(self.name, self._error, kind="network", symbol=spec.symbol)
        if self._quote is not None:
            return self._quote
        return make_quote(spec, price=self._price, source=self.name)

    def get_history(self, spec: SymbolSpec, period: str, interval: str) -> HistoricalData:
        self.history_calls += 1
        if self._history_error:
            raise ProviderError(self.name, self._history_error, kind="network", symbol=spec.symbol)
        if self._history is not None:
            return self._history
        return make_history(spec, period, interval, self.name)


@pytest.fixture()
def settings() -> Settings:
    return Settings(cache_ttl=60.0, history_cache_ttl=300.0)


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


def test_quote_falls_back_to_the_next_provider(settings: Settings) -> None:
    primary = FakeProvider("primary", error="push2 限流")
    backup = FakeProvider("backup", price=321.0)
    service = MarketService(settings, [primary, backup])

    quote = service.get_quote("AAPL")

    assert quote.source == "backup"
    assert quote.price == pytest.approx(321.0)
    assert quote.error is None
    assert primary.quote_calls == 1
    assert backup.quote_calls == 1


def test_quote_without_price_is_treated_as_failure(settings: Settings) -> None:
    empty = FakeProvider("empty", price=None)
    good = FakeProvider("good", price=50.0)
    service = MarketService(settings, [empty, good])

    quote = service.get_quote("600519.SS")

    assert quote.source == "good"
    assert quote.price == pytest.approx(50.0)


def test_all_providers_failing_produces_an_explained_unavailable_quote(settings: Settings) -> None:
    first = FakeProvider("first", error="第一源超时")
    second = FakeProvider("second", error="第二源限流")
    service = MarketService(settings, [first, second])

    quote = service.get_quote("AAPL")

    assert quote.price is None
    assert quote.data_status is DataStatus.UNAVAILABLE
    assert "第一源超时" in (quote.error or "")
    assert "第二源限流" in (quote.error or "")


def test_provider_that_does_not_support_the_asset_is_skipped_with_a_reason(settings: Settings) -> None:
    no_quotes = FakeProvider("no_quotes", quote_supported=False)
    good = FakeProvider("good", price=11.0)
    service = MarketService(settings, [no_quotes, good])

    quote = service.get_quote("AAPL")

    assert quote.price == pytest.approx(11.0)
    assert no_quotes.quote_calls == 0


def test_no_providers_at_all_is_reported(settings: Settings) -> None:
    service = MarketService(settings, [])
    quote = service.get_quote("AAPL")
    assert quote.price is None
    assert "没有启用的数据源" in (quote.error or "")


def test_second_call_is_served_from_cache_and_keeps_market_timestamp(settings: Settings) -> None:
    provider = FakeProvider("only", price=42.0)
    service = MarketService(settings, [provider])

    first = service.get_quote("AAPL")
    second = service.get_quote("AAPL")

    assert provider.quote_calls == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.timestamp == first.timestamp  # market time is not refreshed
    assert second.price == first.price


def test_cache_respects_ttl(settings: Settings) -> None:
    provider = FakeProvider("only", price=42.0)
    service = MarketService(Settings(cache_ttl=0.0), [provider])

    service.get_quote("AAPL")
    service.get_quote("AAPL")

    assert provider.quote_calls == 2


def test_clear_cache_forces_a_refetch(settings: Settings) -> None:
    provider = FakeProvider("only", price=42.0)
    service = MarketService(settings, [provider])

    service.get_quote("AAPL")
    service.clear_cache()
    fresh = service.get_quote("AAPL")

    assert provider.quote_calls == 2
    assert fresh.from_cache is False


def test_cache_is_keyed_by_symbol(settings: Settings) -> None:
    provider = FakeProvider("only", price=42.0)
    service = MarketService(settings, [provider])

    service.get_quote("AAPL")
    service.get_quote("MSFT")

    assert provider.quote_calls == 2


def test_invalid_symbol_raises_for_the_tool_layer_to_translate(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("only")])
    with pytest.raises(InputError):
        service.get_quote("!!!bad!!!")


def test_market_hint_is_honoured_and_conflicts_rejected(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("only")])
    assert service.get_quote("600519", market="CN").market is Market.CN
    with pytest.raises(InputError):
        service.get_quote("600519.SS", market="US")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_history_falls_back_and_reports_source(settings: Settings) -> None:
    broken = FakeProvider("broken", history_error="接口不可用")
    good = FakeProvider("good")
    service = MarketService(settings, [broken, good])

    history = service.get_historical_data("AAPL", "1mo", "1d")

    assert history.source == "good"
    assert history.count == 1


def test_unsupported_period_interval_combination_is_rejected(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("only")])
    with pytest.raises(InputError):
        service.get_historical_data("AAPL", "1mo", "1mo")
    with pytest.raises(InputError):
        service.get_historical_data("AAPL", "2w", "1d")


def test_history_total_failure_returns_no_bars_and_a_reason(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("only", history_error="接口挂了")])

    history = service.get_historical_data("AAPL", "1mo", "1d")

    assert history.bars == []
    assert history.count == 0
    assert history.data_status is DataStatus.UNAVAILABLE
    assert "接口挂了" in (history.error or "")


def test_history_is_cached_independently_of_quotes(settings: Settings) -> None:
    provider = FakeProvider("only")
    service = MarketService(settings, [provider])

    service.get_historical_data("AAPL", "1mo", "1d")
    cached = service.get_historical_data("AAPL", "1mo", "1d")
    service.get_historical_data("AAPL", "3mo", "1d")

    assert cached.from_cache is True
    assert provider.history_calls == 2


# ---------------------------------------------------------------------------
# Overview / introspection
# ---------------------------------------------------------------------------


def test_overview_counts_successes_and_failures_per_asset(settings: Settings) -> None:
    class HalfBroken(FakeProvider):
        def get_quote(self, spec: SymbolSpec) -> Quote:
            self.quote_calls += 1
            if spec.symbol == "^GSPC":
                raise ProviderError(self.name, "标普接口失败", kind="network", symbol=spec.symbol)
            return make_quote(spec, price=1.0, source=self.name)

    service = MarketService(settings, [HalfBroken("half")])
    overview = service.get_market_overview()

    total = sum(len(group.items) for group in overview.groups)
    assert total == sum(len(symbols) for _, _, symbols in OVERVIEW_GROUPS)
    assert overview.ok_count == total - 1
    assert overview.unavailable_count == 1
    assert [group.label for group in overview.groups] == [label for label, _, _ in OVERVIEW_GROUPS]

    us_group = next(group for group in overview.groups if group.market is Market.US)
    spx = next(item for item in us_group.items if item.symbol == "^GSPC")
    assert spx.price is None
    assert "标普接口失败" in (spx.error or "")


def test_overview_commodity_group_is_labelled_as_futures_reference(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("only", price=1.0)])
    overview = service.get_market_overview()
    labels = [group.label for group in overview.groups]
    assert any("期货参考价" in label for label in labels)


def test_server_status_lists_providers_and_never_claims_realtime(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("sina"), FakeProvider("akshare")])
    status = service.get_server_status()

    assert status.status == "ok"
    assert status.providers == ["sina", "akshare"]
    assert "COMMODITY" in status.markets
    assert any("不能证明" in note for note in status.notes)


def test_supported_markets_documents_formats_and_limits(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("sina")])
    markets = service.get_supported_markets()

    assert len(markets.markets) == 4
    assert markets.periods == ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
    assert markets.intervals == ["1d", "1wk", "1mo"]
    assert any(".SS" in note for note in markets.notes)
    assert any("期货" in note for note in markets.notes)


def test_symbol_help_is_available_for_error_messages(settings: Settings) -> None:
    service = MarketService(settings, [FakeProvider("sina")])
    assert "600519.SS" in service.symbol_help()


def test_default_construction_builds_real_providers() -> None:
    service = MarketService(Settings())
    names = [provider.name for provider in service.providers]
    assert names  # at least one provider is enabled by default
    assert set(names) <= {"sina", "akshare", "yfinance"}
