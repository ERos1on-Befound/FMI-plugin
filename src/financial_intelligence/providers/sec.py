"""SEC EDGAR financial statements (US listed companies).

Data path
---------
``company_tickers.json``                -> ticker -> CIK
``api/xbrl/companyfacts/CIK##########`` -> every XBRL fact the company filed

The XBRL payload contains *all* historical filings, so we extract periods
ourselves:

* annual   -> ``form == "10-K"`` (or 20-F/40-F) with a duration of 330-400 days
* quarterly-> ``form == "10-Q"`` with a duration of 70-110 days (single quarter,
  not the year-to-date duplicate that many filings also tag)

SEC asks every client to send an identifying ``User-Agent`` and to stay well
below 10 requests/second.  We therefore make at most two requests per company
and cache both responses on disk (facts: 24h, ticker map: 7 days).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from ..config import Settings
from ..models.financial_statements import (
    BALANCE_FIELDS,
    CASHFLOW_FIELDS,
    FIELD_ALIASES,
    INCOME_FIELDS,
    FinancialStatement,
    canonicalize,
    derive_balance,
    derive_income,
    normalise_capex,
)
from ..schemas import AssetType, DataStatus, Market, iso, utc_now
from ..utils.cache import DiskCache
from ..utils.symbol_mapper import SymbolSpec
from .base import FinancialDataProvider, ProviderError, call_with_timeout

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_TICKERS_TTL = 7 * 24 * 3600.0
_FACTS_TTL = 24 * 3600.0
_ANNUAL_FORMS = {"10-K", "20-F", "40-F"}
_ANNUAL_RANGE = (330, 400)
_QUARTER_RANGE = (70, 110)
#: 10-Q cash-flow facts are year-to-date (3/6/9 months).
_QUARTER_CASH_RANGE = (70, 300)

_ALL_ALIASES = sorted(
    {
        alias
        for source in ("sec",)
        for field_aliases in FIELD_ALIASES[source].values()
        for alias in field_aliases
    }
    | set(INCOME_FIELDS)
    | set(BALANCE_FIELDS)
    | set(CASHFLOW_FIELDS)
)


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _duration_days(entry: dict[str, Any]) -> int | None:
    start = _parse_date(entry.get("start"))
    end = _parse_date(entry.get("end"))
    if start is None or end is None:
        return None
    return (end - start).days


class SecFinancialProvider(FinancialDataProvider):
    """Statement provider for companies filing with the SEC."""

    name = "sec"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._cache = DiskCache()
        self._client: httpx.Client | None = None

    # -- capability ------------------------------------------------------
    def supports_financials(self, spec: SymbolSpec) -> bool:
        return spec.market is Market.US and spec.asset_type is AssetType.STOCK

    # -- http ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.request_timeout,
                headers=self._headers(),
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get(self, url: str, *, symbol: str) -> Any:
        def call() -> httpx.Response:
            return self._http().get(url)

        response = call_with_timeout(call, self.settings.request_timeout + 5, self.name, symbol=symbol)
        if response.status_code == 404:
            raise ProviderError(
                self.name,
                f"SEC 没有该公司的 XBRL 数据（{url}）。可能它不是在美国 SEC 申报的公司，或代码不是美股代码。",
                kind="not_found",
                symbol=symbol,
            )
        if response.status_code in (403, 429):
            raise ProviderError(
                self.name,
                f"SEC 拒绝或限流了请求（HTTP {response.status_code}）。请把 FMI_SEC_USER_AGENT 设置为带真实联系方式的字符串后再试。",
                kind="rate_limit" if response.status_code == 429 else "forbidden",
                symbol=symbol,
            )
        if response.status_code != 200:
            raise ProviderError(
                self.name,
                f"SEC 返回 HTTP {response.status_code}（{url}）。",
                kind="http",
                symbol=symbol,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(self.name, f"SEC 返回的内容不是 JSON：{exc}", kind="parse", symbol=symbol)

    # -- resolution ------------------------------------------------------
    def _ticker_map(self) -> dict[str, tuple[int, str]]:
        cached = self._cache.get("sec", "company_tickers", _TICKERS_TTL)
        if cached is None:
            payload = self._get(TICKERS_URL, symbol="*")
            mapping: dict[str, list[Any]] = {}
            for row in payload.values():
                ticker = str(row.get("ticker", "")).upper()
                if ticker:
                    mapping[ticker] = [int(row["cik_str"]), str(row.get("title", ""))]
            cached = mapping
            self._cache.set("sec", "company_tickers", cached)
        return {ticker: (int(v[0]), str(v[1])) for ticker, v in cached.items()}

    def resolve(self, spec: SymbolSpec) -> tuple[int, str]:
        tickers = self._ticker_map()
        ticker = (spec.yahoo_symbol or spec.symbol).upper()
        if ticker not in tickers:
            raise ProviderError(
                self.name,
                f"SEC 的代码表里没有 '{ticker}'。请确认这是美股代码（例如 AAPL、MSFT、BRK-B）。",
                kind="not_found",
                symbol=spec.symbol,
            )
        return tickers[ticker]

    def _facts(self, cik: int, *, symbol: str) -> dict[str, Any]:
        key = f"CIK{cik:010d}"
        cached = self._cache.get("sec", key, _FACTS_TTL)
        if cached is None:
            cached = self._get(FACTS_URL.format(cik=cik), symbol=symbol)
            self._cache.set("sec", key, cached)
        return cached

    # -- extraction ------------------------------------------------------
    def _entries(self, facts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        entries: dict[str, list[dict[str, Any]]] = {}
        for tag, payload in us_gaap.items():
            items = []
            for unit, values in payload.get("units", {}).items():
                for value in values:
                    item = dict(value)
                    item["_unit"] = unit
                    items.append(item)
            if items:
                entries[tag] = items
        return entries

    def _pick(
        self,
        entries: dict[str, list[dict[str, Any]]],
        tag: str,
        end: date,
        *,
        target_days: int | None,
            window: tuple[int, int] | None,
    ) -> dict[str, Any] | None:
        """Best entry for ``tag`` at ``end``.

        ``target_days=None`` selects instant (balance sheet) facts only.
        """

        best: dict[str, Any] | None = None
        best_score: float | None = None
        for entry in entries.get(tag, []):
            if _parse_date(entry.get("end")) != end:
                continue
            days = _duration_days(entry)
            if target_days is None:
                if days is not None:
                    continue
                score = 0.0
            else:
                if days is None:
                    continue
                if window is not None and not (window[0] <= days <= window[1]):
                    continue
                score = abs(days - target_days)
            if best_score is None or score < best_score:
                best, best_score = entry, score
        return best

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
                f"SEC 数据源只支持 annual / quarterly 两种报告期（收到 '{period_type}'）。",
                kind="unsupported",
                symbol=spec.symbol,
            )
        cik, title = self.resolve(spec)
        facts = self._facts(cik, symbol=spec.symbol)
        entries = self._entries(facts)
        company_name = facts.get("entityName") or title or None

        if period_type == "annual":
            forms, window, target = _ANNUAL_FORMS, _ANNUAL_RANGE, 365
            cash_window, cash_target = _ANNUAL_RANGE, 365
        else:
            forms, window, target = {"10-Q"}, _QUARTER_RANGE, 91
            # A 10-Q cash-flow statement is year-to-date (3, 6 or 9 months), so it
            # needs a wider window; ``cumulative`` records what we actually got.
            cash_window, cash_target = _QUARTER_CASH_RANGE, 91

        # Candidate period ends: any duration fact filed with the wanted form.
        ends: set[date] = set()
        widest = max(window[1], cash_window[1])
        widest_low = min(window[0], cash_window[0])
        for tag in _ALL_ALIASES:
            for entry in entries.get(tag, []):
                if str(entry.get("form")) not in forms:
                    continue
                days = _duration_days(entry)
                if days is None or not (widest_low <= days <= widest):
                    continue
                end = _parse_date(entry.get("end"))
                if end is not None:
                    ends.add(end)

        statements: list[FinancialStatement] = []
        for end in sorted(ends, reverse=True):
            raw: dict[str, dict[str, Any]] = {"income": {}, "balance": {}, "cashflow": {}}
            chosen: list[dict[str, Any]] = []
            days_by_kind: dict[str, list[int]] = {"income": [], "balance": [], "cashflow": []}
            starts_by_kind: dict[str, list[date]] = {"income": [], "balance": [], "cashflow": []}
            for kind, fields in (
                ("income", INCOME_FIELDS),
                ("balance", BALANCE_FIELDS),
                ("cashflow", CASHFLOW_FIELDS),
            ):
                if kind == "balance":
                    kind_target, kind_window = None, None
                elif kind == "cashflow":
                    kind_target, kind_window = cash_target, cash_window
                else:
                    kind_target, kind_window = target, window
                for field in fields:
                    for tag in FIELD_ALIASES["sec"].get(field, []):
                        entry = self._pick(
                            entries,
                            tag,
                            end,
                            target_days=kind_target,
                            window=kind_window,
                        )
                        if entry is not None:
                            raw[kind][tag] = entry.get("val")
                            chosen.append(entry)
                            days = _duration_days(entry)
                            if days is not None:
                                days_by_kind[kind].append(days)
                                start = _parse_date(entry.get("start"))
                                if start is not None:
                                    starts_by_kind[kind].append(start)
                            break
            if not raw["income"] and not raw["balance"]:
                continue

            # The income-statement period defines the reporting period; a 10-Q cash
            # flow can span the year to date, which is recorded in ``notes``.
            income_starts = sorted(starts_by_kind["income"])
            starts = income_starts or sorted(
                d for values in starts_by_kind.values() for d in values
            )
            # ``filed`` must be *this* period's filing.  companyfacts keeps every
            # filing that mentioned a fact, including comparatives restated in the
            # next 10-K, so the earliest filing is the original one.
            filed = sorted(
                d for d in (_parse_date(e.get("filed")) for e in chosen) if d is not None
            )
            currency = "UNKNOWN"
            for source_kind in ("income", "balance", "cashflow"):
                if source_kind == "balance":
                    days_target, days_window = None, None
                elif source_kind == "cashflow":
                    days_target, days_window = cash_target, cash_window
                else:
                    days_target, days_window = target, window
                for tag in raw[source_kind]:
                    unit = _find_unit(entries, tag, end, days_target, days_window)
                    if unit and "/" not in unit:
                        currency = unit.upper()
                        break
                if currency != "UNKNOWN":
                    break

            income_canon = canonicalize("income", raw["income"], "sec")
            balance_canon = canonicalize("balance", raw["balance"], "sec")
            cash_canon = canonicalize("cashflow", raw["cashflow"], "sec")
            derived = derive_income(income_canon.values) + derive_balance(balance_canon.values)
            cash_canon.values["capital_expenditure"] = normalise_capex(
                cash_canon.values.get("capital_expenditure")
            )

            cash_days = max(days_by_kind["cashflow"]) if days_by_kind["cashflow"] else None
            cumulative = bool(period_type == "quarterly" and cash_days and cash_days > 135)
            fiscal_year, fiscal_period = _fiscal_period_of(chosen, end, period_type)
            notes = [
                "数据来自 SEC EDGAR companyfacts XBRL 接口（10-K/10-Q 原始申报值）。",
                f"报告期结束 {end.isoformat()}，申报日 {filed[0].isoformat() if filed else '未知'}。",
            ]
            if cumulative:
                notes.append(
                    f"现金流量表为年初至今累计口径（{cash_days} 天），不是单季数据；"
                    "如需单季请与上一期累计值相减。"
                )
            merged = {**income_canon.values, **balance_canon.values, **cash_canon.values}
            missing = [
                field
                for field in (
                    income_canon.missing_fields
                    + balance_canon.missing_fields
                    + cash_canon.missing_fields
                )
                if merged.get(field) is None
            ]
            statements.append(
                FinancialStatement(
                    symbol=spec.symbol,
                    company_name=company_name,
                    market=Market.US,
                    currency=currency,
                    unit=currency,
                    unit_scale=1.0,
                    fiscal_period=fiscal_period,
                    fiscal_year=fiscal_year,
                    period_type=period_type,  # type: ignore[arg-type]
                    cumulative=cumulative,
                    period_start=starts[0] if starts else None,
                    period_end=end,
                    filing_date=filed[0] if filed else None,
                    source=self.name,
                    income_statement=income_canon.values,
                    balance_sheet=balance_canon.values,
                    cash_flow_statement=cash_canon.values,
                    raw_items={
                        "income": {k: _to_float(v) for k, v in raw["income"].items()},
                        "balance": {k: _to_float(v) for k, v in raw["balance"].items()},
                        "cashflow": {k: _to_float(v) for k, v in raw["cashflow"].items()},
                    },
                    matched_items={
                        "income": income_canon.matched_items,
                        "balance": balance_canon.matched_items,
                        "cashflow": cash_canon.matched_items,
                    },
                    derived_fields=derived,
                    missing_fields=missing,
                    data_status=DataStatus.HISTORICAL,
                    retrieved_at=iso(utc_now()) or "",
                    notes=notes,
                )
            )
            if len(statements) >= max(limit, 1):
                break

        if not statements:
            raise ProviderError(
                self.name,
                f"SEC 有该公司的 XBRL 数据，但没有解析出可用的 {period_type} 报告期。",
                kind="empty",
                symbol=spec.symbol,
            )
        return statements


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fiscal_period_of(
    entries: list[dict[str, Any]], end: date, period_type: str
) -> tuple[int, str]:
    """Fiscal year and label for ``end``.

    companyfacts stores the same period once per filing that mentions it, and a
    comparative copy carries the *later* filing's ``fy``/``fp``.  The earliest
    filing is therefore the one that labels the period correctly (Apple's
    FY2024 10-K ends 2024-09-28 but its FY2026 Q1 10-Q also repeats that date).
    """
    dated = sorted(
        ((stamp, entry) for entry in entries if (stamp := _parse_date(entry.get("filed"))) is not None),
        key=lambda pair: pair[0],
    )
    for _, entry in dated:
        fy_raw = entry.get("fy")
        if fy_raw is None:
            continue
        try:
            fy = int(fy_raw)
        except (TypeError, ValueError):
            continue
        fp = str(entry.get("fp") or "").upper()
        if period_type == "annual" and fp == "FY":
            return fy, f"FY{fy}"
        if period_type == "quarterly" and fp.startswith("Q") and fp[1:].isdigit():
            return fy, f"{fy}Q{int(fp[1:])}"
    if period_type == "annual":
        return end.year, f"FY{end.year}"
    return end.year, f"{end.year}Q{(end.month - 1) // 3 + 1}"


def _find_unit(
    entries: dict[str, list[dict[str, Any]]],
    tag: str,
    end: date,
    target_days: int | None,
    window: tuple[int, int] | None,
) -> str | None:
    for entry in entries.get(tag, []):
        if _parse_date(entry.get("end")) != end:
            continue
        days = _duration_days(entry)
        if target_days is None:
            if days is not None:
                continue
        else:
            if days is None or (window is not None and not (window[0] <= days <= window[1])):
                continue
        unit = entry.get("_unit")
        if unit:
            return str(unit)
    return None
