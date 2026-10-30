"""Value validation and normalisation helpers.

These helpers are deliberately strict: invalid input raises a typed exception
with an actionable message instead of being silently coerced into something the
caller did not ask for.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

from ..config import SUPPORTED_INTERVALS, SUPPORTED_PERIODS
from ..schemas import AssetType, Market

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InputError(ValueError):
    """Raised when caller-supplied arguments are invalid."""


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

#: Permissive check on the *raw* symbol; semantic checks live in symbol_mapper.
_RAW_SYMBOL_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff^=.\-: _]{1,32}$")


def validate_raw_symbol(raw: str) -> str:
    """Validate a raw user-supplied symbol string and return it stripped."""
    if raw is None:
        raise InputError("symbol 不能为空。示例：AAPL、0700.HK、600519.SS、^GSPC、GC=F。")
    if not isinstance(raw, str):
        raise InputError(f"symbol 必须是字符串，收到 {type(raw).__name__}。")
    text = raw.strip()
    if not text:
        raise InputError("symbol 不能为空字符串。示例：AAPL、0700.HK、600519.SS、^GSPC、GC=F。")
    if len(text) > 32:
        raise InputError(f"symbol 过长（{len(text)} 字符），请提供代码本身而不是描述。")
    if not _RAW_SYMBOL_RE.match(text):
        raise InputError(
            f"symbol {text!r} 含有非法字符；支持字母、数字、中文别名以及 ^ = . - 等符号。"
        )
    return text


# ---------------------------------------------------------------------------
# Enums supplied as free text
# ---------------------------------------------------------------------------


def parse_market(value: Any) -> Market | None:
    """Parse an optional market filter (case-insensitive)."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    try:
        return Market(text)
    except ValueError as exc:
        allowed = "/".join(m.value for m in Market if m is not Market.UNKNOWN)
        raise InputError(f"market={value!r} 不受支持；可用值：{allowed}。") from exc


def parse_asset_type(value: Any) -> AssetType | None:
    """Parse an optional asset-type filter (case-insensitive)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return AssetType(text)
    except ValueError as exc:
        allowed = "/".join(a.value for a in AssetType if a is not AssetType.UNKNOWN)
        raise InputError(f"asset_type={value!r} 不受支持；可用值：{allowed}。") from exc


# ---------------------------------------------------------------------------
# History parameters
# ---------------------------------------------------------------------------

#: Period/interval combinations we refuse because they cannot produce a series.
UNSUPPORTED_COMBOS: dict[tuple[str, str], str] = {
    ("1mo", "1mo"): "period=1mo 与 interval=1mo 只能得到 1 根未完结的月线，不构成序列；"
    "请改用 interval=1d 或 1wk，或把 period 放宽到 6mo 以上。",
}


def parse_period(period: Any) -> str:
    """Validate a history window such as ``1y``."""
    text = str(period or "").strip().lower()
    if not text:
        raise InputError(f"period 不能为空；可用值：{', '.join(SUPPORTED_PERIODS)}。")
    if text not in SUPPORTED_PERIODS:
        raise InputError(
            f"period={period!r} 不受支持；可用值：{', '.join(SUPPORTED_PERIODS)}。"
        )
    return text


def parse_interval(interval: Any) -> str:
    """Validate a bar interval such as ``1d``."""
    text = str(interval or "").strip().lower()
    if not text:
        raise InputError(f"interval 不能为空；可用值：{', '.join(SUPPORTED_INTERVALS)}。")
    if text not in SUPPORTED_INTERVALS:
        raise InputError(
            f"interval={interval!r} 不受支持；可用值：{', '.join(SUPPORTED_INTERVALS)}。"
        )
    return text


def validate_period_interval(period: str, interval: str) -> None:
    """Raise :class:`InputError` for combinations that cannot work."""
    message = UNSUPPORTED_COMBOS.get((period, interval))
    if message:
        raise InputError(message)


# ---------------------------------------------------------------------------
# Numeric / temporal coercion
# ---------------------------------------------------------------------------


def to_float(value: Any) -> float | None:
    """Best-effort conversion to a finite float; ``None`` on anything else.

    Missing market data must stay missing — this never invents ``0``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def to_iso_datetime(value: Any) -> str | None:
    """Convert a provider timestamp (datetime / date / epoch seconds) to ISO 8601 UTC."""
    if value is None:
        return None
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isnan(number) or math.isinf(number) or number <= 0:
            return None
        # Heuristic: values beyond 1e11 are milliseconds.
        if number > 1e11:
            number /= 1000.0
        try:
            dt = datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def to_iso_date(value: Any) -> str | None:
    """Convert a provider date to ``YYYY-MM-DD`` (used for daily bars)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # AKShare returns "2024-01-02"; keep the date part only.
    return text[:10] if len(text) >= 10 and text[4] in {"-", "/"} else text


def dedupe_sorted(rows: Iterable[Any], key: str = "date") -> list[Any]:
    """Drop duplicate bars and sort ascending by ``key``."""
    seen: dict[str, Any] = {}
    for row in rows:
        value = getattr(row, key, None) or (row.get(key) if isinstance(row, dict) else None)
        if value is None:
            continue
        seen[str(value)] = row
    return [seen[k] for k in sorted(seen)]
