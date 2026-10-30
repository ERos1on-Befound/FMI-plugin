"""Technical indicators computed with pandas/numpy.

Conventions used by this project (documented in ``references/technical-guide.md``):

* **EMA** – recursive ``EMA_t = α·x_t + (1-α)·EMA_{t-1}`` with ``α = 2/(n+1)``,
  seeded with the first close of the series (equivalent to ``pandas.ewm(adjust=False)``).
* **RSI(14)** – Wilder smoothing: the first average gain/loss is the simple mean
  of the first 14 changes, afterwards ``avg = (avg·13 + today) / 14``.
* **MACD(12,26,9)** – ``DIF = EMA12 - EMA26``, ``DEA = EMA9(DIF)``,
  ``HIST = DIF - DEA`` (the histogram is the difference, not twice the difference).
* **Bollinger(20,2)** – middle = SMA20, σ = **population** standard deviation
  (``ddof=0``) of the last 20 closes, bands = middle ± 2σ.
* **ATR(14)** – ``TR = max(H-L, |H-C_prev|, |L-C_prev|)`` with Wilder smoothing,
  seeded by the simple mean of the first 14 true ranges.

Every indicator only *describes the observed history*; none of them is a signal
to buy or sell, and none of the explanations may predict future prices.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ..models.analysis_result import IndicatorSeries
from ..schemas import HistoricalBar

INDICATOR_FAMILIES: dict[str, str] = {
    "sma": "简单移动平均线",
    "ema": "指数移动平均线",
    "rsi": "相对强弱指标",
    "macd": "MACD",
    "bollinger": "布林带",
    "atr": "平均真实波幅",
}

SMA_WINDOWS = (5, 10, 20, 60)
EMA_WINDOWS = (12, 26)
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BOLLINGER_PERIOD, BOLLINGER_STD = 20, 2.0
ATR_PERIOD = 14

#: Minimum number of bars needed before a family can be computed at all.
MIN_BARS = {"sma": 5, "ema": 12, "rsi": RSI_PERIOD + 1, "macd": MACD_SLOW + MACD_SIGNAL, "bollinger": BOLLINGER_PERIOD, "atr": ATR_PERIOD + 1}

DEFAULT_INDICATORS: tuple[str, ...] = ("sma", "ema", "rsi", "macd", "bollinger", "atr")

MACD_PARAMS: dict[str, float] = {"fast": float(MACD_FAST), "slow": float(MACD_SLOW), "signal": float(MACD_SIGNAL)}
BOLLINGER_PARAMS: dict[str, float] = {"period": float(BOLLINGER_PERIOD), "std": BOLLINGER_STD}

#: Series emitted per family, used for both results and ``insufficient_data`` placeholders.
FAMILY_SERIES: dict[str, tuple[tuple[str, str, dict[str, float], str], ...]] = {
    "sma": tuple((f"sma_{window}", "trend", {"window": float(window)}, "price") for window in SMA_WINDOWS),
    "ema": tuple((f"ema_{window}", "trend", {"window": float(window)}, "price") for window in EMA_WINDOWS),
    "rsi": ((f"rsi_{RSI_PERIOD}", "momentum", {"period": float(RSI_PERIOD)}, "index"),),
    "macd": (
        ("macd", "momentum", MACD_PARAMS, "price"),
        ("macd_signal", "momentum", MACD_PARAMS, "price"),
        ("macd_histogram", "momentum", MACD_PARAMS, "price"),
    ),
    "bollinger": tuple(
        (name, "volatility", BOLLINGER_PARAMS, unit)
        for name, unit in (
            ("bollinger_middle", "price"),
            ("bollinger_upper", "price"),
            ("bollinger_lower", "price"),
            ("bollinger_bandwidth", "%"),
            ("bollinger_percent_b", "%"),
        )
    ),
    "atr": ((f"atr_{ATR_PERIOD}", "volatility", {"period": float(ATR_PERIOD)}, "price"),),
}


class TechnicalInputError(ValueError):
    """Raised when the OHLCV input cannot be used at all."""


# ---------------------------------------------------------------------------
# input handling
# ---------------------------------------------------------------------------
def bars_to_frame(bars: Sequence[HistoricalBar]) -> pd.DataFrame:
    """Convert historical bars into a clean, ascending OHLCV frame."""
    if not bars:
        raise TechnicalInputError("没有可用的历史行情数据。")
    frame = pd.DataFrame(
        [
            {
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["close"])
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    if frame.empty:
        raise TechnicalInputError("历史行情数据缺少可用的收盘价。")
    # Rows where high/low are missing still allow close based indicators.
    frame["high"] = frame["high"].fillna(frame["close"])
    frame["low"] = frame["low"].fillna(frame["close"])
    frame["open"] = frame["open"].fillna(frame["close"])
    return frame.reset_index(drop=True)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def sma(values: pd.Series, window: int) -> pd.Series:
    return values.rolling(window=window, min_periods=window).mean()


def ema(values: pd.Series, window: int) -> pd.Series:
    """Recursive EMA seeded with the first observation (``adjust=False``)."""
    alpha = 2.0 / (window + 1.0)
    return values.ewm(alpha=alpha, adjust=False, min_periods=1).mean()


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder smoothing: seed with the SMA of the first ``period`` values."""
    result = np.full(values.shape, np.nan, dtype=float)
    if len(values) < period:
        return result
    first = float(np.mean(values[:period]))
    result[period - 1] = first
    previous = first
    for index in range(period, len(values)):
        previous = (previous * (period - 1) + float(values[index])) / period
        result[index] = previous
    return result


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    values = close.to_numpy(dtype=float)
    result = np.full(values.shape, np.nan, dtype=float)
    if len(values) <= period:
        return pd.Series(result, index=close.index)
    changes = np.diff(values)
    gains = np.clip(changes, 0.0, None)
    losses = np.clip(-changes, 0.0, None)
    avg_gain = _wilder(gains, period)
    avg_loss = _wilder(losses, period)
    for index in range(period, len(values)):
        gain = avg_gain[index - 1]
        loss = avg_loss[index - 1]
        if np.isnan(gain) or np.isnan(loss):
            continue
        if loss == 0:
            result[index] = 100.0 if gain > 0 else 50.0
        else:
            rs = gain / loss
            result[index] = 100.0 - 100.0 / (1.0 + rs)
    return pd.Series(result, index=close.index)


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = ema(close, MACD_FAST)
    slow = ema(close, MACD_SLOW)
    dif = fast - slow
    dea = ema(dif, MACD_SIGNAL)
    hist = dif - dea
    return dif, dea, hist


