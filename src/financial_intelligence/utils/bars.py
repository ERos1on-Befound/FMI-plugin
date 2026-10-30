"""Bar normalisation, resampling and window helpers.

Providers return wildly different frames (Chinese or English column names, epoch
milliseconds or ISO dates, daily-only history).  Everything is normalised here so
the service layer only ever sees ``{date, open, high, low, close, volume}``
dictionaries in ascending date order.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .validators import to_float

#: Canonical field -> accepted column names (lower-cased, stripped).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "日期", "时间", "datetime", "day", "trade_date"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价", "最新价", "adj close", "adj_close"),
    "volume": ("volume", "成交量", "vol"),
}

#: ``period`` -> number of calendar days to look back.
PERIOD_DAYS: dict[str, int] = {
    "1mo": 31,
    "3mo": 93,
    "6mo": 183,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
}


def _normalise_date(value: Any) -> str | None:
    """Convert provider date values (ISO string, datetime, epoch ms) to ``YYYY-MM-DD``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number <= 0:
            return None
        seconds = number / 1000.0 if number > 1e11 else number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] in {"-", "/"}:
        return text[:10].replace("/", "-")
    if text.isdigit():
        return _normalise_date(float(text))
    return None


def frame_to_bars(frame: Any) -> list[dict[str, Any]]:
    """Convert a pandas DataFrame (or list of dicts) into normalised bar dicts."""
    if frame is None:
        return []
    records: list[dict[str, Any]]
    if isinstance(frame, list):
        records = [dict(row) for row in frame]
    elif hasattr(frame, "to_dict"):
        try:
            records = frame.reset_index().to_dict(orient="records")
        except Exception:  # noqa: BLE001 - fall back to plain dict conversion
            records = frame.to_dict(orient="records")
        # The index often carries the date (yfinance); reset_index exposes it as
        # "index" / "Date" / "date".
    else:
        raise TypeError(f"不支持的行情数据结构：{type(frame)!r}")

    bars: list[dict[str, Any]] = []
    for record in records:
        lowered = {str(k).strip().lower(): v for k, v in record.items()}
        row: dict[str, Any] = {}
        for field, aliases in _COLUMN_ALIASES.items():
            value = None
            for alias in aliases:
                if alias in lowered and lowered[alias] not in (None, ""):
                    value = lowered[alias]
                    break
            if value is None and field == "date":
                for key in ("index", "datetime64", "unnamed: 0"):
                    if key in lowered and lowered[key] not in (None, ""):
                        value = lowered[key]
                        break
            row[field] = value

        bar_date = _normalise_date(row["date"])
        if not bar_date:
            continue
        bar = {
            "date": bar_date,
            "open": to_float(row["open"]),
            "high": to_float(row["high"]),
            "low": to_float(row["low"]),
            "close": to_float(row["close"]),
            "volume": to_float(row["volume"]),
        }
        if bar["close"] is None and bar["open"] is None:
            continue
        bars.append(bar)

    return sort_and_dedupe(bars)


def sort_and_dedupe(bars: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort ascending by date and drop duplicate dates (last one wins)."""
    seen: dict[str, dict[str, Any]] = {}
    for bar in bars:
        if bar.get("date"):
            seen[bar["date"]] = bar
    return [seen[key] for key in sorted(seen)]


def resample_bars(bars: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    """Aggregate daily bars into weekly/monthly bars (no pandas dependency)."""
    if interval == "1d" or not bars:
        return list(bars)

    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for bar in bars:
        try:
            parsed = datetime.strptime(bar["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if interval == "1wk":
            iso_year, iso_week, _ = parsed.isocalendar()
            key = (iso_year, iso_week)
        else:  # 1mo
            key = (parsed.year, parsed.month)
        groups.setdefault(key, []).append(bar)

    result: list[dict[str, Any]] = []
    for key in sorted(groups):
        chunk = groups[key]
        first, last = chunk[0], chunk[-1]
        volumes = [b["volume"] for b in chunk if b.get("volume") is not None]
        result.append(
            {
                "date": last["date"],
                "open": first.get("open"),
                "high": max((b["high"] for b in chunk if b.get("high") is not None), default=None),
                "low": min((b["low"] for b in chunk if b.get("low") is not None), default=None),
                "close": last.get("close"),
                "volume": sum(volumes) if volumes else None,
            }
        )
    return result


def window_bars(
    bars: list[dict[str, Any]],
    period: str,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Keep bars inside the requested ``period`` window (e.g. ``1y``)."""
    days = PERIOD_DAYS.get(period)
    if days is None or not bars:
        return list(bars)
    reference = today or date.today()
    cutoff = (reference - timedelta(days=days)).isoformat()
    return [bar for bar in bars if bar["date"] >= cutoff]


def cap_bars(
    bars: list[dict[str, Any]], limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Cap a series to the most recent ``limit`` bars; return ``(bars, truncated)``."""
    if limit <= 0 or len(bars) <= limit:
        return list(bars), False
    return bars[-limit:], True
