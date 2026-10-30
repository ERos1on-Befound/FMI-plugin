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

from ..models.financial_statements import (
    BALANCE_FIELDS,
    CASHFLOW_FIELDS,
    INCOME_FIELDS,
    FinancialStatement,
    canonicalize,
    derive_balance,
    derive_income,
    normalise_capex,
)
from ..schemas import AssetType, DataStatus, HistoricalData, Market, Quote, iso, utc_now
from ..utils.bars import PERIOD_DAYS, frame_to_bars
from ..utils.symbol_mapper import SymbolSpec
from ..utils.validators import to_float
from .base import (
    FinancialDataProvider,
    MarketDataProvider,
    ProviderError,
    assemble_history,
    call_with_timeout,
)

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

# ---------------------------------------------------------------------------
# Published financial statements (A-share via Sina mirror, HK via HKEX mirror)
# ---------------------------------------------------------------------------

_CN_STATEMENT_NAMES = {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}
_CN_META_COLUMNS = {"报告日", "数据源", "是否审计", "公告日期", "币种", "类型", "更新日期"}
#: HK report-type codes used by ``stock_financial_hk_report_em``:
#: 001 = annual, 002 = interim, 003 = first quarter, 004 = third quarter.
_HK_QUARTERLY_TYPES = {"002", "003", "004"}
#: Currency labels the mirrors use, normalised to ISO-4217 codes.
_CURRENCY_ALIASES = {
    "人民币": "CNY",
    "元": "CNY",
    "rmb": "CNY",
    "港币": "HKD",
    "港元": "HKD",
    "hkd": "HKD",
    "美元": "USD",
    "美金": "USD",
    "usd": "USD",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "--", "-"}:
        return ""
    return text


def _iso_currency(value: Any) -> str | None:
    """Normalise ``人民币`` / ``港币`` / ``CNY`` … to an ISO-4217 code."""
    text = _clean_text(value)
    if not text:
        return None
    mapped = _CURRENCY_ALIASES.get(text.lower(), _CURRENCY_ALIASES.get(text))
    if mapped:
        return mapped
    if len(text) == 3 and text.isalpha():
        return text.upper()
    return None


