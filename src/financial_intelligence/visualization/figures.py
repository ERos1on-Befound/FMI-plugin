"""Figure builders for FMI charts.

Every function here takes *already computed* data and returns a Plotly figure.
No indicator maths happens in this module: moving averages, RSI, MACD, Bollinger
bands and ATR are produced by :mod:`financial_intelligence.analysis.technical`
from the exact same code path the analytical tools use, so a chart can never
disagree with a tool's numbers.

The chart service (:mod:`financial_intelligence.services.chart_service`) owns
data fetching, file output and metadata; this module owns pixels only.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import theme


class ChartDataError(ValueError):
    """Raised when the data cannot produce a meaningful chart."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _require_points(*series: Sequence[float] | Sequence[object], minimum: int = 2) -> None:
    if not series:
        raise ChartDataError("没有可用于绘图的数据")
    usable = max(len([v for v in item if v is not None]) for item in series)
    if usable < minimum:
        raise ChartDataError(
            f"可用数据点只有 {usable} 个，少于绘制图表所需的 {minimum} 个；"
            "请改用更长的 period 或更小的 interval 后重试。"
        )


def _clean(dates: Sequence[str], values: Sequence[float | None]) -> tuple[list[str], list[float]]:
    xs: list[str] = []
    ys: list[float] = []
    for date, value in zip(dates, values, strict=False):
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:  # NaN
            continue
        xs.append(str(date))
        ys.append(number)
    return xs, ys


def _line_trace(
    *,
    dates: Sequence[str],
    values: Sequence[float | None],
    name: str,
    color: str,
    width: float = 2.0,
    dash: str | None = None,
    fill: str | None = None,
    hover_suffix: str = "",
) -> go.Scatter | None:
    xs, ys = _clean(dates, values)
    if not xs:
        return None
    trace = go.Scatter(
        x=xs,
        y=ys,
        name=name,
        mode="lines",
        line=dict(color=color, width=width, dash=dash),
        hovertemplate=f"%{{x}}<br>{name}: %{{y:,.2f}}{hover_suffix}<extra></extra>",
    )
    if fill:
        trace.fill = fill
    return trace


# ---------------------------------------------------------------------------
# price charts
# ---------------------------------------------------------------------------
def price_figure(
    series: Sequence[Mapping[str, object]],
    *,
    title: str,
    subtitle: str | None = None,
    y_title: str = "价格",
    normalize: bool = False,
    area: bool = False,
) -> go.Figure:
    """Line / area price chart for one or more assets.

    ``series`` items: ``{"name": str, "dates": [...], "values": [...]}``.
    When ``normalize`` is true each series is rebased to 100 at its first
    available value, which is the only honest way to compare assets quoted in
    different currencies or at different price levels.
    """
    if not series:
        raise ChartDataError("没有可绘制的价格序列")
    fig = go.Figure()
    for index, item in enumerate(series):
        dates = list(item.get("dates") or [])
        values = list(item.get("values") or [])
        _require_points(values)
        xs, ys = _clean(dates, values)
        if len(ys) < 2:
            raise ChartDataError("价格序列可用点不足 2 个，无法绘制走势图")
        if normalize:
            base = ys[0]
            if base == 0:
                raise ChartDataError("起始价格为 0，无法归一化")
            ys = [value / base * 100.0 for value in ys]
        color = theme.color_for(index)
        trace = go.Scatter(
            x=xs,
            y=ys,
            name=str(item.get("name") or f"序列{index + 1}"),
            mode="lines",
            line=dict(color=color, width=2.2 if len(series) == 1 else 1.8),
            hovertemplate=(
                f"%{{x}}<br>{item.get('name') or ''}: %{{y:,.2f}}"
                f"{'(基准=100)' if normalize else ''}<extra></extra>"
            ),
        )
        if area and len(series) == 1:
            trace.fill = "tozeroy"
            trace.fillcolor = theme.hex_to_rgba(color, 0.14)
        fig.add_trace(trace)
    layout = theme.base_layout(
        title=title, subtitle=subtitle, y_title=y_title, x_title="日期"
    )
    fig.update_layout(**layout)
    return fig


