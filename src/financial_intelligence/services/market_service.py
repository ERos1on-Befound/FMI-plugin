"""Market data service: the single place where providers are orchestrated.

Responsibilities
----------------
* Resolve a raw user symbol into a :class:`SymbolSpec` (via ``symbol_mapper``).
* Ask every *capable* provider in the configured order; on failure, fall through
  to the next provider and keep **every** failure reason for the final report.
* Cache successful results for a short TTL (quotes 60s / history 300s by default)
  and mark cache hits with ``from_cache=True`` while keeping the original market
  ``timestamp`` intact.
* Never invent data: when nothing works the caller gets an explicit
  ``data_status=unavailable`` record whose ``error`` explains what failed.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable, Sequence

from ..config import (
    SUPPORTED_INTERVALS,
    SUPPORTED_PERIODS,
    Settings,
    load_settings,
)
from ..providers import MarketDataProvider, ProviderError, build_providers
from ..schemas import (
    AssetType,
    DataStatus,
    HistoricalData,
    Market,
    MarketOverview,
    OverviewGroup,
    Quote,
    ServerStatus,
    SupportedMarkets,
    iso,
    utc_now,
)
from ..utils.symbol_mapper import SYMBOL_HELP, map_symbol
from ..utils.validators import InputError, parse_asset_type, parse_market, parse_period, parse_interval, validate_period_interval

logger = logging.getLogger(__name__)

#: Fixed reference assets of ``get_market_overview``.
OVERVIEW_GROUPS: tuple[tuple[str, Market, tuple[str, ...]], ...] = (
    ("A股", Market.CN, ("000001.SS", "399001.SZ", "000300.SS")),
    ("港股", Market.HK, ("^HSI", "^HSTECH")),
    ("美股", Market.US, ("^GSPC", "^IXIC", "^DJI")),
    ("大宗商品（期货参考价）", Market.COMMODITY, ("GC=F", "CL=F", "BZ=F")),
)

_MARKET_LABELS = {
    "CN": "中国内地（A股/ETF/指数）",
    "HK": "香港",
    "US": "美国",
    "COMMODITY": "国际大宗商品（期货参考）",
}


class MarketService:
    """Thin orchestration layer used by every MCP tool."""

    def __init__(
        self,
        settings: Settings | None = None,
        providers: Sequence[MarketDataProvider] | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.providers: list[MarketDataProvider] = (
            list(providers) if providers is not None else build_providers(self.settings)
        )
        self._quote_cache: dict[tuple[str, str], tuple[float, Quote]] = {}
        self._history_cache: dict[tuple[str, str, str, str], tuple[float, HistoricalData]] = {}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def get_server_status(self) -> ServerStatus:
        notes = [
            "所有报价都来自免费公开接口，没有付费数据源，也没有交易所直连。",
            "yfinance / AKShare / 新浪财经的快照都**不能证明**是实时行情，因此报价一律不标 realtime。",
            "期货代码（如 GC=F / CL=F / BZ=F）返回的是期货合约参考价，不是现货价格。",
            "本服务只做数据检索，不构成任何投资建议。",
        ]
        modules = ["config", "schemas", "providers", "services", "utils"]
        return ServerStatus(
            server_name=self.settings.server_name,
            version=self.settings.version,
            status="ok",
            current_time=iso(utc_now()) or "",
            providers=[provider.name for provider in self.providers],
            markets=[market.value for market in Market if market is not Market.UNKNOWN],
            asset_types=[item.value for item in AssetType if item is not AssetType.UNKNOWN],
            modules=modules,
            notes=notes,
        )

    def get_supported_markets(self) -> SupportedMarkets:
        notes = [
            "A股：沪市 6 位代码以 .SS 结尾（600519.SS），深市以 .SZ 结尾（000001.SZ）。",
            "港股：4-5 位数字代码，可写 0700.HK 或 700.HK（内部补零为 00700.HK）。",
            "美股：字母代码，如 AAPL / NVDA；指数用 ^GSPC / ^IXIC / ^DJI。",
            "指数别名：上证指数 / 深证成指 / 沪深300 / 恒生指数 / 恒生科技 / 标普500 / 纳斯达克 / 道琼斯。",
            "大宗商品：使用期货代码 GC=F（黄金）/ CL=F（WTI原油）/ BZ=F（布伦特原油），返回期货参考价而非现货价。",
            "1wk / 1mo 的K线由日线在服务端聚合得到；period=1mo 时不支持 interval=1mo。",
        ]
        return SupportedMarkets(
            markets=[_MARKET_LABELS.get(market.value, market.value) for market in Market if market is not Market.UNKNOWN],
            asset_types=[item.value for item in AssetType if item is not AssetType.UNKNOWN],
            providers=[provider.name for provider in self.providers],
            periods=list(SUPPORTED_PERIODS),
            intervals=list(SUPPORTED_INTERVALS),
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------
    def get_quote(
        self,
        symbol: str,
        market: str | None = None,
        asset_type: str | None = None,
    ) -> Quote:
        """Return a unified quote for ``symbol``.

        Never raises for data problems: an unavailable quote documents the reason.
        """
        spec = map_symbol(symbol, market=parse_market(market), asset_type=parse_asset_type(asset_type))

        cache_key = (spec.symbol, spec.market.value)
        cached = self._cache_get(self._quote_cache, cache_key, self.settings.cache_ttl)
        if cached is not None:
            logger.debug("quote cache hit: %s", spec.symbol)
            return cached.model_copy(update={"from_cache": True, "retrieved_at": iso(utc_now()) or ""})

        reasons: list[str] = []
        for provider in self.providers:
            if not provider.supports_quote(spec):
                reasons.append(f"{provider.name}: 不提供该资产的报价")
                continue
            try:
                quote = provider.get_quote(spec)
            except ProviderError as exc:
                reasons.append(str(exc))
                logger.info("quote provider failed: %s", exc)
                continue
            except Exception as exc:  # noqa: BLE001 - providers must never crash the server
                wrapped = ProviderError.from_exception(provider.name, exc, symbol=spec.symbol)
                reasons.append(str(wrapped))
                logger.warning("quote provider crashed: %s", wrapped)
                continue

            if quote.price is None:
                reasons.append(f"{provider.name}: {quote.error or '未返回可用价格'}")
                continue

            self._quote_cache[cache_key] = (time.monotonic(), quote)
            return quote

        return Quote.unavailable(
            symbol=spec.symbol,
            name=spec.name_hint,
            market=spec.market,
            asset_type=spec.asset_type,
            currency=spec.currency,
            source="unavailable",
            error="所有数据源都无法提供该报价 -> " + " | ".join(reasons) if reasons else "没有启用的数据源。",
        )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def get_historical_data(self, symbol: str, period: str = "1mo", interval: str = "1d") -> HistoricalData:
        spec = map_symbol(symbol)
        clean_period = parse_period(period)
        clean_interval = parse_interval(interval)
        validate_period_interval(clean_period, clean_interval)

        cache_key = (spec.symbol, spec.market.value, clean_period, clean_interval)
        cached = self._cache_get(self._history_cache, cache_key, self.settings.history_cache_ttl)
        if cached is not None:
            return cached.model_copy(update={"from_cache": True, "retrieved_at": iso(utc_now()) or ""})

        reasons: list[str] = []
        for provider in self.providers:
            if not provider.supports_history(spec):
                reasons.append(f"{provider.name}: 不提供该资产的历史数据")
                continue
            try:
                history = provider.get_history(spec, clean_period, clean_interval)
            except ProviderError as exc:
                reasons.append(str(exc))
                logger.info("history provider failed: %s", exc)
                continue
            except Exception as exc:  # noqa: BLE001
                wrapped = ProviderError.from_exception(provider.name, exc, symbol=spec.symbol)
                reasons.append(str(wrapped))
                logger.warning("history provider crashed: %s", wrapped)
                continue

            self._history_cache[cache_key] = (time.monotonic(), history)
            return history

        return HistoricalData(
            symbol=spec.symbol,
            name=spec.name_hint,
            market=spec.market,
            asset_type=spec.asset_type,
            currency=spec.currency,
            period=clean_period,
            interval=clean_interval,
            source="unavailable",
            data_status=DataStatus.UNAVAILABLE,
            retrieved_at=iso(utc_now()) or "",
            bars=[],
            count=0,
            error="所有数据源都无法提供该历史数据 -> " + " | ".join(reasons) if reasons else "没有启用的数据源。",
        )

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------
    def get_market_overview(self) -> MarketOverview:
        groups: list[OverviewGroup] = []
        ok = 0
        missing = 0
        for label, market, symbols in OVERVIEW_GROUPS:
            items: list[Quote] = []
            for symbol in symbols:
                quote = self.get_quote(symbol, market=market.value)
                if quote.price is None:
                    missing += 1
                else:
                    ok += 1
                items.append(quote)
            groups.append(OverviewGroup(label=label, market=market, items=items))

        notes = [
            "概览中的每个条目都是独立请求，单个资产失败不会影响其他资产（失败原因见该条目的 error 字段）。",
            "美元/港元指数与期货的涨跌幅按各自币种计算，跨市场比较前请自行统一币种。",
            "期货价格为近月连续合约参考价，不是现货价格。",
            "本概览只做数据呈现，不构成投资建议。",
        ]
        return MarketOverview(
            title="市场概览（A股 / 港股 / 美股 / 大宗商品）",
            retrieved_at=iso(utc_now()) or "",
            groups=groups,
            ok_count=ok,
            unavailable_count=missing,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _cache_get(store: dict, key: tuple, ttl: float):
        if ttl <= 0:  # ttl=0 disables caching entirely
            store.pop(key, None)
            return None
        entry = store.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if (time.monotonic() - stored_at) > ttl:
            store.pop(key, None)
            return None
        return value

    def clear_cache(self) -> None:
        self._quote_cache.clear()
        self._history_cache.clear()

    def symbol_help(self) -> str:
        return SYMBOL_HELP


def market_label(market: Market) -> str:
    return _MARKET_LABELS.get(market.value, market.value)


def group_quotes(quotes: Iterable[Quote]) -> dict[str, list[Quote]]:
    """Group quotes by market, preserving insertion order (helper for examples)."""
    grouped: dict[str, list[Quote]] = {}
    for quote in quotes:
        grouped.setdefault(quote.market.value, []).append(quote)
    return grouped