class AkshareFinancialProvider(FinancialDataProvider):
    """Statements for A-share and Hong Kong companies, through AKShare.

    * **A-share** — ``stock_financial_report_sina``: the periodic reports as
      mirrored by Sina (originally filed on the exchanges / CNINFO).  Each row is
      one reporting period; the quarter figures are *year to date*.
    * **Hong Kong** — ``stock_financial_hk_report_em``: the HKEX filings mirrored
      by Eastmoney's datacenter host (a different, non-push2 host, so it is not
      affected by the quote-endpoint throttling).

    Amounts stay in the currency the filing uses, in whole currency units
    (``unit_scale`` = 1.0); nothing is silently converted or rescaled.
    """

    name = "akshare"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._frames: dict[str, tuple[float, Any]] = {}

    # -- capability ------------------------------------------------------
    def supports_financials(self, spec: SymbolSpec) -> bool:
        if spec.asset_type is not AssetType.STOCK:
            return False
        if spec.market is Market.CN:
            return bool(spec.akshare_code and spec.akshare_exchange)
        if spec.market is Market.HK:
            return bool(spec.akshare_code)
        return False

    def get_financials(
        self,
        spec: SymbolSpec,
        *,
        period_type: str = "annual",
        limit: int = 5,
    ) -> list[FinancialStatement]:
        if period_type not in {"annual", "quarterly"}:
            raise ProviderError(
                self.name,
                f"AKShare 财务接口只支持 annual / quarterly（收到 '{period_type}'）；"
                "港股与 A 股没有可靠的免费 TTM 拼接数据。",
                kind="unsupported",
                symbol=spec.symbol,
            )
        if spec.market is Market.CN:
            return self._cn_statements(spec, period_type, limit)
        if spec.market is Market.HK:
            return self._hk_statements(spec, period_type, limit)
        raise ProviderError(
            self.name,
            f"AKShare 财报接口不覆盖 {spec.market.value} 市场的 {spec.symbol}。",
            kind="unsupported",
            symbol=spec.symbol,
        )

    # -- frame cache -----------------------------------------------------
    def _cached(self, key: str) -> Any | None:
        entry = self._frames.get(key)
        if entry is None:
            return None
        stored, frame = entry
        ttl = getattr(self.settings, "financial_cache_ttl", 12 * 3600.0)
        if ttl <= 0:
            self._frames.pop(key, None)
            return None
        import time

        if time.monotonic() - stored > ttl:
            self._frames.pop(key, None)
            return None
        return frame

    def _remember(self, key: str, frame: Any) -> Any:
        import time

        self._frames[key] = (time.monotonic(), frame)
        return frame

    # -- A-share ---------------------------------------------------------
    def _cn_frame(self, spec: SymbolSpec, kind: str) -> Any:
        code = f"{spec.akshare_exchange}{spec.akshare_code}"
        key = f"cn:{code}:{kind}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        ak = _ak()
        name = _CN_STATEMENT_NAMES[kind]
        try:
            frame = call_with_timeout(
                lambda: ak.stock_financial_report_sina(stock=code, symbol=name),
                self.settings.request_timeout + 20,
                self.name,
                symbol=spec.symbol,
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise ProviderError(
                self.name,
                f"AKShare 财报接口 stock_financial_report_sina({code}, {name}) 失败：{exc}",
                kind="error",
                symbol=spec.symbol,
            ) from exc
        return self._remember(key, frame)

    def _cn_statements(
        self, spec: SymbolSpec, period_type: str, limit: int
    ) -> list[FinancialStatement]:
        periods: dict[str, dict[str, Any]] = {}
        for kind in ("income", "balance", "cashflow"):
            frame = self._cn_frame(spec, kind)
            for _, row in frame.iterrows():
                raw = _clean_text(row.get("报告日"))
                if len(raw) != 8 or not raw.isdigit():
                    continue
                periods.setdefault(raw, {})[kind] = row

        if period_type == "annual":
            wanted = [key for key in sorted(periods, reverse=True) if key.endswith("1231")]
        else:
            wanted = [key for key in sorted(periods, reverse=True) if not key.endswith("1231")]
        wanted = wanted[: max(limit, 1)]
        if not wanted:
            raise ProviderError(
                self.name,
                f"AKShare 返回了 {spec.symbol} 的财报，但没有 {period_type} 报告期。",
                kind="empty",
                symbol=spec.symbol,
            )

        statements: list[FinancialStatement] = []
        for raw in wanted:
            rows = periods[raw]
            end = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            start = date(end.year, 1, 1)
            days = (end - start).days + 1
            cumulative = period_type == "quarterly" and days > 120
            currency = "CNY"
            updated = None
            audited = None

            canon: dict[str, Any] = {}
            raw_items: dict[str, dict[str, float | None]] = {}
            matched: dict[str, dict[str, str]] = {}
            for kind, source in (("income", "cn_sina"), ("balance", "cn_sina"), ("cashflow", "cn_sina")):
                row = rows.get(kind)
                if row is None:
                    canon[kind] = canonicalize(kind, {}, source)
                    raw_items[kind] = {}
                    matched[kind] = {}
                    continue
                items = {
                    _clean_text(column): value
                    for column, value in row.items()
                    if _clean_text(column) and _clean_text(column) not in _CN_META_COLUMNS
                }
                currency = _iso_currency(row.get("币种")) or currency
                # ``公告日期`` repeats per report *type* (every annual row carries
                # the latest annual announcement date), so it is not a reliable
                # per-period filing date.  ``更新日期`` is per row and we surface it
                # as context instead of pretending it is the announcement date.
                raw_updated = _clean_text(row.get("更新日期"))[:10]
                if raw_updated:
                    updated = raw_updated
                audited = _clean_text(row.get("是否审计")) or audited
                result = canonicalize(kind, items, source)
                canon[kind] = result
                raw_items[kind] = {key: to_float(value) for key, value in items.items() if to_float(value) is not None}
                matched[kind] = result.matched_items

            income = canon["income"]
            balance = canon["balance"]
            cash = canon["cashflow"]
            derived = derive_income(income.values) + derive_balance(balance.values)
            cash.values["capital_expenditure"] = normalise_capex(cash.values.get("capital_expenditure"))
            merged = {**income.values, **balance.values, **cash.values}
            missing = [
                field
                for field in (income.missing_fields + balance.missing_fields + cash.missing_fields)
                if merged.get(field) is None
            ]

            if period_type == "annual":
                fiscal_period = f"FY{end.year}"
            else:
                fiscal_period = f"{end.year}Q{(end.month - 1) // 3 + 1}"

            notes = [
                "数据来自 AKShare stock_financial_report_sina（交易所定期报告的镜像）。",
                f"报告期 {start.isoformat()} ~ {end.isoformat()}。",
                "该镜像不提供可靠的逐期公告日（公告日期字段按报告类型重复），因此 filing_date 留空"
                + (f"；镜像更新日期 {updated}。" if updated else "。"),
            ]
            if audited:
                notes.append(f"审计状态：{audited}。")
            if cumulative:
                notes.append(
                    f"该期为年初至今累计口径（{days} 天），不是单季数据；单季需与上一期累计值相减。"
                )

            statements.append(
                FinancialStatement(
                    symbol=spec.symbol,
                    company_name=spec.name_hint,
                    market=Market.CN,
                    currency=currency,
                    unit=currency,
                    unit_scale=1.0,
                    fiscal_period=fiscal_period,
                    fiscal_year=end.year,
                    period_type=period_type,  # type: ignore[arg-type]
                    cumulative=cumulative,
                    period_start=start,
                    period_end=end,
                    filing_date=None,
                    source=self.name,
                    income_statement=income.values,
                    balance_sheet=balance.values,
                    cash_flow_statement=cash.values,
                    raw_items=raw_items,
                    matched_items=matched,
                    derived_fields=derived,
                    missing_fields=missing,
                    data_status=DataStatus.HISTORICAL,
                    retrieved_at=iso(utc_now()) or "",
                    notes=notes,
                )
            )
        return statements

    # -- Hong Kong -------------------------------------------------------
    def _hk_frame(self, spec: SymbolSpec, kind: str, indicator: str) -> Any:
        # HK financial endpoints want the 5-digit code ("00700"), while the quote
        # and history endpoints accept the 4-digit form used elsewhere.
        code = str(spec.akshare_code or "").zfill(5)
        key = f"hk:{code}:{kind}:{indicator}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        ak = _ak()
        name = _CN_STATEMENT_NAMES[kind]
        try:
            frame = call_with_timeout(
                lambda: ak.stock_financial_hk_report_em(
                    stock=code, symbol=name, indicator=indicator
                ),
                self.settings.request_timeout + 20,
                self.name,
                symbol=spec.symbol,
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                self.name,
                f"AKShare 港股财报接口 stock_financial_hk_report_em({code}, {name}, {indicator}) 失败：{exc}",
                kind="error",
                symbol=spec.symbol,
            ) from exc
        return self._remember(key, frame)

    def _hk_currency(self, spec: SymbolSpec) -> str:
        code = str(spec.akshare_code or "").zfill(5)
        key = f"hk:{code}:currency"
        cached = self._cached(key)
        if cached is not None:
            return str(cached)
        ak = _ak()
        try:
            frame = call_with_timeout(
                lambda: ak.stock_financial_hk_analysis_indicator_em(
                    symbol=code, indicator="年度"
                ),
                self.settings.request_timeout + 20,
                self.name,
                symbol=spec.symbol,
            )
        except Exception:  # noqa: BLE001 - currency detection is best effort
            return "HKD"
        currency = "HKD"
        if frame is not None and "CURRENCY" in getattr(frame, "columns", []):
            values = [_iso_currency(value) for value in frame["CURRENCY"].tolist()]
            values = [value for value in values if value]
            if values:
                currency = values[0]
        self._remember(key, currency)
        return currency

    def _hk_statements(
        self, spec: SymbolSpec, period_type: str, limit: int
    ) -> list[FinancialStatement]:
        indicator = "年度" if period_type == "annual" else "报告期"
        currency = self._hk_currency(spec) if period_type == "annual" else "HKD"
        periods: dict[str, dict[str, dict[str, float]]] = {}
        starts: dict[str, date] = {}
        for kind in ("income", "balance", "cashflow"):
            frame = self._hk_frame(spec, kind, indicator)
            if frame is None or getattr(frame, "empty", True):
                continue
            for _, row in frame.iterrows():
                raw = _clean_text(row.get("REPORT_DATE"))[:10]
                item = _clean_text(row.get("STD_ITEM_NAME"))
                code = _clean_text(row.get("DATE_TYPE_CODE"))
                amount = to_float(row.get("AMOUNT"))
                if not raw or not item or amount is None:
                    continue
                if period_type == "annual" and code not in {"001", ""}:
                    continue
                if period_type == "quarterly" and code not in _HK_QUARTERLY_TYPES:
                    continue
                periods.setdefault(raw, {}).setdefault(kind, {})[item] = amount
                start_text = _clean_text(row.get("START_DATE"))[:10]
                if start_text and raw not in starts:
                    try:
                        starts[raw] = date.fromisoformat(start_text)
                    except ValueError:
                        pass

        wanted = [key for key in sorted(periods, reverse=True)][: max(limit, 1)]
        if not wanted:
            raise ProviderError(
                self.name,
                f"AKShare 返回了 {spec.symbol} 的港股财报，但没有 {period_type} 报告期。",
                kind="empty",
                symbol=spec.symbol,
            )

        statements: list[FinancialStatement] = []
        for raw in wanted:
            rows = periods[raw]
            end = date.fromisoformat(raw)
            start = starts.get(raw) or date(end.year, 1, 1)
            days = (end - start).days + 1
            cumulative = period_type == "quarterly" and days > 120
            canon: dict[str, Any] = {}
            raw_items: dict[str, dict[str, float | None]] = {}
            matched: dict[str, dict[str, str]] = {}
            for kind, source in (("income", "hk_em"), ("balance", "hk_em"), ("cashflow", "hk_em")):
                items = rows.get(kind, {})
                result = canonicalize(kind, items, source)
                canon[kind] = result
                raw_items[kind] = dict(items)
                matched[kind] = result.matched_items

            income = canon["income"]
            balance = canon["balance"]
            cash = canon["cashflow"]
            derived = derive_income(income.values) + derive_balance(balance.values)
            cash.values["capital_expenditure"] = normalise_capex(cash.values.get("capital_expenditure"))
            merged = {**income.values, **balance.values, **cash.values}
            missing = [
                field
                for field in (income.missing_fields + balance.missing_fields + cash.missing_fields)
                if merged.get(field) is None
            ]

            if period_type == "annual":
                fiscal_period = f"FY{end.year}"
            else:
                fiscal_period = f"{end.year}Q{(end.month - 1) // 3 + 1}"

            statements.append(
                FinancialStatement(
                    symbol=spec.symbol,
                    company_name=spec.name_hint,
                    market=Market.HK,
                    currency=currency,
                    unit=currency,
                    unit_scale=1.0,
                    fiscal_period=fiscal_period,
                    fiscal_year=end.year,
                    period_type=period_type,  # type: ignore[arg-type]
                    cumulative=cumulative,
                    period_start=start,
                    period_end=end,
                    filing_date=None,
                    source=self.name,
                    income_statement=income.values,
                    balance_sheet=balance.values,
                    cash_flow_statement=cash.values,
                    raw_items=raw_items,
                    matched_items=matched,
                    derived_fields=derived,
                    missing_fields=missing,
                    data_status=DataStatus.HISTORICAL,
                    retrieved_at=iso(utc_now()) or "",
                    notes=[
                        "数据来自 AKShare stock_financial_hk_report_em（港交所披露易定期报告的镜像）。",
                        f"报告期 {start.isoformat()} ~ {end.isoformat()}；该接口不返回公告日期，"
                        "filing_date 留空。",
                    ],
                )
            )
        return statements
