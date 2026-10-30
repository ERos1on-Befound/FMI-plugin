"""AKShare provider: historical series (and a commodity quote fallback).

AKShare wraps many free public endpoints.  Verified working from this host:

* A-share daily history ............ ``stock_zh_a_hist`` (Eastmoney push2his)
* A-share index daily history ...... ``stock_zh_index_daily`` (Sina) / ``..._tx`` (Tencent)
* Hong Kong stock daily history .... ``stock_hk_daily`` (Sina)
* US stock daily history ........... ``stock_us_daily`` (Sina)
* US index daily history ........... ``index_us_stock_sina`` (Sina)
* Foreign futures daily history .... ``futures_foreign_hist`` (Sina)
* Foreign futures snapshot ......... ``futures_foreign_commodity_realtime`` (Sina)

Eastmoney's ``push2`` snapshot endpoints (``stock_bid_ask_em``, ``stock_zh_index_spot_em``,
``index_zh_a_hist`` …) are frequently rate-limited by IP, which is why quotes for
CN/HK/US come from the Sina provider instead.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..schemas import DataStatus, HistoricalData, Quote, iso, utc_now
from ..utils.bars import PERIOD_DAYS, frame_to_bars
from ..utils.symbol_mapper import SymbolSpec
from ..utils.validators import to_float
from .base import MarketDataProvider, ProviderError, assemble_history, call_with_timeout

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

_US_INDEX_CODES = {"^GSPC": ".INX", "^SPX": ".INX", "^IXIC": ".IXIC", "^DJI": ".DJI"}
_COMMODITY_CODES = {
    "GC=F": "GC",
    "CL=F": "CL",
    "BZ=F": "OIL",
    "SI=F": "SI",
    "HG=F": "HG",
    "NG": "NG",
    "NG=F": "NG",
}

_FUTURES_NOTE = "外盘期货近月连续合约参考价，不是现货价格；合约规格与单位以交易所为准。"
_FUTURES_HISTORY_NOTE = "外盘期货历史为近月连续合约参考序列，不是现货价格。"


def _ak() -> Any:
    """Import AKShare lazily so server start-up never depends on it."""
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001 - surfaced as a provider error
        raise ProviderError("akshare", f"无法导入 akshare：{exc}", kind="import") from exc
    return ak


def _eastmoney_throttled() -> bool:
    """True while the Eastmoney provider is in its cooldown window."""
    try:
        from .eastmoney import BREAKER

        return BREAKER.is_open
    except Exception:  # noqa: BLE001 - never let a diagnostic helper break a request
        return False


class AkshareProvider(MarketDataProvider):
    """Historical data via AKShare plus a foreign-futures quote fallback."""

    name = "akshare"

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------
    def supports_quote(self, spec: SymbolSpec) -> bool:
        return spec.is_commodity

    def supports_history(self, spec: SymbolSpec) -> bool:
        if spec.is_commodity:
            return spec.symbol.upper() in _COMMODITY_CODES
        if spec.symbol.startswith("^"):
            return spec.symbol in _US_INDEX_CODES or spec.symbol in {"^HSI", "^HSTECH"}
        return stride_supported(spec)

    # ------------------------------------------------------------------
    # Quote (commodities only)
    # ------------------------------------------------------------------
    def get_quote(self, spec: SymbolSpec) -> Quote:
        code = _COMMODITY_CODES.get(spec.symbol.upper())
        if code is None:
            raise ProviderError(
                "akshare",
                f"报价回退仅覆盖外盘期货合约；{spec.symbol} 不受支持。",
                kind="unsupported",
                symbol=spec.symbol,
            )
        ak = _ak()

        def fetch() -> Any:
            return ak.futures_foreign_commodity_realtime(symbol=[code])

        frame = call_with_timeout(fetch, self.settings.request_timeout, self.name, symbol=spec.symbol)
        if frame is None or len(frame) == 0:
            raise ProviderError("akshare", f"未返回 {code} 的期货行情。", kind="empty", symbol=spec.symbol)

        row = frame.iloc[0].to_dict()
        price = to_float(row.get("最新价"))
        prev_settlement = to_float(row.get("昨日结算价"))
        timestamp = _futures_timestamp(row.get("日期"), row.get("行情时间"))
        retrieved = utc_now()

        status = DataStatus.DELAYED
        error = None
        if price is None:
            status = DataStatus.UNAVAILABLE
            error = "数据源返回的最新价为空。"
        elif timestamp is None:
            status = DataStatus.UNKNOWN
        elif (retrieved - timestamp).total_seconds() > self.settings.stale_after:
            status = DataStatus.STALE

        quote = Quote(
            symbol=spec.symbol,
            name=spec.name_hint or str(row.get("名称") or "") or None,
            market=spec.market,
            asset_type=spec.asset_type,
            currency=spec.currency,
            price=price,
            previous_close=prev_settlement,
            timestamp=iso(timestamp),
            retrieved_at=iso(retrieved) or "",
            source=self.name,
            data_status=status,
            error=error,
            status_note=_FUTURES_NOTE + " previous_close 为昨日结算价。",
        )
        return quote.with_derived()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def get_history(self, spec: SymbolSpec, period: str, interval: str) -> HistoricalData:
        days = PERIOD_DAYS.get(period, 366)
        bars = call_with_timeout(
            lambda: self._daily_bars(spec, days),
            max(self.settings.request_timeout * 3, 20.0),
            self.name,
            symbol=spec.symbol,
        )
        return assemble_history(
            spec,
            bars,
            period=period,
            interval=interval,
            source=self.name,
            settings=self.settings,
            status_note=_FUTURES_HISTORY_NOTE if spec.is_commodity else None,
        )

    # -- fetch dispatch --------------------------------------------------
    def _daily_bars(self, spec: SymbolSpec, days: int) -> list[dict[str, Any]]:
        if spec.is_commodity:
            return self._commodity_history(spec)
        if spec.symbol.startswith("^"):
            if spec.symbol in _US_INDEX_CODES:
                return self._us_index_history(spec)
            return self._hk_index_history(spec)
        if spec.market.value == "CN":
            return self._cn_history(spec, days)
        if spec.market.value == "HK":
            return self._hk_history(spec)
        return self._us_history(spec)

    def _try_chain(self, spec: SymbolSpec, attempts: list[tuple[str, Callable[[], Any]]]) -> list[dict[str, Any]]:
        """Run fetch attempts in order; raise one combined error if all fail."""
        errors: list[str] = []
        for label, func in attempts:
            try:
                bars = frame_to_bars(func())
            except Exception as exc:  # noqa: BLE001 - aggregated below
                errors.append(f"{label} -> {exc.__class__.__name__}: {exc}")
                continue
            if bars:
                return bars
            errors.append(f"{label} -> 返回空数据")
        raise ProviderError(
            "akshare",
            f"{spec.symbol} 的历史行情在所有接口上都失败了：" + " | ".join(errors),
            kind="empty",
            symbol=spec.symbol,
        )

    def _cn_history(self, spec: SymbolSpec, days: int) -> list[dict[str, Any]]:
        ak = _ak()
        code = spec.akshare_code
        exchange = spec.akshare_exchange or "sh"
        start = (date.today() - timedelta(days=days + 15)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")

        if spec.asset_type.value == "index":
            prefixed = f"{exchange}{code}"
            attempts: list[tuple[str, Callable[[], Any]]] = [
                ("stock_zh_index_daily", lambda: ak.stock_zh_index_daily(symbol=prefixed)),
                ("stock_zh_index_daily_tx", lambda: ak.stock_zh_index_daily_tx(symbol=prefixed)),
                (
                    "index_zh_a_hist",
                    lambda: ak.index_zh_a_hist(
                        symbol=code, period="daily", start_date=start, end_date=end
                    ),
                ),
            ]
        else:
            eastmoney_attempt = (
                "stock_zh_a_hist",
                lambda: ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=start, end_date=end, adjust=""
                ),
            )
            sina_attempt = (
                "stock_zh_a_daily",
                lambda: ak.stock_zh_a_daily(symbol=f"{exchange}{code}", adjust=""),
            )
            # ``stock_zh_a_hist`` is backed by Eastmoney push2his. When that edge
            # is in cooldown (see providers/eastmoney.py) the first attempt is
            # guaranteed to be dropped, so put the Sina-backed interface first
            # instead of spending a request that is known to fail.
            attempts = (
                [sina_attempt, eastmoney_attempt]
                if _eastmoney_throttled()
                else [eastmoney_attempt, sina_attempt]
            )
        return self._try_chain(spec, attempts)

    def _hk_history(self, spec: SymbolSpec) -> list[dict[str, Any]]:
        ak = _ak()
        code = (spec.akshare_code or "").zfill(5)
        return self._try_chain(
            spec,
            [
                ("stock_hk_daily", lambda: ak.stock_hk_daily(symbol=code, adjust="")),
            ],
        )

    def _hk_index_history(self, spec: SymbolSpec) -> list[dict[str, Any]]:
        ak = _ak()
        symbol = {"^HSI": "HSI", "^HSTECH": "HSTECH"}.get(spec.symbol)
        if symbol is None:
            raise ProviderError("akshare", f"不支持港股指数 {spec.symbol}。", kind="unsupported", symbol=spec.symbol)
        return self._try_chain(
            spec,
            [
                ("stock_hk_index_daily_sina", lambda: ak.stock_hk_index_daily_sina(symbol=symbol)),
            ],
        )

    def _us_history(self, spec: SymbolSpec) -> list[dict[str, Any]]:
        ak = _ak()
        ticker = spec.yahoo_symbol.replace("-", ".")
        return self._try_chain(
            spec,
            [
                ("stock_us_daily", lambda: ak.stock_us_daily(symbol=ticker, adjust="")),
            ],
        )

    def _us_index_history(self, spec: SymbolSpec) -> list[dict[str, Any]]:
        ak = _ak()
        symbol = _US_INDEX_CODES[spec.symbol]
        return self._try_chain(
            spec,
            [
                ("index_us_stock_sina", lambda: ak.index_us_stock_sina(symbol=symbol)),
            ],
        )

    def _commodity_history(self, spec: SymbolSpec) -> list[dict[str, Any]]:
        ak = _ak()
        symbol = _COMMODITY_CODES.get(spec.symbol.upper())
        if symbol is None:
            raise ProviderError(
                "akshare",
                f"不支持期货参考合约 {spec.symbol}；可用：{', '.join(sorted(set(_COMMODITY_CODES)))}",
                kind="unsupported",
                symbol=spec.symbol,
            )
        return self._try_chain(
            spec,
            [
                ("futures_foreign_hist", lambda: ak.futures_foreign_hist(symbol=symbol)),
            ],
        )


def stride_supported(spec: SymbolSpec) -> bool:
    """Return True when AKShare can serve *history* for this symbol."""
    if spec.market.value == "CN":
        return bool(spec.akshare_code and spec.akshare_exchange)
    if spec.market.value == "HK":
        return bool(spec.akshare_code) and spec.asset_type.value == "stock"
    if spec.market.value == "US":
        return spec.asset_type.value == "stock"
    return False


def _futures_timestamp(date_value: Any, time_value: Any) -> datetime | None:
    """Combine the futures ``日期`` / ``行情时间`` columns (Beijing time) into UTC."""
    if date_value is None:
        return None
    text_date = str(date_value).strip().replace("/", "-")[:10]
    text_time = str(time_value or "").strip()
    if len(text_time) == 5:
        text_time += ":00"
    try:
        local = datetime.strptime(f"{text_date} {text_time or '00:00:00'}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            local = datetime.strptime(text_date, "%Y-%m-%d")
        except ValueError:
            return None
    return local.replace(tzinfo=_TZ_SHANGHAI).astimezone(timezone.utc)
