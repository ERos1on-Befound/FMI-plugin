"""Map user-supplied asset names/codes onto canonical provider symbols.

The mapper is the single place that knows how a Chinese alias (``上证指数``), a
bare A-share code (``600519``), a Hong Kong code (``700``), a Yahoo style ticker
(``^GSPC``) or a futures reference (``GC=F``) becomes:

* a canonical display symbol  (``000001.SS``, ``0700.HK``, ``AAPL``, ``GC=F``)
* a Yahoo Finance symbol      (``000001.SS``, ``0700.HK``, ``AAPL``, ``GC=F``)
* an AKShare code + exchange  (``000001`` + ``sh``)

Ambiguity is resolved explicitly and documented, never by guessing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import AssetType, Market
from .validators import InputError, validate_raw_symbol

# ---------------------------------------------------------------------------
# Canonical spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolSpec:
    """Everything the providers need to know about one requested asset."""

    original: str
    symbol: str
    market: Market
    asset_type: AssetType
    currency: str
    yahoo_symbol: str
    akshare_code: str | None = None
    akshare_exchange: str | None = None
    name_hint: str | None = None

    @property
    def is_commodity(self) -> bool:
        return self.asset_type is AssetType.COMMODITY

    @property
    def is_index(self) -> bool:
        return self.asset_type is AssetType.INDEX


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

#: Human alias -> (canonical symbol, display name). Keys are matched lower-cased.
_ALIASES: dict[str, tuple[str, str]] = {
    # --- mainland China: indices ---
    "上证指数": ("000001.SS", "上证指数"),
    "上证综指": ("000001.SS", "上证指数"),
    "上证": ("000001.SS", "上证指数"),
    "shanghai composite": ("000001.SS", "上证指数"),
    "深证成指": ("399001.SZ", "深证成指"),
    "深证": ("399001.SZ", "深证成指"),
    "shenzhen component": ("399001.SZ", "深证成指"),
    "沪深300": ("000300.SS", "沪深300"),
    "沪深 300": ("000300.SS", "沪深300"),
    "csi300": ("000300.SS", "沪深300"),
    "csi 300": ("000300.SS", "沪深300"),
    "中证500": ("000905.SS", "中证500"),
    "创业板指": ("399006.SZ", "创业板指"),
    # --- mainland China: stocks ---
    "贵州茅台": ("600519.SS", "贵州茅台"),
    "茅台": ("600519.SS", "贵州茅台"),
    "平安银行": ("000001.SZ", "平安银行"),
    # --- Hong Kong ---
    "恒生指数": ("^HSI", "恒生指数"),
    "恒指": ("^HSI", "恒生指数"),
    "hsi": ("^HSI", "恒生指数"),
    "hang seng": ("^HSI", "恒生指数"),
    "恒生科技": ("^HSTECH", "恒生科技指数"),
    "恒生科技指数": ("^HSTECH", "恒生科技指数"),
    "hstech": ("^HSTECH", "恒生科技指数"),
    "腾讯": ("0700.HK", "腾讯控股"),
    "腾讯控股": ("0700.HK", "腾讯控股"),
    "阿里巴巴": ("9988.HK", "阿里巴巴-W"),
    "美团": ("3690.HK", "美团-W"),
    # --- United States: indices ---
    "标普500": ("^GSPC", "标普500"),
    "标普": ("^GSPC", "标普500"),
    "s&p500": ("^GSPC", "标普500"),
    "s&p 500": ("^GSPC", "标普500"),
    "纳斯达克": ("^IXIC", "纳斯达克综合指数"),
    "纳指": ("^IXIC", "纳斯达克综合指数"),
    "nasdaq": ("^IXIC", "纳斯达克综合指数"),
    "道琼斯": ("^DJI", "道琼斯工业平均指数"),
    "道指": ("^DJI", "道琼斯工业平均指数"),
    "dow jones": ("^DJI", "道琼斯工业平均指数"),
    # --- United States: stocks ---
    "苹果": ("AAPL", "Apple Inc."),
    "apple": ("AAPL", "Apple Inc."),
    "英伟达": ("NVDA", "NVIDIA Corporation"),
    "nvidia": ("NVDA", "NVIDIA Corporation"),
    "微软": ("MSFT", "Microsoft Corporation"),
    "microsoft": ("MSFT", "Microsoft Corporation"),
    "特斯拉": ("TSLA", "Tesla, Inc."),
    "tesla": ("TSLA", "Tesla, Inc."),
    "谷歌": ("GOOGL", "Alphabet Inc."),
    "亚马逊": ("AMZN", "Amazon.com, Inc."),
    # --- commodities (futures reference contracts, NOT spot) ---
    "黄金": ("GC=F", "COMEX 黄金期货（参考）"),
    "gold": ("GC=F", "COMEX 黄金期货（参考）"),
    "comex黄金": ("GC=F", "COMEX 黄金期货（参考）"),
    "白银": ("SI=F", "COMEX 白银期货（参考）"),
    "silver": ("SI=F", "COMEX 白银期货（参考）"),
    "原油": ("CL=F", "WTI 原油期货（参考）"),
    "wti": ("CL=F", "WTI 原油期货（参考）"),
    "wti原油": ("CL=F", "WTI 原油期货（参考）"),
    "布伦特": ("BZ=F", "布伦特原油期货（参考）"),
    "布伦特原油": ("BZ=F", "布伦特原油期货（参考）"),
    "brent": ("BZ=F", "布伦特原油期货（参考）"),
    "铜": ("HG=F", "COMEX 铜期货（参考）"),
    "天然气": ("NG=F", "NYMEX 天然气期货（参考）"),
}

#: Bare 6-digit A-share codes that are Shanghai/Shenzhen indices, not stocks.
_CN_INDEX_CODES = frozenset(
    {
        "000300",  # 沪深300
        "000905",  # 中证500
        "000016",  # 上证50
        "000852",  # 中证1000
        "000688",  # 科创50
        "000010",  # 上证180
        "000009",  # 上证380
        "000015",  # 上证红利
        "000903",  # 中证100
        "000906",  # 中证800
        "000985",  # 中证全指
        "000001",  # 上证指数 — only reachable via explicit .SS / alias (see note)
    }
)

#: Codes that are indices only when the exchange suffix is ``.SS``.
_SS_INDEX_CODES = _CN_INDEX_CODES

_COMMODITY_TICKERS = frozenset({"GC=F", "CL=F", "BZ=F", "SI=F", "HG=F", "NG=F", "PL=F", "PA=F", "ZC=F"})

_CURRENCY_BY_MARKET = {
    Market.CN: "CNY",
    Market.HK: "HKD",
    Market.US: "USD",
    Market.COMMODITY: "USD",
    Market.UNKNOWN: None,
}

_CN_SUFFIX_TO_EXCHANGE = {".SS": "sh", ".SH": "sh", ".SZ": "sz", ".BJ": "bj"}
_EXCHANGE_TO_CN_SUFFIX = {"sh": ".SS", "sz": ".SZ", "bj": ".BJ"}

_DIGITS_RE = re.compile(r"^\d+$")
_US_RE = re.compile(r"^[A-Z][A-Z0-9\-]{0,9}(\.[A-Z])?$")
_PREFIXED_CN_RE = re.compile(r"^(SH|SZ|BJ)(\d{6})$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SYMBOL_HELP = (
    "支持的写法：\n"
    "  • 美股：AAPL、NVDA、BRK.B\n"
    "  • A股：600519 / 600519.SS / 000001.SZ / sh600519\n"
    "  • 港股：0700.HK / 700 / 9988.HK\n"
    "  • 指数：^GSPC、^IXIC、^HSI、000001.SS、399001.SZ\n"
    "  • 大宗（期货参考）：GC=F、CL=F、BZ=F\n"
    "  • 中文别名：上证指数、沪深300、恒生科技、标普500、纳指、道指、黄金、WTI、布伦特原油"
)


def map_symbol(
    raw: str,
    *,
    market: Market | None = None,
    asset_type: AssetType | None = None,
) -> SymbolSpec:
    """Resolve ``raw`` into a :class:`SymbolSpec`.

    ``market`` / ``asset_type`` are optional *hints* from the caller; a hint that
    contradicts the symbol itself is treated as a caller error, because silently
    reinterpreting it would hide a mistake.
    """
    text = validate_raw_symbol(raw)
    alias = _ALIASES.get(text.lower()) or _ALIASES.get(text)
    name_hint: str | None = None
    if alias is not None:
        canonical, name_hint = alias
        symbol, detected_market, detected_type = _classify(canonical, via_alias=True)
    else:
        symbol, detected_market, detected_type = _classify(text, via_alias=False)

    resolved_market = market or detected_market
    resolved_type = asset_type or detected_type

    if market is not None and detected_market is not Market.UNKNOWN and market is not detected_market:
        # Commodity/index tickers are unambiguous; only reject clear mismatches.
        if not (detected_type is AssetType.INDEX and market in {Market.US, Market.HK, Market.CN}):
            raise InputError(
                f"symbol {text!r} 属于 {detected_market.value} 市场，与传入的 market={market.value} 冲突。"
            )
    if asset_type is not None and detected_type is not AssetType.UNKNOWN and asset_type is not detected_type:
        raise InputError(
            f"symbol {text!r} 的资产类型是 {detected_type.value}，与传入的 asset_type={asset_type.value} 冲突。"
        )

    yahoo_symbol = _to_yahoo_symbol(symbol)
    akshare_code, akshare_exchange = _to_akshare(symbol, resolved_market)

    return SymbolSpec(
        original=text,
        symbol=symbol,
        market=resolved_market,
        asset_type=resolved_type,
        currency=_CURRENCY_BY_MARKET.get(resolved_market) or "USD",
        yahoo_symbol=yahoo_symbol,
        akshare_code=akshare_code,
        akshare_exchange=akshare_exchange,
        name_hint=name_hint,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(text: str, *, via_alias: bool) -> tuple[str, Market, AssetType]:
    """Return ``(canonical_symbol, market, asset_type)`` for a non-alias input."""
    raw = text.strip()
    upper = raw.upper()

    # Commodity futures reference contracts -------------------------------
    if upper.endswith("=F") or upper in _COMMODITY_TICKERS:
        symbol = upper if upper.endswith("=F") else f"{upper}=F"
        return symbol, Market.COMMODITY, AssetType.COMMODITY

    # Indices ------------------------------------------------------------
    if upper.startswith("^"):
        return upper, Market.US, AssetType.INDEX

    # sh600519 / SZ000001 shorthand --------------------------------------
    match = _PREFIXED_CN_RE.match(upper)
    if match:
        prefix, digits = match.group(1).lower(), match.group(2)
        suffix = _EXCHANGE_TO_CN_SUFFIX[prefix]
        return f"{digits}{suffix}", Market.CN, _cn_type(digits, suffix)

    # Explicit exchange suffix -------------------------------------------
    for suffix, exchange in _CN_SUFFIX_TO_EXCHANGE.items():
        if upper.endswith(suffix):
            digits = upper[: -len(suffix)]
            if not _DIGITS_RE.match(digits) or len(digits) != 6:
                raise InputError(
                    f"{raw!r} 的 A股代码部分必须是 6 位数字，例如 600519.SS。"
                )
            canonical = f"{digits}{_EXCHANGE_TO_CN_SUFFIX[exchange]}"
            return canonical, Market.CN, _cn_type(digits, canonical[-3:].upper())

    if upper.endswith(".US"):
        return _classify(upper[:-3], via_alias=via_alias)

    if upper.endswith((".HK", ".HKG")):
        digits = upper.split(".")[0]
        if not _DIGITS_RE.match(digits) or not 1 <= len(digits) <= 5:
            raise InputError(f"{raw!r} 的港股代码部分必须是 1-5 位数字，例如 0700.HK。")
        code = _normalise_hk_code(digits)
        return f"{code}.HK", Market.HK, AssetType.STOCK

    # Digits only --------------------------------------------------------
    if _DIGITS_RE.match(upper):
        digits = upper
        if len(digits) == 6:
            if digits.startswith(("399", "899")):
                return f"{digits}.SZ", Market.CN, AssetType.INDEX
            if digits in _CN_INDEX_CODES and digits != "000001":
                return f"{digits}.SS", Market.CN, AssetType.INDEX
            if digits.startswith(("6", "9")):
                return f"{digits}.SS", Market.CN, AssetType.STOCK
            if digits.startswith(("4", "8")):
                return f"{digits}.BJ", Market.CN, AssetType.STOCK
            return f"{digits}.SZ", Market.CN, AssetType.STOCK
        if 1 <= len(digits) <= 5:
            code = _normalise_hk_code(digits)
            return f"{code}.HK", Market.HK, AssetType.STOCK
        raise InputError(f"无法识别代码 {raw!r}。\n{SYMBOL_HELP}")

    # Everything else: assume United States ticker -----------------------
    if _US_RE.match(upper):
        display = upper
        if display.count(".") == 1 and len(display.split(".")[1]) == 1:
            display = display  # class shares such as BRK.B stay as-is for display
        return display, Market.US, AssetType.STOCK

    if via_alias:
        raise InputError(f"内部别名表指向了无法识别的代码 {raw!r}。")

    raise InputError(f"无法识别代码 {raw!r}。\n{SYMBOL_HELP}")


def _cn_type(digits: str, suffix: str) -> AssetType:
    """Decide whether a 6-digit A-share code is an index or a stock."""
    upper_suffix = suffix.upper()
    if digits.startswith("399") or digits.startswith("899"):
        return AssetType.INDEX
    if upper_suffix in {".SS", ".SH"}:
        return AssetType.INDEX if digits in _SS_INDEX_CODES else AssetType.STOCK
    return AssetType.STOCK


def _normalise_hk_code(digits: str) -> str:
    """``700`` -> ``0700``, ``00700`` -> ``0700``, ``9988`` -> ``9988``."""
    stripped = digits.lstrip("0") or "0"
    return stripped.zfill(4)


# ---------------------------------------------------------------------------
# Provider symbol conversions
# ---------------------------------------------------------------------------


def _to_yahoo_symbol(symbol: str) -> str:
    """Convert a canonical symbol to the Yahoo Finance spelling."""
    if symbol.endswith(".SH"):
        return symbol[:-3] + ".SS"
    if "." in symbol and not symbol.startswith("^") and not symbol.endswith("=F"):
        head, _, tail = symbol.rpartition(".")
        # BRK.B -> BRK-B (Yahoo uses a dash for share classes).
        if len(tail) == 1 and head:
            return f"{head}-{tail}"
    return symbol


def _to_akshare(symbol: str, market: Market) -> tuple[str | None, str | None]:
    """Return ``(code, exchange)`` usable with AKShare; ``(None, None)`` if N/A."""
    if market is Market.CN:
        head, _, suffix = symbol.rpartition(".")
        exchange = _CN_SUFFIX_TO_EXCHANGE.get(f".{suffix}")
        if exchange and _DIGITS_RE.match(head):
            return head, exchange
        return None, None
    if market is Market.HK:
        head = symbol.split(".")[0]
        return (head if _DIGITS_RE.match(head) else None), "hk"
    return None, None
