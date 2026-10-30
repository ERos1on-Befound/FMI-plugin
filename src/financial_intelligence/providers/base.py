"""Provider contract shared by every data source.

Each provider translates a :class:`SymbolSpec` into provider-specific calls and
returns the unified models from :mod:`financial_intelligence.schemas`.  Providers
never guess: if something fails they raise :class:`ProviderError` with a message
a human can act on, and the service layer decides whether to fall back.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any, Callable

from ..config import Settings
from ..models.financial_statements import FinancialStatement
from ..schemas import DataStatus, HistoricalBar, HistoricalData, Quote, iso, utc_now
from ..utils.bars import cap_bars, resample_bars, window_bars
from ..utils.symbol_mapper import SymbolSpec


class ProviderError(RuntimeError):
    """A data source failed; ``kind`` classifies why."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        kind: str = "error",
        symbol: str | None = None,
    ) -> None:
        self.provider = provider
        self.message = message
        self.kind = kind
        self.symbol = symbol
        prefix = f"[{provider}] {symbol}: " if symbol else f"[{provider}] "
        super().__init__(prefix + message)

    @classmethod
    def from_exception(
        cls, provider: str, exc: BaseException, *, symbol: str | None = None
    ) -> "ProviderError":
        message = str(exc).strip() or exc.__class__.__name__
        kind = "error"
        name = exc.__class__.__name__
        if "RateLimit" in name or "Too Many Requests" in message:
            kind = "rate_limit"
        elif "Timeout" in name or "timed out" in message.lower():
            kind = "timeout"
        elif "Connection" in name or "RemoteDisconnected" in message:
            kind = "network"
        return cls(provider, f"{name}: {message}", kind=kind, symbol=symbol)


def call_with_timeout(
    func: Callable[[], Any],
    timeout: float,
    provider: str,
    *,
    symbol: str | None = None,
) -> Any:
    """Run ``func`` in a daemon thread and enforce a hard timeout.

    Vendor libraries do not honour our timeout consistently (some requests have
    no timeout at all), so the wall-clock guard lives here.  A daemon thread is
    used deliberately: a hung request must not block interpreter shutdown.
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc

    thread = threading.Thread(target=runner, name=f"fmi-{provider}", daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        raise ProviderError(
            provider,
            f"请求超时（超过 {timeout:.0f} 秒），数据源没有在限定时间内返回。",
            kind="timeout",
            symbol=symbol,
        )
    error = box.get("error")
    if error is not None:
        if isinstance(error, ProviderError):
            raise error
        raise ProviderError.from_exception(provider, error, symbol=symbol)
    return box.get("value")


class MarketDataProvider(ABC):
    """Base class for a market data source."""

    #: Short stable identifier used in ``source`` fields and settings.
    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- capability declarations ----------------------------------------
    def supports_quote(self, spec: SymbolSpec) -> bool:  # noqa: ARG002 - default all
        return True

    def supports_history(self, spec: SymbolSpec) -> bool:  # noqa: ARG002 - default all
        return True

    # -- operations ------------------------------------------------------
    @abstractmethod
    def get_quote(self, spec: SymbolSpec) -> Quote:
        """Return a single quote or raise :class:`ProviderError`."""

    @abstractmethod
    def get_history(self, spec: SymbolSpec, period: str, interval: str) -> HistoricalData:
        """Return a historical series or raise :class:`ProviderError`."""

    def close(self) -> None:
        """Release any long-lived resources; default is a no-op."""


class FinancialDataProvider(ABC):
    """Base class for a source of published financial statements."""

    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def supports_financials(self, spec: SymbolSpec) -> bool:  # noqa: ARG002 - default none
        return False

    @abstractmethod
    def get_financials(
        self,
        spec: SymbolSpec,
        *,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[FinancialStatement]:
        """Return statements ordered newest first, or raise :class:`ProviderError`."""

    def close(self) -> None:
        """Release any long-lived resources; default is a no-op."""


def assemble_history(
    spec: SymbolSpec,
    bars: list[dict[str, Any]],
    *,
    period: str,
    interval: str,
    source: str,
    settings: Settings,
    data_status: DataStatus = DataStatus.HISTORICAL,
    name: str | None = None,
    status_note: str | None = None,
) -> HistoricalData:
    """Shared post-processing: resample, window, cap and decorate a bar series."""
    if not bars:
        raise ProviderError(
            source,
            f"数据源没有返回 {spec.symbol} 在 period={period}, interval={interval} 下的任何K线。",
            kind="empty",
            symbol=spec.symbol,
        )

    series = resample_bars(bars, interval)
    series = window_bars(series, period)
    if not series:
        raise ProviderError(
            source,
            f"数据源返回了K线，但在 period={period} 窗口内没有可用数据。",
            kind="empty",
            symbol=spec.symbol,
        )
    series, truncated = cap_bars(series, settings.max_history_rows)

    notes: list[str] = []
    if status_note:
        notes.append(status_note)
    if truncated:
        notes.append(f"结果已按服务端上限截断为最近 {settings.max_history_rows} 根K线。")
    if interval != "1d":
        notes.append(f"{interval} K线由日线数据聚合得到（开=首日开盘，高=区间最高，低=区间最低，收=末日收盘，量=区间合计）。")

    bars_models = [
        HistoricalBar(
            date=bar["date"],
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            volume=bar["volume"],
            symbol=spec.symbol,
            currency=spec.currency,
            source=source,
        )
        for bar in series
    ]
    return HistoricalData(
        symbol=spec.symbol,
        name=spec.name_hint or name,
        market=spec.market,
        asset_type=spec.asset_type,
        currency=spec.currency,
        period=period,
        interval=interval,
        source=source,
        data_status=data_status,
        retrieved_at=iso(utc_now()) or "",
        bars=bars_models,
        count=len(bars_models),
        truncated=truncated,
        status_note=" ".join(notes) if notes else None,
    )