def candlestick_figure(
    bars: Sequence[Mapping[str, object]],
    *,
    title: str,
    subtitle: str | None = None,
    show_volume: bool = False,
    currency: str | None = None,
) -> go.Figure:
    """OHLC candlestick chart, optionally with a volume subplot."""
    rows = [bar for bar in bars if bar.get("open") is not None and bar.get("close") is not None]
    if len(rows) < 2:
        raise ChartDataError("K 线数据不足 2 个数据点：无法绘制有意义的 K 线图")
    dates = [str(bar.get("date")) for bar in rows]
    has_volume = show_volume and any(bar.get("volume") is not None for bar in rows)
    candle = go.Candlestick(
        x=dates,
        open=[bar.get("open") for bar in rows],
        high=[bar.get("high") for bar in rows],
        low=[bar.get("low") for bar in rows],
        close=[bar.get("close") for bar in rows],
        name="OHLC",
        increasing=dict(line=dict(color=theme.UP_COLOR), fillcolor=theme.UP_COLOR),
        decreasing=dict(line=dict(color=theme.DOWN_COLOR), fillcolor=theme.DOWN_COLOR),
        hoverlabel=dict(namelength=-1),
    )
    if has_volume:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.72, 0.28],
            vertical_spacing=0.05,
        )
        fig.add_trace(candle, row=1, col=1)
        colors = [
            theme.UP_COLOR
            if (bar.get("close") or 0) >= (bar.get("open") or 0)
            else theme.DOWN_COLOR
            for bar in rows
        ]
        fig.add_trace(
            go.Bar(
                x=dates,
                y=[bar.get("volume") for bar in rows],
                name="成交量",
                marker_color=colors,
                hovertemplate="%{x}<br>成交量: %{y:,.0f}<extra></extra>",
            ),
            row=2,
            col=1,
        )
        fig.update_yaxes(title_text=currency or "价格", row=1, col=1)
        fig.update_yaxes(title_text="成交量", row=2, col=1)
        fig.update_layout(
            xaxis_rangeslider_visible=False, xaxis2_rangeslider_visible=False
        )
    else:
        fig = go.Figure(candle)
        fig.update_layout(xaxis_rangeslider_visible=False)
    layout = theme.base_layout(
        title=title,
        subtitle=subtitle,
        y_title=currency or "价格",
        x_title="日期",
        showlegend=False,
        height=700 if has_volume else 620,
        margin=dict(l=60, r=30, t=86, b=96),
    )
    fig.update_layout(**layout)
    return fig


