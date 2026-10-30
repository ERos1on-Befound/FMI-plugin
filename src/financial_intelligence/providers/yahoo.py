"""Yahoo Finance provider (portable fallback).

This provider is implemented with the official ``yfinance`` package and is kept
as a *portable* fallback: it works from networks where Yahoo does not rate-limit
the client, and it is the only free source used here that also returns a
currency/instrument type in one round trip.

From the network this MVP was developed on, every yfinance call raises
``YFRateLimitError: Too Many Requests`` — in that case the market service falls
through to the Sina / AKShare providers.  This module therefore never assumes
the network is reachable: failures are reported as :class:`ProviderError`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..schemas import AssetType, DataStatus, HistoricalData, Quote, iso, utc_now
from ..utils.bars import frame_to_bars
from ..utils.symbol_mapper import SymbolSpec
from ..utils.validators import to_float, to_iso_datetime
from .base import MarketDataProvider, ProviderError, assemble_history, call_with_timeout

_INDEX_TYPE_HINTS = {"index", "etf", "mutualfund", "currency", "cryptocurrency"}


def _yf() -> Any:
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("yahoo", f"无法导入 yfinance：{exc}", kind="import") from exc
    return yf


class YahooProvider(MarketDataProvider):
    """Quotes and history via yfinance (Yahoo Finance)."""

    name = "yfinance"
    alias = "yahoo"

    # ------------------------------------------------------------------
    def supports_quote(self, spec: SymbolSpec) -> bool:
        return bool(spec.yahoo_symbol)

    def supports_history(self, spec: SymbolSpec) -> bool:
        return bool(spec.yahoo_symbol)

    # ------------------------------------------------------------------
    def _ticker(self, spec: SymbolSpec) -> Any:
        yf = _yf()
        return yf.Ticker(spec.yahoo_symbol)

    def get_quote(self, spec: SymbolSpec) -> Quote:
        def fetch() -> tuple[dict[str, Any], dict[str, Any]]:
            ticker = self._ticker(spec)
            frame = ticker.history(period="5d", interval="1d")
            meta = dict(getattr(ticker, "history_metadata", None) or {})
            return meta, {"rows": int(len(frame)) if frame is not None else 0}

        meta, stats = call_with_timeout(
            fetch, max(self.settings.request_timeout, 15.0), self.name, symbol=spec.symbol
        )
        retrieved = utc_now()

        price = to_float(meta.get("regularMarketPrice"))
        previous_close = to_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
        timestamp = to_iso_datetime(meta.get("regularMarketTime"))

        status = DataStatus.DELAYED
        error = None
        if price is None:
            status = DataStatus.UNAVAILABLE
            error = "Yahoo 未返回 regularMarketPrice（可能代码无效或该资产当日无成交）。"
        elif timestamp is None:
            status = DataStatus.UNKNOWN
        elif (retrieved - timestamp).total_seconds() > self.settings.stale_after:
            status = DataStatus.STALE

        instrument = str(meta.get("instrumentType") or "").lower()
        asset_type = spec.asset_type
        if asset_type.value == "unknown" and instrument:
            asset_type = AssetType.INDEX if instrument in _INDEX_TYPE_HINTS else AssetType.UNKNOWN

        currency = (meta.get("currency") or spec.currency or "").upper() or None
        notes: list[str] = []
        if spec.is_commodity:
            notes.append(
                "Yahoo 的期货代码（如 GC=F）返回的是期货合约价格，不是现货价格；"
                "合约规格与单位以交易所为准。"
            )
        if stats.get("rows") == 0:
            notes.append("Yahoo 近期日线为空，估值可能来自延迟快照。")

        quote = Quote(
            symbol=spec.symbol,
            name=spec.name_hint or meta.get("shortName") or meta.get("longName"),
            market=spec.market,
            asset_type=asset_type,
            currency=currency,
            price=price,
            previous_close=previous_close,
            timestamp=iso(timestamp),
            retrieved_at=iso(retrieved) or "",
            source=self.name,
            data_status=status,
            error=error,
            status_note=" ".join(notes) if notes else None,
        )
        return quote.with_derived()

    # ------------------------------------------------------------------
    def get_history(self, spec: SymbolSpec, period: str, interval: str) -> HistoricalData:
        def fetch() -> Any:
            ticker = self._ticker(spec)
            return ticker.history(period=period, interval=interval, auto_adjust=False)

        frame = call_with_timeout(
            fetch, max(self.settings.request_timeout * 2, 20.0), self.name, symbol=spec.symbol
        )
        bars = frame_to_bars(frame)
        if not bars:
            raise ProviderError(
                "yfinance",
                f"Yahoo 未返回 {spec.symbol} 在 period={period}, interval={interval} 下的历史数据。",
                kind="empty",
                symbol=spec.symbol,
            )
        # Yahoo already aggregated at the requested interval: skip re-resampling
        # by asking assemble_history for the same interval it receives.
        return assemble_history(
            spec,
            bars,
            period=period,
            interval=interval,
            source=self.name,
            settings=self.settings,
            status_note=(
                "Yahoo 的期货代码返回期货合约历史，不是现货价格。" if spec.is_commodity else None
            ),
        )


def _utcnow() -> datetime:  # pragma: no cover - convenience re-export
    return datetime.now(tz=timezone.utc)
