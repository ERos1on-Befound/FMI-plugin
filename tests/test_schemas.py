"""Schema-level tests: the data contract must never silently invent values."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from financial_intelligence.schemas import (
    AssetType,
    DataStatus,
    HistoricalBar,
    HistoricalData,
    Market,
    Quote,
    model_to_dict,
)


def make_quote(**overrides) -> Quote:
    payload = dict(
        symbol="AAPL",
        name="Apple Inc.",
        market=Market.US,
        asset_type=AssetType.STOCK,
        currency="USD",
        price=110.0,
        previous_close=100.0,
        timestamp="2026-10-29T20:00:00+00:00",
        retrieved_at="2026-10-30T01:00:00+00:00",
        source="sina",
        data_status=DataStatus.DELAYED,
    )
    payload.update(overrides)
    return Quote(**payload)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


def test_enum_values_match_the_documented_contract() -> None:
    assert [m.value for m in Market] == ["CN", "HK", "US", "COMMODITY", "UNKNOWN"]
    assert [a.value for a in AssetType] == ["stock", "index", "commodity", "unknown"]
    assert [s.value for s in DataStatus] == [
        "realtime",
        "delayed",
        "historical",
        "stale",
        "unavailable",
        "unknown",
    ]


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


def test_unavailable_quote_has_no_fabricated_numbers() -> None:
    quote = Quote.unavailable(
        symbol="BAD",
        market=Market.UNKNOWN,
        asset_type=AssetType.UNKNOWN,
        source="unavailable",
        error="数据源全部失败",
    )
    assert quote.price is None
    assert quote.previous_close is None
    assert quote.change is None
    assert quote.change_percent is None
    assert quote.timestamp is None
    assert quote.data_status is DataStatus.UNAVAILABLE
    assert quote.error == "数据源全部失败"
    assert quote.retrieved_at  # retrieval time is always present


def test_with_derived_computes_change_and_percent() -> None:
    quote = make_quote(price=110.0, previous_close=100.0).with_derived()
    assert quote.change == pytest.approx(10.0)
    assert quote.change_percent == pytest.approx(10.0)


def test_with_derived_keeps_none_when_previous_close_is_missing_or_zero() -> None:
    assert make_quote(previous_close=None).with_derived().change is None
    assert make_quote(previous_close=0.0).with_derived().change_percent is None
    assert make_quote(price=None).with_derived().change is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_prices_become_none(bad: float) -> None:
    assert make_quote(price=bad).price is None


def test_quote_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_quote(price_close=1.0)  # typo'd field must be rejected, not ignored


def test_timestamp_and_retrieved_at_are_separate_fields() -> None:
    quote = make_quote()
    assert quote.timestamp == "2026-10-29T20:00:00+00:00"
    assert quote.retrieved_at == "2026-10-30T01:00:00+00:00"
    assert quote.timestamp != quote.retrieved_at


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_historical_data_counts_bars() -> None:
    bars = [
        HistoricalBar(
            date=f"2026-10-{day:02d}",
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=100.0,
            symbol="AAPL",
            currency="USD",
            source="akshare",
        )
        for day in (28, 29, 30)
    ]
    history = HistoricalData(
        symbol="AAPL",
        market=Market.US,
        asset_type=AssetType.STOCK,
        period="1mo",
        interval="1d",
        source="akshare",
        data_status=DataStatus.HISTORICAL,
        retrieved_at="2026-10-30T01:00:00+00:00",
        bars=bars,
    )
    assert history.count == 0
    assert history.attach_count().count == 3
    assert history.bars[0].date == "2026-10-28"


def test_historical_bar_requires_symbol_and_source() -> None:
    with pytest.raises(ValidationError):
        HistoricalBar(date="2026-10-30", source="akshare")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_model_to_dict_serialises_enums_as_strings() -> None:
    payload = model_to_dict(make_quote())
    assert payload["market"] == "US"
    assert payload["asset_type"] == "stock"
    assert payload["data_status"] == "delayed"
    assert isinstance(payload["price"], float)