def volume_figure(
    bars: Sequence[Mapping[str, object]],
    *,
    title: str,
    subtitle: str | None = None,
) -> go.Figure:
    """Standalone volume bar chart.  Missing volumes are never rendered as 0."""
    rows = [bar for bar in bars if bar.get("volume") is not None]
    if not rows:
        raise ChartDataError(
            "该标的数据源没有提供成交量（volume 全为缺失）；"
            "按照项目规则不会用 0 伪造成交量，因此不生成成交量图。"
        )
    if len(rows) < 3:
        raise ChartDataError("成交量数据点不足 3 个，无法绘制有意义的成交量图")
    dates = [str(bar.get("date")) for bar in rows]
    colors = [
        theme.UP_COLOR if (bar.get("close") or 0) >= (bar.get("open") or 0) else theme.DOWN_COLOR
        for bar in rows
    ]
    fig = go.Figure(
        go.Bar(
            x=dates,
            y=[bar.get("volume") for bar in rows],
            marker_color=colors,
            name="成交量",
            hovertemplate="%{x}<br>成交量: %{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(**theme.base_layout(title=title, subtitle=subtitle, y_title="成交量", x_title="日期", showlegend=False))
    return fig


# ---------------------------------------------------------------------------
# technical charts
# ---------------------------------------------------------------------------
def ma_figure(
    bars: Sequence[Mapping[str, object]],
    averages: Mapping[str, Sequence[float | None]],
    *,
    title: str,
    subtitle: str | None = None,
) -> go.Figure:
    """Close price with the selected simple moving averages."""
    closes = [bar.get("close") for bar in bars]
    _require_points(closes)
    dates = [str(bar.get("date")) for bar in bars]
    fig = go.Figure()
    close_trace = _line_trace(
        dates=dates, values=closes, name="收盘价", color="#1f2937", width=1.6
    )
    if close_trace is not None:
        fig.add_trace(close_trace)
    for index, (label, values) in enumerate(averages.items()):
        trace = _line_trace(
            dates=dates, values=values, name=label, color=theme.color_for(index + 1), width=1.5
        )
        if trace is not None:
            fig.add_trace(trace)
    if len(fig.data) < 2:
        raise ChartDataError("均线可用数据不足，无法生成均线图")
    fig.update_layout(
        **theme.base_layout(title=title, subtitle=subtitle, y_title="价格", x_title="日期")
    )
    return fig


def macd_figure(
    dates: Sequence[str],
    dif: Sequence[float | None],
    dea: Sequence[float | None],
    histogram: Sequence[float | None],
    *,
    title: str,
    subtitle: str | None = None,
) -> go.Figure:
    """MACD panel: DIF, DEA and the histogram (bar = DIF - DEA)."""
    _require_points(histogram, minimum=3)
    fig = go.Figure()
    colors = [
        theme.UP_COLOR if (value or 0) >= 0 else theme.DOWN_COLOR for value in histogram
    ]
    fig.add_trace(
        go.Bar(
            x=list(dates),
            y=list(histogram),
            name="MACD 柱 (DIF−DEA)",
            marker_color=colors,
            hovertemplate="%{x}<br>柱体: %{y:,.4f}<extra></extra>",
        )
    )
    dif_trace = _line_trace(dates=dates, values=dif, name="DIF", color=theme.PRIMARY, width=1.6)
    dea_trace = _line_trace(dates=dates, values=dea, name="DEA", color=theme.SECONDARY, width=1.6)
    if dif_trace is not None:
        fig.add_trace(dif_trace)
    if dea_trace is not None:
        fig.add_trace(dea_trace)
    theme.add_reference_line(fig, value=0.0, label="0")
    fig.update_layout(
        **theme.base_layout(title=title, subtitle=subtitle, y_title="MACD", x_title="日期")
    )
    return fig


def rsi_figure(
    dates: Sequence[str],
    values: Sequence[float | None],
    *,
    title: str,
    subtitle: str | None = None,
    references: Iterable[float] = (30.0, 50.0, 70.0),
) -> go.Figure:
    """RSI panel with the conventional 30 / 50 / 70 reference lines."""
    _require_points(values, minimum=3)
    fig = go.Figure()
    trace = _line_trace(dates=dates, values=values, name="RSI14", color=theme.PRIMARY, width=1.8)
    if trace is None:
        raise ChartDataError("RSI 序列没有可用数值")
    fig.add_trace(trace)
    for level in references:
        theme.add_reference_line(fig, value=float(level), label=f"{level:g}")
    fig.update_layout(
        **theme.base_layout(title=title, subtitle=subtitle, y_title="RSI", x_title="日期")
    )
    fig.update_yaxes(range=[0, 100])
    return fig


def bollinger_figure(
    bars: Sequence[Mapping[str, object]],
    middle: Sequence[float | None],
    upper: Sequence[float | None],
    lower: Sequence[float | None],
    *,
    title: str,
    subtitle: str | None = None,
) -> go.Figure:
    """Close price with Bollinger middle/upper/lower bands."""
    closes = [bar.get("close") for bar in bars]
    _require_points(middle, minimum=3)
    dates = [str(bar.get("date")) for bar in bars]
    fig = go.Figure()
    upper_trace = _line_trace(
        dates=dates, values=upper, name="上轨", color=theme.color_for(1), width=1.2, dash="dot"
    )
    lower_trace = _line_trace(
        dates=dates, values=lower, name="下轨", color=theme.color_for(1), width=1.2, dash="dot"
    )
    if upper_trace is not None and lower_trace is not None:
        fig.add_trace(lower_trace)
        fig.add_trace(
            go.Scatter(
                x=upper_trace.x,
                y=upper_trace.y,
                name="带宽区间",
                mode="lines",
                line=dict(color=theme.color_for(1), width=0),
                fill="tonexty",
                fillcolor=theme.hex_to_rgba(theme.color_for(1), 0.10),
                hoverinfo="skip",
                showlegend=True,
            )
        )
    middle_trace = _line_trace(
        dates=dates, values=middle, name="中轨 SMA20", color=theme.SECONDARY, width=1.5
    )
    close_trace = _line_trace(dates=dates, values=closes, name="收盘价", color="#1f2937", width=1.4)
    for trace in (middle_trace, close_trace):
        if trace is not None:
            fig.add_trace(trace)
    if len(fig.data) < 2:
        raise ChartDataError("布林带可用数据不足，无法绘图")
    fig.update_layout(
        **theme.base_layout(title=title, subtitle=subtitle, y_title="价格", x_title="日期")
    )
    return fig


def atr_figure(
    dates: Sequence[str],
    values: Sequence[float | None],
    *,
    title: str,
    subtitle: str | None = None,
) -> go.Figure:
    """ATR14 panel — historical volatility magnitude, not a forecast."""
    _require_points(values, minimum=3)
    fig = go.Figure()
    trace = _line_trace(dates=dates, values=values, name="ATR14", color=theme.color_for(3), width=1.8)
    if trace is None:
        raise ChartDataError("ATR 序列没有可用数值")
    fig.add_trace(trace)
    fig.update_layout(**theme.base_layout(title=title, subtitle=subtitle, y_title="ATR", x_title="日期"))
    return fig


# ---------------------------------------------------------------------------
# fundamental charts
# ---------------------------------------------------------------------------
def fundamental_figure(
    periods: Sequence[str],
    series: Mapping[str, Sequence[float | None]],
    *,
    title: str,
    subtitle: str | None = None,
    y_title: str = "",
    percent: bool = False,
    as_line: bool = False,
    text_labels: bool = False,
) -> go.Figure:
    """Bar or line chart of one or more fundamental metrics across periods."""
    if not periods:
        raise ChartDataError("没有可用的财务报告期")
    fig = go.Figure()
    for index, (label, values) in enumerate(series.items()):
        cleaned = [
            None if value is None or value != value else float(value) for value in values
        ]
        if all(value is None for value in cleaned):
            continue
        hover = " %{y:,.2f}%" if percent else " %{y:,.4g}"
        if as_line:
            fig.add_trace(
                go.Scatter(
                    x=list(periods),
                    y=cleaned,
                    name=label,
                    mode="lines+markers",
                    line=dict(color=theme.color_for(index), width=2),
                    marker=dict(size=7),
                    hovertemplate="%{x}<br>" + label + hover + "<extra></extra>",
                )
            )
        else:
            fig.add_trace(
                go.Bar(
                    x=list(periods),
                    y=cleaned,
                    name=label,
                    marker_color=theme.color_for(index),
                    text=(
                        [
                            "" if value is None else f"{value:,.2f}"
                            for value in cleaned
                        ]
                        if text_labels
                        else None
                    ),
                    textposition="outside" if text_labels else None,
                    hovertemplate="%{x}<br>" + label + hover + "<extra></extra>",
                )
            )
    if not fig.data:
        raise ChartDataError("所选期间的指标全部缺失，无法生成财务趋势图")
    fig.update_layout(
        **theme.base_layout(
            title=title,
            subtitle=subtitle,
            y_title=y_title,
            x_title="报告期",
            showlegend=len(series) > 1,
            height=560,
        )
    )
    if not as_line and len(series) > 1:
        fig.update_layout(barmode="group")
    return fig


def comparison_figure(
    categories: Sequence[str],
    series: Mapping[str, Sequence[float | None]],
    *,
    title: str,
    subtitle: str | None = None,
    y_title: str = "",
    percent: bool = False,
) -> go.Figure:
    """Grouped bar chart comparing companies on one or more metrics."""
    if not categories:
        raise ChartDataError("没有可比较的公司")
    fig = go.Figure()
    for index, (label, values) in enumerate(series.items()):
        cleaned = [
            None if value is None or value != value else float(value) for value in values
        ]
        if all(value is None for value in cleaned):
            continue
        hover = " %{y:,.2f}%" if percent else " %{y:,.4g}"
        fig.add_trace(
            go.Bar(
                x=list(categories),
                y=cleaned,
                name=label,
                marker_color=theme.color_for(index),
                hovertemplate="%{x}<br>" + label + hover + "<extra></extra>",
            )
        )
    if not fig.data:
        raise ChartDataError("所选指标在所有公司上都不可用，无法生成对比图")
    fig.update_layout(
        **theme.base_layout(
            title=title, subtitle=subtitle, y_title=y_title, x_title="公司", height=560
        )
    )
    if len(series) > 1:
        fig.update_layout(barmode="group")
    return fig