def bollinger(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = sma(close, BOLLINGER_PERIOD)
    std = close.rolling(window=BOLLINGER_PERIOD, min_periods=BOLLINGER_PERIOD).std(ddof=0)
    upper = middle + BOLLINGER_STD * std
    lower = middle - BOLLINGER_STD * std
    return middle, upper, lower


def atr(frame: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    true_range = np.full(len(frame), np.nan, dtype=float)
    for index in range(len(frame)):
        candidates = [high[index] - low[index]]
        if index > 0:
            candidates.append(abs(high[index] - close[index - 1]))
            candidates.append(abs(low[index] - close[index - 1]))
        true_range[index] = max(candidates)
    smoothed = _wilder(true_range[1:], period)
    values = np.full(len(frame), np.nan, dtype=float)
    values[1:] = smoothed
    return pd.Series(values, index=frame.index)


# ---------------------------------------------------------------------------
# series construction
# ---------------------------------------------------------------------------
def _history(series: pd.Series, dates: pd.Series, points: int) -> list[dict[str, float | str | None]]:
    cleaned = series.dropna()
    if cleaned.empty:
        return []
    tail = cleaned.tail(points)
    rows: list[dict[str, float | str | None]] = []
    for index, value in tail.items():
        stamp = dates.loc[index]
        rows.append(
            {
                "date": stamp.date().isoformat() if hasattr(stamp, "date") else str(stamp),
                "value": round(float(value), 6),
            }
        )
    return rows


def _series(
    *,
    name: str,
    category: str,
    params: dict[str, float],
    values: pd.Series,
    dates: pd.Series,
    unit: str = "price",
    explanation: str = "",
    history_points: int = 10,
    notes: Sequence[str] = (),
) -> IndicatorSeries:
    cleaned = values.dropna()
    if cleaned.empty:
        return IndicatorSeries(
            name=name,
            category=category,
            params=params,
            unit=unit,
            value=None,
            status="insufficient_data",
            explanation="历史数据长度不足，无法计算该指标。",
            notes=list(notes),
        )
    latest_index = cleaned.index[-1]
    value = float(cleaned.loc[latest_index])
    previous = float(cleaned.iloc[-2]) if len(cleaned) >= 2 else None
    change = value - previous if previous is not None else None
    change_pct = (change / previous * 100.0) if previous not in (None, 0) else None
    stamp = dates.loc[latest_index]
    return IndicatorSeries(
        name=name,
        category=category,
        params=params,
        unit=unit,
        value=round(value, 6),
        previous_value=round(previous, 6) if previous is not None else None,
        change=round(change, 6) if change is not None else None,
        change_pct=round(change_pct, 4) if change_pct is not None else None,
        date=stamp.date().isoformat() if hasattr(stamp, "date") else str(stamp),
        history=_history(values, dates, history_points),
        status="ok",
        explanation=explanation,
        notes=list(notes),
    )


def _price_frame(bars: Sequence[HistoricalBar]) -> pd.DataFrame:
    return bars_to_frame(bars)


def _insufficient(name: str, category: str, params: dict[str, float], need: int, have: int, unit: str = "price") -> IndicatorSeries:
    return IndicatorSeries(
        name=name,
        category=category,
        params=params,
        unit=unit,
        value=None,
        status="insufficient_data",
        explanation=f"需要至少 {need} 个交易日，当前只有 {have} 个。",
    )


def compute_indicators(
    bars: Sequence[HistoricalBar],
    indicators: Sequence[str] | None = None,
    *,
    history_points: int = 10,
) -> list[IndicatorSeries]:
    """Compute the requested indicator families from OHLCV bars."""
    frame = _price_frame(bars)
    dates = frame["date"]
    close = frame["close"]
    requested = _normalise_selection(indicators)
    result: list[IndicatorSeries] = []

    # Families whose minimum history is longer than the available window are
    # reported as ``insufficient_data`` instead of returning warm-up artefacts
    # (an EMA26 seeded with the first close of 12 bars is not a 26-day EMA).
    active: list[str] = []
    for family in requested:
        if len(frame) < MIN_BARS[family]:
            for name, category, params, unit in FAMILY_SERIES[family]:
                result.append(_insufficient(name, category, params, MIN_BARS[family], len(frame), unit))
        else:
            active.append(family)
    requested = tuple(active)

    if "sma" in requested:
        for window in SMA_WINDOWS:
            if len(close) < window:
                result.append(
                    IndicatorSeries(
                        name=f"sma_{window}",
                        category="trend",
                        params={"window": float(window)},
                        value=None,
                        status="insufficient_data",
                        explanation=f"需要至少 {window} 个交易日，当前只有 {len(close)} 个。",
                    )
                )
                continue
            values = sma(close, window)
            latest = float(values.iloc[-1])
            last_close = float(close.iloc[-1])
            relation = "高于" if last_close > latest else ("低于" if last_close < latest else "等于")
            result.append(
                _series(
                    name=f"sma_{window}",
                    category="trend",
                    params={"window": float(window)},
                    values=values,
                    dates=dates,
                    history_points=history_points,
                    explanation=(
                        f"最新收盘价 {last_close:,.4f} {relation} SMA{window} {latest:,.4f}。"
                        "均线只描述历史价格的平均水平，不代表未来方向。"
                    ),
                )
            )

    if "ema" in requested:
        for window in EMA_WINDOWS:
            if len(close) < window:
                result.append(
                    _insufficient(f"ema_{window}", "trend", {"window": float(window)}, window, len(close))
                )
                continue
            values = ema(close, window)
            result.append(
                _series(
                    name=f"ema_{window}",
                    category="trend",
                    params={"window": float(window), "alpha": round(2.0 / (window + 1.0), 6)},
                    values=values,
                    dates=dates,
                    history_points=history_points,
                    notes=("EMA 以首个收盘价为初始值递推（α=2/(n+1)，adjust=False）。",),
                )
            )

    if "rsi" in requested:
        values = rsi(close, RSI_PERIOD)
        latest = values.dropna()
        if latest.empty:
            result.append(
                IndicatorSeries(
                    name=f"rsi_{RSI_PERIOD}",
                    category="momentum",
                    params={"period": float(RSI_PERIOD)},
                    unit="index",
                    value=None,
                    status="insufficient_data",
                    explanation=f"RSI 需要至少 {RSI_PERIOD + 1} 个交易日。",
                    notes=("采用 Wilder 平滑。",),
                )
            )
        else:
            level = float(latest.iloc[-1])
            if level >= 70:
                zone = "相对高位区间（≥70，仅描述历史状态，不是卖出信号）"
            elif level <= 30:
                zone = "相对低位区间（≤30，仅描述历史状态，不是买入信号）"
            else:
                zone = "中间区间（30–70）"
            result.append(
                _series(
                    name=f"rsi_{RSI_PERIOD}",
                    category="momentum",
                    params={"period": float(RSI_PERIOD)},
                    values=values,
                    dates=dates,
                    unit="index",
                    history_points=history_points,
                    explanation=f"RSI{RSI_PERIOD} 为 {level:.2f}，处于{zone}。",
                    notes=(
                        "采用 Wilder 平滑（首值为前 14 个涨跌幅的简单平均）。",
                        "RSI 只衡量近期涨跌力量对比，不预测价格。",
                    ),
                )
            )

    if "macd" in requested:
        dif, dea, hist = macd(close)
        result.append(
            _series(
                name="macd",
                category="momentum",
                params={"fast": float(MACD_FAST), "slow": float(MACD_SLOW), "signal": float(MACD_SIGNAL)},
                values=dif,
                dates=dates,
                history_points=history_points,
                notes=("DIF = EMA12 - EMA26。",),
            )
        )
        result.append(
            _series(
                name="macd_signal",
                category="momentum",
                params={"fast": float(MACD_FAST), "slow": float(MACD_SLOW), "signal": float(MACD_SIGNAL)},
                values=dea,
                dates=dates,
                history_points=history_points,
                notes=("DEA = EMA9(DIF)。",),
            )
        )
        hist_series = _series(
            name="macd_histogram",
            category="momentum",
            params={"fast": float(MACD_FAST), "slow": float(MACD_SLOW), "signal": float(MACD_SIGNAL)},
            values=hist,
            dates=dates,
            history_points=history_points,
            notes=("柱体 = DIF - DEA（本项目定义，与部分软件的两倍柱体不同）。",),
        )
        if hist_series.value is not None:
            state = "为正（DIF 高于 DEA）" if hist_series.value > 0 else "为负（DIF 低于 DEA）"
            hist_series.explanation = f"MACD 柱体{state}，只描述当前快慢均线的相对位置。"
        result.append(hist_series)

    if "bollinger" in requested:
        middle, upper, lower = bollinger(close)
        latest_close = float(close.iloc[-1])
        middle_value = middle.iloc[-1] if not middle.dropna().empty else None
        upper_value = upper.iloc[-1] if not upper.dropna().empty else None
        lower_value = lower.iloc[-1] if not lower.dropna().empty else None
        bandwidth = None
        percent_b = None
        if middle_value and upper_value is not None and lower_value is not None and middle_value != 0:
            bandwidth = (float(upper_value) - float(lower_value)) / float(middle_value) * 100.0
            span = float(upper_value) - float(lower_value)
            percent_b = (latest_close - float(lower_value)) / span * 100.0 if span else None
        position = "上轨附近或以上" if upper_value is not None and latest_close >= float(upper_value) else (
            "下轨附近或以下" if lower_value is not None and latest_close <= float(lower_value) else "通道内部"
        )
        result.append(
            _series(
                name="bollinger_middle",
                category="volatility",
                params={"period": float(BOLLINGER_PERIOD), "std": BOLLINGER_STD},
                values=middle,
                dates=dates,
                history_points=history_points,
                explanation=f"中轨（SMA{BOLLINGER_PERIOD}）为 {float(middle_value):,.4f}。" if middle_value is not None else "",
                notes=("σ 使用总体标准差（ddof=0）。",),
            )
        )
        result.append(
            _series(
                name="bollinger_upper",
                category="volatility",
                params={"period": float(BOLLINGER_PERIOD), "std": BOLLINGER_STD},
                values=upper,
                dates=dates,
                history_points=history_points,
                explanation="上轨 = 中轨 + 2σ。",
                notes=("σ 使用总体标准差（ddof=0）。",),
            )
        )
        result.append(
            _series(
                name="bollinger_lower",
                category="volatility",
                params={"period": float(BOLLINGER_PERIOD), "std": BOLLINGER_STD},
                values=lower,
                dates=dates,
                history_points=history_points,
                explanation="下轨 = 中轨 - 2σ。",
                notes=("σ 使用总体标准差（ddof=0）。",),
            )
        )
        bandwidth_series = pd.Series(np.nan, index=close.index, dtype=float)
        if bandwidth is not None and middle_value:
            bandwidth_series = (upper - lower) / middle * 100.0
        result.append(
            _series(
                name="bollinger_bandwidth",
                category="volatility",
                params={"period": float(BOLLINGER_PERIOD), "std": BOLLINGER_STD},
                values=bandwidth_series,
                dates=dates,
                unit="%",
                history_points=history_points,
                explanation=(
                    f"带宽为 {bandwidth:.2f}%，价格{position}。带宽扩大表示近期波动区间变宽。"
                    if bandwidth is not None
                    else ""
                ),
            )
        )
        percent_b_series = pd.Series(np.nan, index=close.index, dtype=float)
        span = (upper - lower).replace(0, np.nan)
        percent_b_series = (close - lower) / span * 100.0
        result.append(
            _series(
                name="bollinger_percent_b",
                category="volatility",
                params={"period": float(BOLLINGER_PERIOD), "std": BOLLINGER_STD},
                values=percent_b_series,
                dates=dates,
                unit="%",
                history_points=history_points,
                explanation=(
                    f"价格相对位置 %B 为 {percent_b:.1f}%（0% = 下轨，100% = 上轨），价格{position}。"
                    if percent_b is not None
                    else ""
                ),
            )
        )

    if "atr" in requested:
        values = atr(frame, ATR_PERIOD)
        cleaned = values.dropna()
        latest_atr = float(cleaned.iloc[-1]) if not cleaned.empty else None
        last_close = float(close.iloc[-1])
        ratio = (latest_atr / last_close * 100.0) if latest_atr and last_close else None
        result.append(
            _series(
                name=f"atr_{ATR_PERIOD}",
                category="volatility",
                params={"period": float(ATR_PERIOD)},
                values=values,
                dates=dates,
                history_points=history_points,
                explanation=(
                    f"ATR{ATR_PERIOD} 为 {latest_atr:,.4f}（约为收盘价的 {ratio:.2f}%），"
                    "描述近期的平均波动幅度，不代表未来波动方向。"
                    if latest_atr is not None and ratio is not None
                    else ""
                ),
                notes=("TR = max(H-L, |H-Cprev|, |L-Cprev|)，采用 Wilder 平滑。",),
            )
        )

    return result


def _normalise_selection(indicators: Sequence[str] | None) -> tuple[str, ...]:
    if not indicators:
        return DEFAULT_INDICATORS
    selected: list[str] = []
    unknown: list[str] = []
    for item in indicators:
        key = str(item).strip().lower().replace(" ", "_")
        if key in INDICATOR_FAMILIES:
            if key not in selected:
                selected.append(key)
            continue
        # Accept concrete series names such as ``sma_20`` or ``macd_histogram``.
        family = key.split("_")[0]
        if family in INDICATOR_FAMILIES:
            if family not in selected:
                selected.append(family)
            continue
        unknown.append(str(item))
    if unknown:
        raise TechnicalInputError(
            "不支持的技术指标：" + "、".join(unknown) + "；可用：" + "、".join(sorted(INDICATOR_FAMILIES))
            + "（也可以写具体指标名，如 sma_20、rsi_14、macd_histogram）。"
        )
    return tuple(selected) or DEFAULT_INDICATORS


def summarize(indicators: Sequence[IndicatorSeries], *, bars: int = 0, window: str = "") -> list[str]:
    """Plain-language, history-only summary lines (never predictions)."""
    lines: list[str] = []
    by_name = {item.name: item for item in indicators}

    def _value(name: str) -> float | None:
        item = by_name.get(name)
        return None if item is None else item.value

    sma5, sma20 = _value("sma_5"), _value("sma_20")
    if sma5 is not None and sma20 is not None:
        relation = "高于" if sma5 > sma20 else ("低于" if sma5 < sma20 else "等于")
        lines.append(f"短期均线 SMA5 {relation}中期均线 SMA20（{sma5:,.4f} vs {sma20:,.4f}）。")

    rsi_value = _value(f"rsi_{RSI_PERIOD}")
    if rsi_value is not None:
        lines.append(f"RSI{RSI_PERIOD} 为 {rsi_value:.2f}，仅反映近期涨跌力量对比。")

    hist = _value("macd_histogram")
    dif, dea = _value("macd"), _value("macd_signal")
    if hist is not None and dif is not None and dea is not None:
        state = "为正" if hist > 0 else ("为负" if hist < 0 else "为零")
        lines.append(f"MACD：DIF {dif:,.4f}，DEA {dea:,.4f}，柱体{state}。")

    bandwidth = _value("bollinger_bandwidth")
    percent_b = _value("bollinger_percent_b")
    if bandwidth is not None:
        position = ""
        if percent_b is not None:
            if percent_b >= 100:
                position = "，最新收盘价位于上轨之上"
            elif percent_b <= 0:
                position = "，最新收盘价位于下轨之下"
            else:
                position = f"，最新收盘价位于通道 {percent_b:.0f}% 位置"
        lines.append(f"布林带带宽 {bandwidth:.2f}%{position}。")

    atr_value = _value(f"atr_{ATR_PERIOD}")
    if atr_value is not None:
        lines.append(f"ATR{ATR_PERIOD} 为 {atr_value:,.4f}，描述历史波动幅度。")

    if not lines:
        lines.append("没有足够的行情数据计算技术指标。")
    if window:
        lines.append(f"以上指标基于 {window} 内的 {bars} 个交易日数据，只描述历史状态，不构成买卖建议。")
    return lines


def indicator_catalog() -> list[dict[str, object]]:
    """Catalogue of the supported technical indicators."""
    return [
        {
            "name": "sma",
            "label": "简单移动平均 (SMA)",
            "category": "trend",
            "windows": list(SMA_WINDOWS),
            "formula": "SMA_n = 最近 n 个收盘价的算术平均",
            "inputs": ["close"],
            "min_bars": min(SMA_WINDOWS),
        },
        {
            "name": "ema",
            "label": "指数移动平均 (EMA)",
            "category": "trend",
            "windows": list(EMA_WINDOWS),
            "formula": "EMA_t = α·close_t + (1-α)·EMA_{t-1}, α = 2/(n+1)",
            "inputs": ["close"],
            "min_bars": min(EMA_WINDOWS),
        },
        {
            "name": "rsi",
            "label": "相对强弱指标 (RSI)",
            "category": "momentum",
            "period": RSI_PERIOD,
            "formula": "RSI = 100 - 100/(1+RS)，RS = 平均涨幅 / 平均跌幅（Wilder 平滑）",
            "inputs": ["close"],
            "min_bars": RSI_PERIOD + 1,
        },
        {
            "name": "macd",
            "label": "MACD",
            "category": "momentum",
            "params": {"fast": MACD_FAST, "slow": MACD_SLOW, "signal": MACD_SIGNAL},
            "formula": "DIF = EMA12 - EMA26；DEA = EMA9(DIF)；柱体 = DIF - DEA",
            "inputs": ["close"],
            "min_bars": MACD_SLOW + MACD_SIGNAL,
        },
        {
            "name": "bollinger",
            "label": "布林带 (Bollinger Bands)",
            "category": "volatility",
            "params": {"period": BOLLINGER_PERIOD, "std": BOLLINGER_STD},
            "formula": "中轨 = SMA20；上下轨 = 中轨 ± 2σ（σ 为总体标准差）",
            "inputs": ["close"],
            "min_bars": BOLLINGER_PERIOD,
        },
        {
            "name": "atr",
            "label": "平均真实波幅 (ATR)",
            "category": "volatility",
            "period": ATR_PERIOD,
            "formula": "TR = max(H-L, |H-Cprev|, |L-Cprev|)，ATR = Wilder 平滑的 TR",
            "inputs": ["high", "low", "close"],
            "min_bars": ATR_PERIOD + 1,
        },
    ]
