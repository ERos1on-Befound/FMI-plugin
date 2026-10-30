"""Tests for the technical indicator engine.

Every indicator is verified against an *independent* reference implementation
written directly from the documented definition, so a wrong formula fails the
test instead of just returning "some number".
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from financial_intelligence.analysis.technical import (
    ATR_PERIOD,
    BOLLINGER_PERIOD,
    EMA_WINDOWS,
    INDICATOR_FAMILIES,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
    SMA_WINDOWS,
    TechnicalInputError,
    atr,
    bars_to_frame,
    bollinger,
    compute_indicators,
    ema,
    indicator_catalog,
    macd,
    rsi,
    sma,
    summarize,
)
from financial_intelligence.schemas import HistoricalBar


# ---------------------------------------------------------------------------
# helpers: bars + independent reference implementations
# ---------------------------------------------------------------------------
def make_bars(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    opens: list[float] | None = None,
    start: date = date(2026, 1, 1),
) -> list[HistoricalBar]:
    bars: list[HistoricalBar] = []
    for index, close in enumerate(closes):
        bars.append(
            HistoricalBar(
                date=(start + timedelta(days=index)).isoformat(),
                open=opens[index] if opens else close,
                high=highs[index] if highs else close,
                low=lows[index] if lows else close,
                close=close,
                volume=1_000.0 + index,
                symbol="TEST",
                currency="USD",
                source="test",
            )
        )
    return bars


def ref_ema(values: list[float], window: int) -> list[float]:
    alpha = 2.0 / (window + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def ref_wilder(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    previous = sum(values[:period]) / period
    result[period - 1] = previous
    for index in range(period, len(values)):
        previous = (previous * (period - 1) + values[index]) / period
        result[index] = previous
    return result


def ref_atr(highs: list[float], lows: list[float], closes: list[float], period: int = ATR_PERIOD) -> list[float | None]:
    true_ranges = [highs[0] - lows[0]]
    for index in range(1, len(highs)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    smoothed = ref_wilder(true_ranges[1:], period)
    return [None] + smoothed


def by_name(series_list, name):
    for item in series_list:
        if item.name == name:
            return item
    raise AssertionError(f"missing series {name}: {[s.name for s in series_list]}")


ASCENDING = [float(value) for value in range(1, 41)]


# ---------------------------------------------------------------------------
# input handling
# ---------------------------------------------------------------------------
def test_bars_to_frame_sorts_and_dedupes() -> None:
    bars = make_bars([10.0, 11.0, 12.0])
    shuffled = [bars[2], bars[0], bars[1], bars[1]]
    frame = bars_to_frame(shuffled)
    assert len(frame) == 3
    assert list(frame["close"]) == [10.0, 11.0, 12.0]


def test_bars_to_frame_rejects_empty() -> None:
    with pytest.raises(TechnicalInputError):
        bars_to_frame([])


def test_bars_to_frame_requires_close() -> None:
    bar = HistoricalBar(date="2026-01-01", close=None, symbol="TEST", source="test")
    with pytest.raises(TechnicalInputError):
        bars_to_frame([bar])


def test_missing_high_low_filled_from_close() -> None:
    bar = HistoricalBar(date="2026-01-01", close=10.0, symbol="TEST", source="test")
    frame = bars_to_frame([bar])
    assert frame.loc[0, "high"] == 10.0
    assert frame.loc[0, "low"] == 10.0
    assert frame.loc[0, "open"] == 10.0


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def test_sma_matches_reference() -> None:
    values = [float(value) for value in range(1, 31)]
    import pandas as pd

    result = sma(pd.Series(values), 5)
    assert result.iloc[-1] == pytest.approx(28.0)
    assert result.iloc[-2] == pytest.approx(27.0)
    assert np.isnan(result.iloc[3])


def test_ema_matches_reference() -> None:
    import pandas as pd

    values = [float(value) for value in range(1, 31)]
    expected = ref_ema(values, 12)
    result = ema(pd.Series(values), 12)
    assert result.iloc[-1] == pytest.approx(expected[-1])
    assert result.iloc[0] == pytest.approx(values[0])


def test_rsi_monotonic_rise_is_100() -> None:
    import pandas as pd

    result = rsi(pd.Series(ASCENDING), RSI_PERIOD)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_monotonic_fall_is_0() -> None:
    import pandas as pd

    result = rsi(pd.Series(list(reversed(ASCENDING))), RSI_PERIOD)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_rsi_flat_series_is_50() -> None:
    import pandas as pd

    result = rsi(pd.Series([5.0] * 30), RSI_PERIOD)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_rsi_matches_reference_on_mixed_series() -> None:
    import pandas as pd

    closes = [10, 11, 10.5, 12, 11.8, 13, 12.2, 14, 13.5, 15, 14.2, 16, 15.1, 17, 16.4, 18, 17.2, 19, 18.1, 20]
    values = [float(value) for value in closes]
    changes = np.diff(values)
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = ref_wilder(gains, RSI_PERIOD)
    avg_loss = ref_wilder(losses, RSI_PERIOD)
    index = len(values) - 1
    rs = avg_gain[index - 1] / avg_loss[index - 1]
    expected = 100.0 - 100.0 / (1.0 + rs)
    result = rsi(pd.Series(values), RSI_PERIOD)
    assert result.iloc[-1] == pytest.approx(expected)


def test_macd_components_match_reference() -> None:
    import pandas as pd

    series = pd.Series(ASCENDING)
    dif, dea, hist = macd(series)
    fast = ref_ema(ASCENDING, MACD_FAST)
    slow = ref_ema(ASCENDING, MACD_SLOW)
    expected_dif = [f - s for f, s in zip(fast, slow)]
    expected_dea = ref_ema(expected_dif, MACD_SIGNAL)
    assert dif.iloc[-1] == pytest.approx(expected_dif[-1])
    assert dea.iloc[-1] == pytest.approx(expected_dea[-1])
    assert hist.iloc[-1] == pytest.approx(expected_dif[-1] - expected_dea[-1])


def test_bollinger_uses_population_std() -> None:
    import pandas as pd

    series = pd.Series([float(value) for value in range(11, 31)])  # 20 values 11..30
    middle, upper, lower = bollinger(series)
    expected_std = float(np.std(np.arange(11, 31), ddof=0))
    assert middle.iloc[-1] == pytest.approx(20.5)
    assert upper.iloc[-1] == pytest.approx(20.5 + 2 * expected_std)
    assert lower.iloc[-1] == pytest.approx(20.5 - 2 * expected_std)


def test_atr_constant_range() -> None:
    import pandas as pd

    closes = [10.0] * 30
    highs = [11.0] * 30
    lows = [9.0] * 30
    frame = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes})
    result = atr(frame, ATR_PERIOD)
    assert result.iloc[-1] == pytest.approx(2.0)


def test_atr_matches_reference_with_gaps() -> None:
    import pandas as pd

    closes = [10.0 + 0.5 * index for index in range(30)]
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.2 for value in closes]
    frame = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes})
    expected = ref_atr(highs, lows, closes, ATR_PERIOD)
    result = atr(frame, ATR_PERIOD)
    assert result.iloc[-1] == pytest.approx(expected[-1])


# ---------------------------------------------------------------------------
# series assembly
# ---------------------------------------------------------------------------
def test_compute_indicators_returns_all_default_series() -> None:
    series_list = compute_indicators(make_bars(ASCENDING))
    names = {item.name for item in series_list}
    for window in SMA_WINDOWS:
        assert f"sma_{window}" in names
    for window in EMA_WINDOWS:
        assert f"ema_{window}" in names
    assert f"rsi_{RSI_PERIOD}" in names
    assert {"macd", "macd_signal", "macd_histogram"} <= names
    assert {
        "bollinger_middle",
        "bollinger_upper",
        "bollinger_lower",
        "bollinger_bandwidth",
        "bollinger_percent_b",
    } <= names
    assert f"atr_{ATR_PERIOD}" in names
    assert all(item.date == "2026-02-09" for item in series_list if item.status == "ok")


def test_sma_values_and_change() -> None:
    series_list = compute_indicators(make_bars(ASCENDING), ["sma"])
    sma5 = by_name(series_list, "sma_5")
    assert sma5.value == pytest.approx(38.0)
    assert sma5.previous_value == pytest.approx(37.0)
    assert sma5.change == pytest.approx(1.0)
    assert sma5.change_pct == pytest.approx(100.0 / 37.0)
    assert sma5.status == "ok"


def test_sma_insufficient_history() -> None:
    series_list = compute_indicators(make_bars([float(value) for value in range(1, 31)]), ["sma"])
    sma60 = by_name(series_list, "sma_60")
    assert sma60.status == "insufficient_data"
    assert sma60.value is None
    assert "60" in sma60.explanation


def test_selection_only_requested_families() -> None:
    series_list = compute_indicators(make_bars(ASCENDING), ["rsi"])
    assert [item.name for item in series_list] == [f"rsi_{RSI_PERIOD}"]


def test_selection_accepts_concrete_names() -> None:
    series_list = compute_indicators(make_bars(ASCENDING), ["sma_20", "macd_histogram"])
    assert {item.name for item in series_list} == {"sma_5", "sma_10", "sma_20", "sma_60", "macd", "macd_signal", "macd_histogram"}


def test_selection_rejects_unknown_indicator() -> None:
    with pytest.raises(TechnicalInputError):
        compute_indicators(make_bars(ASCENDING), ["bogus"])


def test_history_is_capped() -> None:
    series_list = compute_indicators(make_bars(ASCENDING), ["sma"], history_points=5)
    sma5 = by_name(series_list, "sma_5")
    assert len(sma5.history) == 5
    assert sma5.history[-1]["date"] == "2026-02-09"


def test_rsi_zone_explanation_is_descriptive_only() -> None:
    series_list = compute_indicators(make_bars(ASCENDING), ["rsi"])
    item = by_name(series_list, f"rsi_{RSI_PERIOD}")
    assert "相对高位" in item.explanation
    assert "卖出信号" in item.explanation  # explicitly disclaims signal semantics


def test_bollinger_bandwidth_and_percent_b() -> None:
    closes = [float(value) for value in range(11, 31)]
    series_list = compute_indicators(make_bars(closes), ["bollinger"])
    middle = by_name(series_list, "bollinger_middle")
    upper = by_name(series_list, "bollinger_upper")
    lower = by_name(series_list, "bollinger_lower")
    bandwidth = by_name(series_list, "bollinger_bandwidth")
    percent_b = by_name(series_list, "bollinger_percent_b")
    expected_bandwidth = (upper.value - lower.value) / middle.value * 100.0
    assert bandwidth.value == pytest.approx(expected_bandwidth)
    expected_percent_b = (30.0 - lower.value) / (upper.value - lower.value) * 100.0
    assert percent_b.value == pytest.approx(expected_percent_b)


def test_atr_series_on_gapped_bars() -> None:
    closes = ASCENDING
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.0 for value in closes]
    series_list = compute_indicators(make_bars(closes, highs=highs, lows=lows), ["atr"])
    item = by_name(series_list, f"atr_{ATR_PERIOD}")
    expected = ref_atr(highs, lows, closes, ATR_PERIOD)
    assert item.value == pytest.approx(expected[-1])
    assert "波动" in item.explanation


def test_insufficient_bars_for_macd_and_atr() -> None:
    series_list = compute_indicators(make_bars([10.0 + index for index in range(12)]), ["macd", "atr"])
    macd_series = by_name(series_list, "macd")
    atr_series = by_name(series_list, f"atr_{ATR_PERIOD}")
    assert macd_series.status == "insufficient_data"
    assert macd_series.value is None
    assert atr_series.status == "insufficient_data"
    # The EMA26 is not reported as a 26-day EMA when only 12 bars exist.
    ema_series = by_name(compute_indicators(make_bars([10.0 + index for index in range(12)]), ["ema"]), "ema_26")
    assert ema_series.status == "insufficient_data"


def test_summarize_describes_history_only() -> None:
    series_list = compute_indicators(make_bars(ASCENDING))
    lines = summarize(series_list, bars=40, window="2026-01-01 至 2026-02-09")
    text = "\n".join(lines)
    assert "SMA" in text
    assert "RSI" in text
    for forbidden in ("必然", "将会上涨", "目标价", "建议买入", "建议卖出", "保证收益"):
        assert forbidden not in text
    assert "不构成买卖建议" in text


def test_summarize_without_indicators() -> None:
    lines = summarize([])
    assert lines and "没有足够" in lines[0]


def test_catalog_covers_all_families() -> None:
    catalog = indicator_catalog()
    assert {entry["name"] for entry in catalog} == set(INDICATOR_FAMILIES)
    for entry in catalog:
        assert entry["formula"]
        assert entry["min_bars"] >= 1
