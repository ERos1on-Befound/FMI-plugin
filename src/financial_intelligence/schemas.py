"""Unified data models for Financial Market Intelligence.

Everything an MCP tool returns is defined here first, so the structured output
schema, the validation rules and the documentation all come from one place.

Design rules that must never be relaxed:

* A missing value is ``None`` — never ``0``, never a guess.
* ``data_status`` may only be ``realtime`` when the provider can *prove* the
  quote is live.  yfinance and AKShare snapshots cannot prove that, so their
  quotes are reported as ``delayed`` / ``unknown``.
* ``timestamp`` is the time the *market data* refers to; ``retrieved_at`` is
  when *we* fetched it.  They are never mixed up.
* Every failure carries a human-readable ``error`` string instead of fake data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Format a datetime as ISO 8601 (seconds precision, explicit offset)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Market(str, Enum):
    """Market a symbol belongs to."""

    CN = "CN"
    HK = "HK"
    US = "US"
    COMMODITY = "COMMODITY"
    UNKNOWN = "UNKNOWN"


class AssetType(str, Enum):
    """Asset class of a symbol."""

    STOCK = "stock"
    INDEX = "index"
    COMMODITY = "commodity"
    UNKNOWN = "unknown"


class DataStatus(str, Enum):
    """How trustworthy / how fresh a piece of market data is."""

    REALTIME = "realtime"
    DELAYED = "delayed"
    HISTORICAL = "historical"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


class Quote(BaseModel):
    """Unified quote for a stock, index or commodity reference contract."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(description="Asset code, e.g. AAPL, 0700.HK, 600519.SS, ^GSPC, GC=F.")
    name: str | None = Field(default=None, description="Human readable asset name, if known.")
    market: Market = Field(description="Market: CN, HK, US or COMMODITY.")
    asset_type: AssetType = Field(description="Asset class: stock, index or commodity.")
    currency: str | None = Field(default=None, description="Quote currency, e.g. USD, HKD, CNY.")
    price: float | None = Field(default=None, description="Latest available price. None when unavailable.")
    previous_close: float | None = Field(default=None, description="Previous close. None when unknown.")
    change: float | None = Field(default=None, description="price - previous_close. None when either is missing.")
    change_percent: float | None = Field(
        default=None, description="Percent change vs previous close. None when it cannot be computed."
    )
    timestamp: str | None = Field(
        default=None, description="ISO 8601 time the market data refers to (NOT the retrieval time)."
    )
    retrieved_at: str = Field(description="ISO 8601 time this record was retrieved by the server.")
    source: str = Field(description="Data source that produced the value, e.g. yfinance / akshare.")
    data_status: DataStatus = Field(description="Data freshness status: realtime/delayed/stale/unavailable/unknown.")
    error: str | None = Field(default=None, description="Failure reason. Set whenever data is unavailable.")
    status_note: str | None = Field(default=None, description="Extra caveat about the data, e.g. futures reference.")
    from_cache: bool = Field(default=False, description="True when served from the short-lived in-memory cache.")

    @field_validator("price", "previous_close", "change", "change_percent")
    @classmethod
    def _finite_or_none(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value != value:  # NaN
            return None
        if value in (float("inf"), float("-inf")):
            return None
        return float(value)

    @classmethod
    def unavailable(
        cls,
        *,
        symbol: str,
        market: Market,
        asset_type: AssetType,
        source: str,
        error: str,
        name: str | None = None,
        currency: str | None = None,
        status_note: str | None = None,
    ) -> "Quote":
        """Build an explicit "no data" quote that explains why."""
        return cls(
            symbol=symbol,
            name=name,
            market=market,
            asset_type=asset_type,
            currency=currency,
            price=None,
            previous_close=None,
            change=None,
            change_percent=None,
            timestamp=None,
            retrieved_at=iso(utc_now()) or "",
            source=source,
            data_status=DataStatus.UNAVAILABLE,
            error=error,
            status_note=status_note,
        )

    def with_derived(self) -> "Quote":
        """Return a copy with ``change`` / ``change_percent`` filled in when possible."""
        if self.price is None or self.previous_close in (None, 0):
            return self
        delta = self.price - self.previous_close
        pct = delta / self.previous_close * 100.0
        return self.model_copy(update={"change": round(delta, 6), "change_percent": round(pct, 6)})


# ---------------------------------------------------------------------------
# Historical data
# ---------------------------------------------------------------------------


class HistoricalBar(BaseModel):
    """One OHLCV bar of historical market data."""

    model_config = ConfigDict(extra="forbid")

    date: str = Field(description="Bar date/time in ISO 8601 (daily bars use YYYY-MM-DD).")
    open: float | None = Field(default=None, description="Opening price of the bar.")
    high: float | None = Field(default=None, description="Highest price of the bar.")
    low: float | None = Field(default=None, description="Lowest price of the bar.")
    close: float | None = Field(default=None, description="Closing price of the bar.")
    volume: float | None = Field(default=None, description="Traded volume of the bar.")
    symbol: str = Field(description="Asset code the bar belongs to.")
    currency: str | None = Field(default=None, description="Quote currency of the bar.")
    source: str = Field(description="Data source of the bar.")


class HistoricalData(BaseModel):
    """Result of a historical market data query."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(description="Asset code that was requested.")
    name: str | None = Field(default=None, description="Human readable asset name, if known.")
    market: Market = Field(description="Market of the asset.")
    asset_type: AssetType = Field(description="Asset class.")
    currency: str | None = Field(default=None, description="Quote currency.")
    period: str = Field(description="Requested history window, e.g. 1mo / 1y / 5y.")
    interval: str = Field(description="Requested bar interval, e.g. 1d / 1wk / 1mo.")
    source: str = Field(description="Data source that produced the bars.")
    data_status: DataStatus = Field(description="Always 'historical' for a successful series.")
    retrieved_at: str = Field(description="ISO 8601 time this series was retrieved.")
    bars: list[HistoricalBar] = Field(default_factory=list, description="OHLCV bars, oldest first.")
    count: int = Field(default=0, description="Number of bars returned.")
    truncated: bool = Field(default=False, description="True when bars were capped by the server row limit.")
    error: str | None = Field(default=None, description="Failure reason when no bars could be returned.")
    status_note: str | None = Field(default=None, description="Extra caveat, e.g. futures reference contract.")
    from_cache: bool = Field(default=False, description="True when served from the in-memory cache.")

    def attach_count(self) -> "HistoricalData":
        return self.model_copy(update={"count": len(self.bars)})


# ---------------------------------------------------------------------------
# Market overview
# ---------------------------------------------------------------------------


class OverviewGroup(BaseModel):
    """One market section of the overview (A股 / 港股 / 美股 / 大宗商品)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Section label, e.g. A股 / 港股 / 美股 / 大宗商品.")
    market: Market = Field(description="Market of this section.")
    items: list[Quote] = Field(default_factory=list, description="Quotes of the fixed reference assets.")


class MarketOverview(BaseModel):
    """Structured snapshot of the fixed reference assets used by the MVP."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Overview title.")
    retrieved_at: str = Field(description="ISO 8601 time this overview was produced.")
    groups: list[OverviewGroup] = Field(default_factory=list, description="One group per market.")
    ok_count: int = Field(default=0, description="Number of assets with usable data.")
    unavailable_count: int = Field(default=0, description="Number of assets that could not be retrieved.")
    notes: list[str] = Field(default_factory=list, description="Caveats that apply to the whole overview.")


# ---------------------------------------------------------------------------
# Server introspection
# ---------------------------------------------------------------------------


class ServerStatus(BaseModel):
    """Health information for the MCP server."""

    model_config = ConfigDict(extra="forbid")

    server_name: str = Field(description="Server identifier.")
    version: str = Field(description="Server version.")
    status: str = Field(description="Service status, e.g. ok.")
    current_time: str = Field(description="Server time in ISO 8601.")
    providers: list[str] = Field(default_factory=list, description="Enabled data sources.")
    markets: list[str] = Field(default_factory=list, description="Supported markets.")
    asset_types: list[str] = Field(default_factory=list, description="Supported asset classes.")
    modules: list[str] = Field(default_factory=list, description="Core modules that were loaded successfully.")
    notes: list[str] = Field(default_factory=list, description="Operational notes and caveats.")


class SupportedMarkets(BaseModel):
    """Capabilities the server actually implements (never aspirational)."""

    model_config = ConfigDict(extra="forbid")

    markets: list[str] = Field(description="Markets that can be queried right now.")
    asset_types: list[str] = Field(description="Asset classes that can be queried right now.")
    providers: list[str] = Field(description="Data sources currently enabled.")
    periods: list[str] = Field(description="Supported history windows.")
    intervals: list[str] = Field(description="Supported bar intervals.")
    notes: list[str] = Field(default_factory=list, description="Honest caveats about coverage and data quality.")


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    """Serialize a model to plain JSON-compatible types (enums become strings)."""
    return model.model_dump(mode="json")
