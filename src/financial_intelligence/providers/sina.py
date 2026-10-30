"""Sina snapshot quote provider.

Why this provider exists: Yahoo Finance rate-limits some networks aggressively and
Eastmoney's push2 endpoints can be throttled too, but Sina's public quote text
feed (``hq.sinajs.cn``) stays reachable and answers in well under a second for
A-shares, Hong Kong stocks, US stocks/indices and foreign futures.

The feed is a public snapshot, **not** an exchange realtime licence, therefore
every quote produced here is reported as ``delayed`` (never ``realtime``).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..schemas import DataStatus, HistoricalData, Quote, iso, utc_now
from ..utils.symbol_mapper import SymbolSpec
from ..utils.validators import to_float
from .base import MarketDataProvider, ProviderError, call_with_timeout

SINA_URL = "https://hq.sinajs.cn/list={codes}"
_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LINE_RE = re.compile(r'var hq_str_([A-Za-z0-9_$]+)\s*=\s*"(.*)";')

#: Yahoo-style US index tickers -> Sina ``gb_$`` codes.
_US_INDEX_CODES = {
    "^GSPC": "gb_$inx",
    "^SPX": "gb_$inx",
    "^IXIC": "gb_$ixic",
    "^DJI": "gb_$dji",
    "^NDX": "gb_$ndx",
}
#: Hang Seng family indices.
_HK_INDEX_CODES = {"^HSI": "hkHSI", "^HSTECH": "hkHSTECH"}
#: Futures reference contracts -> Sina foreign futures (``hf_``) codes.
_COMMODITY_CODES = {
    "GC=F": "hf_GC",  # COMEX gold
    "CL=F": "hf_CL",  # NYMEX WTI
    "BZ=F": "hf_OIL",  # Brent
    "SI=F": "hf_SI",  # COMEX silver
    "HG=F": "hf_HG",  # COMEX copper
    "NG=F": "hf_NG",  # NYMEX natural gas
    "PL=F": "hf_PL",
    "PA=F": "hf_PA",
}

_FAMILY_NOTE = {
    "cn": "Sina 公开行情快照（A股/指数），非交易所直连，标记为 delayed。",
    "hk": "Sina 公开行情快照（港股），非交易所直连，标记为 delayed。",
    "us": "Sina 公开行情快照（美股），非交易所直连，标记为 delayed。",
    "hf": "外盘期货近月连续合约参考价，不是现货价格；合约/单位以交易所为准。",
}


def sina_code(spec: SymbolSpec) -> tuple[str, str]:
    """Return ``(sina_code, family)`` for a symbol spec."""
    if spec.is_commodity:
        code = _COMMODITY_CODES.get(spec.symbol.upper())
        if code is None:
            raise ProviderError(
                "sina",
                f"不支持的期货参考合约 {spec.symbol}；可用：{', '.join(sorted(_COMMODITY_CODES))}",
                kind="unsupported",
                symbol=spec.symbol,
            )
        return code, "hf"

    if spec.symbol.startswith("^"):
        if spec.symbol in _US_INDEX_CODES:
            return _US_INDEX_CODES[spec.symbol], "us"
        if spec.symbol in _HK_INDEX_CODES:
            return _HK_INDEX_CODES[spec.symbol], "hk"
        raise ProviderError(
            "sina",
            f"暂不支持的指数代码 {spec.symbol}；已支持：{', '.join(sorted(set(_US_INDEX_CODES) | set(_HK_INDEX_CODES)))}",
            kind="unsupported",
            symbol=spec.symbol,
        )

    if spec.market.value == "CN":
        code, exchange = spec.akshare_code, spec.akshare_exchange
        if not code or not exchange:
            raise ProviderError("sina", f"无法解析 A股代码 {spec.symbol}", kind="unsupported", symbol=spec.symbol)
        return f"{exchange}{code}", "cn"

    if spec.market.value == "HK":
        code = spec.akshare_code
        if not code:
            raise ProviderError("sina", f"无法解析港股代码 {spec.symbol}", kind="unsupported", symbol=spec.symbol)
        return f"hk{code.zfill(5)}", "hk"

    # Everything else is treated as a United States ticker.
    ticker = spec.yahoo_symbol.lower().replace("-", ".")
    return f"gb_{ticker}", "us"


def _combine(date_text: str, time_text: str = "") -> datetime | None:
    """Combine Beijing-time date/time strings into a UTC-aware datetime.

    Accepts both split forms (``"2026-10-30"`` + ``"15:00:00"``) and a single
    ``"YYYY-MM-DD HH:MM:SS"`` field, which is what the US feed returns.
    """
    date_text = (date_text or "").strip().replace("/", "-")
    time_text = (time_text or "").strip()
    if not date_text:
        return None
    if not time_text and " " in date_text:
        date_text, time_text = date_text.split(" ", 1)
    if len(time_text) == 5:
        time_text += ":00"
    if not time_text:
        time_text = "00:00:00"
    try:
        local = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            local = datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            return None
    return local.replace(tzinfo=_TZ_SHANGHAI).astimezone(timezone.utc)


def _parse_cn(fields: list[str]) -> dict[str, Any]:
    return {
        "name": fields[0],
        "open": to_float(fields[1]),
        "prev_close": to_float(fields[2]) or None,
        "price": to_float(fields[3]),
        "high": to_float(fields[4]),
        "low": to_float(fields[5]),
        "timestamp": _combine(fields[30], fields[31]),
    }


def _parse_hk(fields: list[str]) -> dict[str, Any]:
    return {
        "name": fields[1] or fields[0],
        "open": to_float(fields[2]),
        "prev_close": to_float(fields[3]) or None,
        "high": to_float(fields[4]),
        "low": to_float(fields[5]),
        "price": to_float(fields[6]),
        "timestamp": _combine(fields[17], fields[18]),
    }


def _parse_us(fields: list[str]) -> dict[str, Any]:
    price = to_float(fields[1])
    change = to_float(fields[4])
    prev_close = None
    if price is not None and change is not None:
        # The US feed does not publish a previous close: derive it, then round
        # away float noise (348.91 - 5.08 -> 343.83000000000004).
        prev_close = round(price - change, 6)
    return {
        "name": fields[0],
        "open": to_float(fields[5]),
        "prev_close": prev_close,
        "high": to_float(fields[6]),
        "low": to_float(fields[7]),
        "price": price,
        "timestamp": _combine(fields[3]) if len(fields) > 3 and fields[3] else None,
    }


def _parse_hf(fields: list[str]) -> dict[str, Any]:
    return {
        "name": fields[13] if len(fields) > 13 else "",
        "open": to_float(fields[8]),
        "prev_close": to_float(fields[7]) or None,
        "high": to_float(fields[4]),
        "low": to_float(fields[5]),
        "price": to_float(fields[0]),
        "timestamp": _combine(fields[12], fields[6]),
    }


_PARSERS = {"cn": _parse_cn, "hk": _parse_hk, "us": _parse_us, "hf": _parse_hf}


class SinaProvider(MarketDataProvider):
    """Quote-only provider backed by ``hq.sinajs.cn``."""

    name = "sina"

    def supports_history(self, spec: SymbolSpec) -> bool:
        return False

    # -- HTTP ------------------------------------------------------------
    def _fetch_raw(self, codes: list[str]) -> dict[str, list[str]]:
        url = SINA_URL.format(codes=",".join(codes))
        try:
            with httpx.Client(
                timeout=self.settings.request_timeout,
                headers=_HEADERS,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                body = response.content.decode("gbk", errors="replace")
        except httpx.HTTPError as exc:
            raise ProviderError(
                "sina",
                f"行情接口请求失败（{exc.__class__.__name__}）：{exc}",
                kind="network",
            ) from exc

        parsed: dict[str, list[str]] = {}
        for line in body.splitlines():
            match = _LINE_RE.match(line.strip())
            if not match:
                continue
            code, payload = match.group(1), match.group(2)
            if not payload.strip():
                continue
            parsed[code] = payload.split(",")
        return parsed

    # -- quote -----------------------------------------------------------
    def get_quote(self, spec: SymbolSpec) -> Quote:
        code, family = sina_code(spec)
        payload = call_with_timeout(
            lambda: self._fetch_raw([code]), self.settings.request_timeout, self.name, symbol=spec.symbol
        )
        fields = payload.get(code)
        if not fields:
            raise ProviderError(
                "sina",
                f"行情接口未返回 {code} 的数据（代码可能不存在或已停牌）。",
                kind="empty",
                symbol=spec.symbol,
            )

        try:
            data = _PARSERS[family](fields)
        except (IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                "sina",
                f"行情字段解析失败：{exc}（原始字段 {len(fields)} 个）",
                kind="parse",
                symbol=spec.symbol,
            ) from exc

        retrieved = utc_now()
        timestamp = data["timestamp"]
        price = data["price"]
        prev_close = data["prev_close"]

        status = DataStatus.DELAYED
        error = None
        if price is None or price == 0:
            status = DataStatus.UNAVAILABLE
            error = "数据源返回的价格为空或为 0（可能停牌、代码错误或该时段无成交）。"
            price = None
        elif timestamp is None:
            status = DataStatus.UNKNOWN
        else:
            age = (retrieved - timestamp).total_seconds()
            if age > self.settings.stale_after:
                status = DataStatus.STALE

        name = spec.name_hint or data.get("name") or None
        quote = Quote(
            symbol=spec.symbol,
            name=name,
            market=spec.market,
            asset_type=spec.asset_type,
            currency=spec.currency,
            price=price,
            previous_close=prev_close,
            timestamp=iso(timestamp),
            retrieved_at=iso(retrieved) or "",
            source=self.name,
            data_status=status,
            error=error,
            status_note=_FAMILY_NOTE.get(family),
        )
        return quote.with_derived()

    def get_history(self, spec: SymbolSpec, period: str, interval: str) -> HistoricalData:
        raise ProviderError("sina", "该数据源只提供行情快照，不提供历史行情。", kind="unsupported")
