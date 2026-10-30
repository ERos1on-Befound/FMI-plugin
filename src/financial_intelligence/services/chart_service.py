"""Chart generation service (Phase 3).

The service owns data acquisition, indicator reuse, file output and metadata.
Figure construction lives in :mod:`financial_intelligence.visualization.figures`
and indicator maths is *never* re-implemented here: technical charts call
:func:`financial_intelligence.analysis.technical.compute_indicators`, the same
function the ``get_technical_indicators`` tool uses, so a chart and a tool can
never disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis import technical as ta
from ..analysis.financial_ratios import compute_metric
from ..config import Settings, default_outputs_dir, load_settings
from ..models.chart_result import (
    AvailableChartsResult,
    ChartMetadata,
    ChartResult,
    ChartTypeInfo,
)
from ..utils import InputError, map_symbol
from ..visualization import figures as figs
from ..visualization import theme

CHART_TYPES: tuple[str, ...] = (
    "line",
    "area",
    "candlestick",
    "volume",
    "technical",
    "fundamental",
)
TECHNICAL_INDICATORS: tuple[str, ...] = ("ma", "macd", "rsi", "bollinger", "atr")
FUNDAMENTAL_METRICS: tuple[str, ...] = (
    "revenue",
    "net_income",
    "gross_margin",
    "net_margin",
    "roe",
    "cash_flow",
    "free_cash_flow",
)
NORMALIZATIONS: tuple[str, ...] = ("none", "base_100")
RATIO_METRICS = frozenset(
    {"gross_margin", "operating_margin", "net_margin", "roe", "roa", "debt_ratio"}
)
AMOUNT_METRICS = frozenset({"revenue", "net_income", "cash_flow", "free_cash_flow"})

_CHART_ALIASES: dict[str, str] = {
    "line": "line",
    "price": "line",
    "prices": "line",
    "area": "area",
    "candlestick": "candlestick",
    "kline": "candlestick",
    "k_line": "candlestick",
    "volume": "volume",
    "vol": "volume",
    "technical": "technical",
    "indicator": "technical",
    "indicators": "technical",
    "fundamental": "fundamental",
    "financial": "fundamental",
    "finance": "fundamental",
}

_INDICATOR_ALIASES: dict[str, str] = {
    "ma": "ma",
    "sma": "ma",
    "moving_average": "ma",
    "均线": "ma",
    "macd": "macd",
    "rsi": "rsi",
    "bollinger": "bollinger",
    "boll": "bollinger",
    "bbands": "bollinger",
    "布林": "bollinger",
    "布林带": "bollinger",
    "atr": "atr",
}

_METRIC_ALIASES: dict[str, str] = {
    "revenue": "revenue",
    "营收": "revenue",
    "net_income": "net_income",
    "netincome": "net_income",
    "净利润": "net_income",
    "gross_margin": "gross_margin",
    "毛利率": "gross_margin",
    "net_margin": "net_margin",
    "净利率": "net_margin",
    "roe": "roe",
    "cash_flow": "cash_flow",
    "ocf": "cash_flow",
    "现金流": "cash_flow",
    "free_cash_flow": "free_cash_flow",
    "fcf": "free_cash_flow",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value))


def _unique(path: Path) -> Path:
    """Return a path that does not exist yet (never overwrite user files)."""
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"无法为 {path.name} 找到可用的文件名")


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _resolve(kind: str, values: Sequence[str] | None, table: dict[str, str], allowed: Sequence[str]) -> tuple[str, ...]:
    if not values:
        return ()
    resolved: list[str] = []
    for raw in values:
        key = raw.strip().lower()
        mapped = table.get(key)
        if mapped is None:
            raise InputError(
                f"不支持的{kind} “{raw}”；可用取值：{', '.join(allowed)}。"
            )
        if mapped not in resolved:
            resolved.append(mapped)
    return tuple(resolved)


def _series_values(series: Any, dates: Sequence[str]) -> list[float | None]:
    """Align an indicator history onto the date axis (missing points -> None)."""
    lookup: dict[str, float] = {}
    for row in series.history or []:
        date = str(row.get("date") or "")
        value = row.get("value")
        if date and value is not None:
            lookup[date] = float(value)
    return [lookup.get(str(date)) for date in dates]


class ChartService:
    """Generate self-contained chart files for an agent."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        market: Any | None = None,
        technical: Any | None = None,
        fundamental: Any | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._market = market
        self._technical = technical
        self._fundamental = fundamental
        base = (self._settings.outputs_dir or "").strip()
        self._outputs = Path(base).expanduser() if base else default_outputs_dir()

    # -- lazy collaborators --------------------------------------------------
    @property
    def market(self) -> Any:
        if self._market is None:
            from .market_service import MarketService

            self._market = MarketService(self._settings)
        return self._market

    @property
    def technical(self) -> Any:
        if self._technical is None:
            from .technical_service import TechnicalService

            self._technical = TechnicalService(self._settings, market=self.market)
        return self._technical

    @property
    def fundamental(self) -> Any:
        if self._fundamental is None:
            from .fundamental_service import FundamentalService

            self._fundamental = FundamentalService(self._settings, market=self.market)
        return self._fundamental

    @property
    def outputs_dir(self) -> Path:
        return self._outputs

    # -- catalogue -----------------------------------------------------------
    def available(self) -> AvailableChartsResult:
        catalog = [
            ChartTypeInfo(
                name="line",
                label="历史价格走势图（折线）",
                category="price",
                description="单只资产的历史收盘价折线图，可用 area 类型改为面积图。",
                requires=["symbol", "period"],
                options=["period", "interval"],
            ),
            ChartTypeInfo(
                name="area",
                label="历史价格走势图（面积）",
                category="price",
                description="与折线图相同的数据，用面积填充突出价格区间。",
                requires=["symbol", "period"],
                options=["period", "interval"],
            ),
            ChartTypeInfo(
                name="candlestick",
                label="K 线图（OHLC + 成交量）",
                category="price",
                description="开高低收蜡烛图，成交量充足时附带成交量副图。",
                requires=["symbol", "period", "open/high/low/close"],
                options=["period", "interval"],
                limitations="数据源未提供成交量时只绘制价格部分，不会伪造成交量。",
            ),
            ChartTypeInfo(
                name="volume",
                label="成交量图",
                category="price",
                description="按交易日绘制的成交量柱状图，颜色与涨跌一致。",
                requires=["symbol", "volume"],
                options=["period", "interval"],
                limitations="数据源没有成交量时该图不可用，也不会用 0 代替。",
            ),
            ChartTypeInfo(
                name="technical",
                label="技术指标图",
                category="technical",
                description="复用分析模块的计算结果绘制均线/MACD/RSI/布林带/ATR 多面板图。",
                requires=["symbol", "period", "interval"],
                options=[*TECHNICAL_INDICATORS],
                limitations="K 线数量不足时指标会标记 insufficient_data，不会画伪值。",
            ),
            ChartTypeInfo(
                name="fundamental",
                label="基本面趋势图",
                category="fundamental",
                description="按财务报告期绘制营收、净利润、利润率、ROE、现金流等趋势。",
                requires=["symbol", "financial statements"],
                options=[*FUNDAMENTAL_METRICS],
                limitations="缺少报告期的科目不会被补 0，报告期按时间正序排列。",
            ),
        ]
        notes = [
            "HTML 为默认输出格式（内联 Plotly，可离线打开）；PNG 为辅助格式，失败不影响 HTML。",
            "多标的对比图在币种不同时必须使用 base_100 归一化。",
        ]
        if not self._settings.enable_png_export:
            notes.append("当前配置关闭了 PNG 导出（FMI_ENABLE_PNG_EXPORT=0）。")
        return AvailableChartsResult(
            chart_types=catalog,
            technical_indicators=list(TECHNICAL_INDICATORS),
            fundamental_metrics=list(FUNDAMENTAL_METRICS),
            normalizations=list(NORMALIZATIONS),
            output_formats=["html", "png", "json"],
            png_export_enabled=bool(self._settings.enable_png_export),
            outputs_dir=str(self._outputs),
            notes=notes,
            generated_at=_now(),
        )

    # -- public entry points -------------------------------------------------
    def generate(
        self,
        symbol: str,
        *,
        chart_type: str = "line",
        period: str = "6mo",
        interval: str = "1d",
        indicators: Any = None,
        metrics: Any = None,
        market: str | None = None,
        period_type: str | None = None,
    ) -> ChartResult:
        kind = _CHART_ALIASES.get(str(chart_type or "line").strip().lower())
        if kind is None:
            raise InputError(
                f"不支持的图表类型 “{chart_type}”；可用取值：{', '.join(CHART_TYPES)}。"
            )
        spec = map_symbol(symbol, market=market)
        if kind == "technical":
            return self._technical_chart(spec, period, interval, indicators)
        if kind == "fundamental":
            # ``metrics`` is the explicit name for financial series; ``indicators``
            # is accepted as well so the MCP tool (which only exposes
            # ``indicators``) can drive both chart families.
            selection = metrics if metrics is not None else indicators
            return self._fundamental_chart(spec, selection, period_type or "annual")
        return self._price_chart(spec, kind, period, interval)

    def comparison(
        self,
        symbols: Any,
        *,
        metric: str = "price",
        period: str = "6mo",
        normalization: str = "none",
        market: str | None = None,
        period_type: str | None = None,
    ) -> ChartResult:
        names = _split(symbols)
        if len(names) < 2:
            raise InputError("请至少提供两个标的代码才能生成对比图。")
        spec_list = [map_symbol(name, market=market) for name in names]
        choice = str(metric or "price").strip().lower()
        mode = str(normalization or "none").strip().lower()
        if mode not in NORMALIZATIONS:
            raise InputError(f"不支持的归一化方式 “{normalization}”；可用取值：none, base_100。")
        if choice in {"price", "prices", "quote", "relative", "normalized", "performance"}:
            normalize = mode == "base_100" or choice in {"relative", "normalized", "performance"}
            return self._price_comparison(spec_list, period, normalize)
        if choice in AMOUNT_METRICS or choice in RATIO_METRICS:
            return self._fundamental_comparison(spec_list, choice, period_type or "annual")
        raise InputError(
            f"不支持的对比指标 “{metric}”；可用 price、{', '.join(sorted(AMOUNT_METRICS | RATIO_METRICS))}。"
        )

    # -- price charts --------------------------------------------------------
    def _price_chart(self, spec: Any, kind: str, period: str, interval: str) -> ChartResult:
        history = self.market.get_historical_data(spec.symbol, period=period, interval=interval)
        if not history.bars:
            return self._unavailable(
                spec,
                kind,
                history.error or "没有取到可用的历史行情数据。",
                source=history.source,
            )
        rows = [bar.model_dump() for bar in history.bars]
        dates = [str(row.get("date")) for row in rows]
        closes = [row.get("close") for row in rows]
        asset = history.name or spec.name_hint or spec.symbol
        currency = history.currency or spec.currency or "UNKNOWN"
        data_range = f"{dates[0]} 至 {dates[-1]}"
        subtitle = (
            f"{data_range} · {currency} · 来源 {history.source} · "
            f"数据状态 {history.data_status.value}"
        )
        try:
            if kind in {"line", "area"}:
                fig = figs.price_figure(
                    [{"name": asset, "dates": dates, "values": closes}],
                    title=f"{asset}（{spec.symbol}）历史价格走势",
                    subtitle=subtitle,
                    y_title=f"收盘价（{currency}）",
                    area=kind == "area",
                )
            elif kind == "candlestick":
                fig = figs.candlestick_figure(
                    rows,
                    title=f"{asset}（{spec.symbol}）K 线图",
                    subtitle=subtitle,
                    show_volume=True,
                    currency=f"价格（{currency}）",
                )
            else:
                fig = figs.volume_figure(
                    rows,
                    title=f"{asset}（{spec.symbol}）成交量",
                    subtitle=subtitle,
                )
        except figs.ChartDataError as exc:
            return self._unavailable(spec, kind, str(exc), source=history.source)
        theme.add_source_note(
            fig,
            source=history.source,
            data_timestamp=dates[-1],
            generated_at=history.retrieved_at,
            data_status=history.data_status.value,
        )
        return self._emit(
            fig,
            spec=spec,
            chart_type=kind,
            category="stock",
            asset=asset,
            data_range=data_range,
            source=history.source,
            retrieved_at=history.retrieved_at,
            data_timestamp=dates[-1],
            currency=currency,
            points=len(rows),
            period=period,
            interval=interval,
        )

    # -- technical charts ----------------------------------------------------
    def _technical_chart(
        self, spec: Any, period: str, interval: str, indicators: Any
    ) -> ChartResult:
        selection = _resolve(
            "技术指标", _split(indicators) or list(("ma", "macd", "rsi")), _INDICATOR_ALIASES, TECHNICAL_INDICATORS
        )
        tech_selection: list[str] = []
        for family in selection:
            if family == "ma":
                tech_selection.extend(["sma", "ema"])
            else:
                tech_selection.append(family)
        history = self.market.get_historical_data(spec.symbol, period=period, interval=interval)
        if not history.bars:
            return self._unavailable(
                spec,
                "technical",
                history.error or "没有取到可用的历史行情数据。",
                source=history.source,
            )
        bars = list(history.bars)
        dates = [str(bar.date) for bar in bars]
        rows = [bar.model_dump() for bar in bars]
        try:
            series = ta.compute_indicators(bars, tech_selection, history_points=len(bars) + 2)
        except ta.TechnicalInputError as exc:
            return self._unavailable(spec, "technical", str(exc), source=history.source)
        by_name = {item.name: item for item in series}
        unavailable = [item.name for item in series if not item.history]
        panels: list[tuple[str, go.Figure]] = []
        try:
            if "ma" in selection:
                averages: dict[str, list[float | None]] = {}
                for window in ta.SMA_WINDOWS:
                    item = by_name.get(f"sma_{window}")
                    if item is not None and item.history:
                        averages[f"MA{window}"] = _series_values(item, dates)
                if averages:
                    panels.append(
                        (
                            "均线系统（收盘价与 MA5/10/20/60）",
                            figs.ma_figure(rows, averages, title="", subtitle=None),
                        )
                    )
            if "macd" in selection:
                dif = by_name.get("macd")
                dea = by_name.get("macd_signal")
                hist = by_name.get("macd_histogram")
                if dif and dea and hist and dif.history:
                    panels.append(
                        (
                            "MACD（DIF / DEA / Histogram，柱体 = DIF − DEA）",
                            figs.macd_figure(
                                dates,
                                _series_values(dif, dates),
                                _series_values(dea, dates),
                                _series_values(hist, dates),
                                title="",
                                subtitle=None,
                            ),
                        )
                    )
            if "rsi" in selection:
                rsi = by_name.get("rsi_14")
                if rsi and rsi.history:
                    panels.append(
                        (
                            "RSI14（30/50/70 仅作参考区间）",
                            figs.rsi_figure(
                                dates, _series_values(rsi, dates), title="", subtitle=None
                            ),
                        )
                    )
            if "bollinger" in selection:
                middle = by_name.get("bollinger_middle")
                upper = by_name.get("bollinger_upper")
                lower = by_name.get("bollinger_lower")
                if middle and upper and lower and middle.history:
                    panels.append(
                        (
                            "布林带（20 日，2 倍总体标准差）",
                            figs.bollinger_figure(
                                rows,
                                _series_values(middle, dates),
                                _series_values(upper, dates),
                                _series_values(lower, dates),
                                title="",
                                subtitle=None,
                            ),
                        )
                    )
            if "atr" in selection:
                atr = by_name.get("atr_14")
                if atr and atr.history:
                    panels.append(
                        (
                            "ATR14（历史波动幅度，不代表预测）",
                            figs.atr_figure(
                                dates, _series_values(atr, dates), title="", subtitle=None
                            ),
                        )
                    )
        except figs.ChartDataError as exc:
            return self._unavailable(spec, "technical", str(exc), source=history.source)
        if not panels:
            return self._unavailable(
                spec,
                "technical",
                "所选技术指标在可用 K 线数量下都无法计算，因此没有生成图表。",
                source=history.source,
            )
        asset = history.name or spec.name_hint or spec.symbol
        currency = history.currency or spec.currency or "UNKNOWN"
        data_range = f"{dates[0]} 至 {dates[-1]}"
        subtitle = (
            f"{data_range} · {currency} · 来源 {history.source} · "
            f"数据状态 {history.data_status.value}"
        )
        notes: list[str] = []
        if unavailable:
            notes.append(f"以下指标数据不足，未绘制：{', '.join(unavailable)}。")
        fig = self._stack(panels, title=f"{asset}（{spec.symbol}）技术指标", subtitle=subtitle)
        theme.add_source_note(
            fig,
            source=history.source,
            data_timestamp=dates[-1],
            generated_at=history.retrieved_at,
            data_status=history.data_status.value,
            extra="指标口径见 references/technical-guide.md；只描述历史状态",
        )
        return self._emit(
            fig,
            spec=spec,
            chart_type="technical",
            category="technical",
            asset=asset,
            data_range=data_range,
            source=history.source,
            retrieved_at=history.retrieved_at,
            data_timestamp=dates[-1],
            currency=currency,
            points=len(rows),
            period=period,
            interval=interval,
            extra_notes=notes,
        )

    @staticmethod
    def _stack(panels: Sequence[tuple[str, go.Figure]], *, title: str, subtitle: str | None) -> go.Figure:
        count = len(panels)
        fig = make_subplots(
            rows=count,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.045,
            subplot_titles=[label for label, _ in panels],
        )
        for row, (_, sub) in enumerate(panels, start=1):
            for trace in sub.data:
                fig.add_trace(trace, row=row, col=1)
        # hlines/shapes live on the source figure, so re-add the panel guides
        # that make the RSI and MACD panels readable after merging.
        for row, (label, _sub) in enumerate(panels, start=1):
            if "RSI" in label:
                for level in (30.0, 50.0, 70.0):
                    fig.add_hline(
                        y=level,
                        line=dict(color=theme.GRID_COLOR, width=1, dash="dot"),
                        row=row,
                        col=1,
                    )
            elif "MACD" in label:
                fig.add_hline(
                    y=0.0,
                    line=dict(color=theme.AXIS_LINE, width=1),
                    row=row,
                    col=1,
                )
        layout = theme.base_layout(
            title=title,
            subtitle=subtitle,
            height=230 + 260 * count,
            showlegend=True,
            margin=dict(l=60, r=30, t=118, b=118),
        )
        layout.pop("xaxis", None)
        layout.pop("yaxis", None)
        # ``update_layout`` replaces the whole annotations list, which would drop
        # the subplot titles created by make_subplots; add the subtitle afterwards.
        extra_annotations = layout.pop("annotations", [])
        # A shared legend below the panels keeps it clear of the per-panel titles.
        layout["legend"] = dict(orientation="h", yanchor="top", y=-0.035, xanchor="left", x=0.0)
        fig.update_layout(**layout)
        for annotation in extra_annotations:
            fig.add_annotation(**annotation)
        fig.update_annotations(font=dict(size=12, color=theme.MUTED))
        return fig

    # -- fundamental charts --------------------------------------------------
    def _fundamental_chart(self, spec: Any, metrics: Any, period_type: str) -> ChartResult:
        selection = _resolve(
            "财务指标", _split(metrics) or ["revenue", "net_income"], _METRIC_ALIASES, FUNDAMENTAL_METRICS
        )
        try:
            bundle = self.fundamental.get_financials(
                spec.symbol, period_type=period_type, limit=8
            )
        except InputError as exc:
            return self._unavailable(spec, "fundamental", str(exc))
        if not bundle.statements:
            return self._unavailable(
                spec,
                "fundamental",
                bundle.error or "没有取到可用的财务报表数据。",
                source=bundle.source,
            )
        statements = list(bundle.statements)  # newest first
        ordered = list(reversed(statements))  # oldest -> newest
        periods = [item.fiscal_period for item in ordered]
        series: dict[str, list[float | None]] = {}
        missing: list[str] = []
        for name in selection:
            label: str
            values: list[float | None]
            if name == "revenue":
                label = "营业收入"
                values = [item.income_statement.get("revenue") for item in ordered]
            elif name == "net_income":
                label = "净利润"
                values = [item.income_statement.get("net_income") for item in ordered]
            elif name == "cash_flow":
                label = "经营现金流"
                values = [
                    item.cash_flow_statement.get("operating_cash_flow") for item in ordered
                ]
                series["经营现金流"] = values
                free = [item.cash_flow_statement.get("free_cash_flow") for item in ordered]
                series["自由现金流"] = free
                if all(value is None for value in values) and all(
                    value is None for value in free
                ):
                    missing.append(name)
                continue
            else:
                values = []
                for index in range(len(ordered)):
                    metric = compute_metric(name, statements, index=len(ordered) - 1 - index)
                    values.append(metric.value)
                label = {
                    "gross_margin": "毛利率",
                    "net_margin": "净利率",
                    "roe": "ROE",
                    "free_cash_flow": "自由现金流",
                }.get(name, name)
            series[label] = values
            if all(value is None for value in values):
                missing.append(name)
        percent = all(name in RATIO_METRICS for name in selection)
        currency = statements[0].currency
        unit_scale = statements[0].unit_scale
        asset = bundle.company_name or statements[0].company_name or spec.name_hint or spec.symbol
        data_range = f"{periods[0]} 至 {periods[-1]}"
        subtitle = (
            f"{data_range} · {currency} · 金额单位 scale={unit_scale:g} · "
            f"来源 {bundle.source} · 数据状态 {bundle.data_status.value}"
        )
        notes: list[str] = []
        if missing:
            notes.append(f"以下指标在所有报告期都缺失，未绘制：{', '.join(missing)}。")
        try:
            fig = figs.fundamental_figure(
                periods,
                series,
                title=f"{asset}（{spec.symbol}）财务趋势",
                subtitle=subtitle,
                y_title="百分比（%）" if percent else f"金额（{currency}）",
                percent=percent,
                as_line=percent,
                text_labels=False,
            )
        except figs.ChartDataError as exc:
            return self._unavailable(spec, "fundamental", str(exc), source=bundle.source)
        theme.add_source_note(
            fig,
            source=bundle.source,
            data_timestamp=statements[0].period_end.isoformat(),
            generated_at=bundle.retrieved_at,
            data_status=bundle.data_status.value,
            extra=f"报告期 {statements[0].fiscal_period} · 披露日 {statements[0].filing_date or '未提供'}",
        )
        return self._emit(
            fig,
            spec=spec,
            chart_type="fundamental",
            category="fundamental",
            asset=asset,
            data_range=data_range,
            source=bundle.source,
            retrieved_at=bundle.retrieved_at,
            data_timestamp=statements[0].period_end.isoformat(),
            currency=currency,
            points=len(periods),
            period=period_type,
            extra_notes=notes,
            fiscal_period=statements[0].fiscal_period,
            reporting_unit=f"scale={unit_scale:g}",
        )

    # -- comparisons ---------------------------------------------------------
    def _price_comparison(
        self, specs: Sequence[Any], period: str, normalize: bool
    ) -> ChartResult:
        entries: list[dict[str, Any]] = []
        currencies: set[str] = set()
        sources: set[str] = set()
        notes: list[str] = []
        first_dates: list[str] = []
        last_dates: list[str] = []
        retrieved: list[str] = []
        first = specs[0]
        for spec in specs:
            history = self.market.get_historical_data(spec.symbol, period=period, interval="1d")
            if not history.bars:
                notes.append(f"{spec.symbol} 没有取到历史行情：{history.error or '数据为空'}")
                continue
            rows = list(history.bars)
            asset = history.name or spec.name_hint or spec.symbol
            entries.append(
                {
                    "name": f"{asset}（{spec.symbol}）",
                    "dates": [str(bar.date) for bar in rows],
                    "values": [bar.close for bar in rows],
                }
            )
            currencies.add((history.currency or spec.currency or "UNKNOWN").upper())
            sources.add(history.source)
            first_dates.append(str(rows[0].date))
            last_dates.append(str(rows[-1].date))
            retrieved.append(history.retrieved_at)
        if not entries:
            return self._unavailable(
                first, "comparison", "所有标的都没有取到历史行情，无法生成对比图。"
            )
        if len(currencies) > 1 and not normalize:
            return self._unavailable(
                first,
                "comparison",
                f"这些标的的币种不同（{', '.join(sorted(currencies))}），"
                "不能把不同币种的价格放在同一纵轴上；请使用 normalization=base_100 做相对表现对比。",
            )
        title = "多标的相对表现（起点 = 100）" if normalize else "多标的价格对比"
        subtitle = (
            f"来源 {', '.join(sorted(sources))} · 币种 {', '.join(sorted(currencies))}"
            + (" · 已按各标的首个可用收盘价归一化" if normalize else "")
        )
        try:
            fig = figs.price_figure(
                entries,
                title=title,
                subtitle=subtitle,
                y_title="相对价格（基准=100）" if normalize else "收盘价",
                normalize=normalize,
            )
        except figs.ChartDataError as exc:
            return self._unavailable(first, "comparison", str(exc))
        theme.add_source_note(
            fig,
            source=", ".join(sorted(sources)),
            data_timestamp=max(last_dates) if last_dates else None,
            generated_at=max(retrieved) if retrieved else None,
            data_status="delayed",
            extra="归一化只消除价格水平差异，不代表汇率调整后的投资回报",
        )
        return self._emit(
            fig,
            spec=first,
            chart_type="comparison",
            category="stock",
            asset=" vs ".join(spec.symbol for spec in specs),
            data_range=(
                f"{min(first_dates)} 至 {max(last_dates)}"
                if first_dates and last_dates
                else None
            ),
            source=", ".join(sorted(sources)),
            retrieved_at=max(retrieved) if retrieved else None,
            data_timestamp=max(last_dates) if last_dates else None,
            currency=", ".join(sorted(currencies)),
            points=max(len(entry["dates"]) for entry in entries),
            period=period,
            interval="1d",
            extra_notes=notes,
        )

    def _fundamental_comparison(
        self, specs: Sequence[Any], metric: str, period_type: str
    ) -> ChartResult:
        names = [spec.symbol for spec in specs]
        try:
            result = self.fundamental.compare(
                ",".join(names), metrics=[metric], period_type=period_type
            )
        except InputError as exc:
            return self._unavailable(specs[0], "comparison", str(exc))
        rows = [row for row in result.rows if row.metric == metric]
        if not rows:
            return self._unavailable(
                specs[0], "comparison", result.error or "没有可比较的财务指标数据。"
            )
        row = rows[0]
        sources = sorted({item.source for item in result.provenance if item.source})
        periods = sorted({item.reporting_period for item in result.provenance if item.reporting_period})
        retrieved = max(
            (item.retrieved_at for item in result.provenance if item.retrieved_at),
            default=None,
        )
        stamps = sorted(
            {item.data_timestamp for item in result.provenance if item.data_timestamp}
        )
        categories: list[str] = []
        values: list[float | None] = []
        currencies: set[str] = set()
        for item in row.values:
            categories.append(item.company_name or item.symbol)
            values.append(item.value)
            currencies.add((item.currency or "UNKNOWN").upper())
        notes = list(result.comparability_notes)
        notes.extend(result.warnings)
        if not any(value is not None for value in values):
            return self._unavailable(
                specs[0], "comparison", "所选指标在所有公司上都不可用，无法生成对比图。"
            )
        if metric in AMOUNT_METRICS and len(currencies) > 1:
            return self._unavailable(
                specs[0],
                "comparison",
                f"金额指标不能跨币种直接对比（{', '.join(sorted(currencies))}）；"
                "项目没有可靠的汇率换算来源，请改用毛利率、净利率、ROE 等比例指标。",
            )
        try:
            fig = figs.comparison_figure(
                categories,
                {row.label: values},
                title=f"公司对比：{row.label}",
                subtitle=f"{'、'.join(periods)} · 来源 {', '.join(sources)}",
                y_title="百分比（%）" if metric in RATIO_METRICS else f"金额（{', '.join(sorted(currencies))}）",
                percent=metric in RATIO_METRICS,
            )
        except figs.ChartDataError as exc:
            return self._unavailable(specs[0], "comparison", str(exc))
        theme.add_source_note(
            fig,
            source=", ".join(sources),
            data_timestamp=stamps[-1] if stamps else None,
            generated_at=retrieved,
            data_status=result.data_status.value,
            extra="; ".join(notes[:2]) if notes else None,
        )
        return self._emit(
            fig,
            spec=specs[0],
            chart_type="comparison",
            category="fundamental",
            asset=" vs ".join(categories),
            data_range="、".join(periods) if periods else None,
            source=", ".join(sources),
            retrieved_at=retrieved,
            data_timestamp=stamps[-1] if stamps else None,
            currency=", ".join(sorted(currencies)),
            points=len(categories),
            period=period_type,
            extra_notes=notes,
        )

    # -- output helpers ------------------------------------------------------
    def _emit(
        self,
        fig: go.Figure,
        *,
        spec: Any,
        chart_type: str,
        category: str,
        asset: str,
        data_range: str | None,
        source: str,
        retrieved_at: str | None,
        data_timestamp: str | None,
        currency: str | None,
        points: int,
        period: str | None = None,
        interval: str | None = None,
        extra_notes: Sequence[str] = (),
        fiscal_period: str | None = None,
        reporting_unit: str | None = None,
    ) -> ChartResult:
        generated_at = _now()
        stem = f"{_safe(spec.symbol)}_{chart_type}_{_stamp()}"
        folder = self._outputs / "charts" / category
        notes = list(extra_notes)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ChartResult(
                chart_name=f"{asset} {chart_type}",
                symbol=spec.symbol,
                asset_name=asset,
                chart_type=chart_type,
                status="unavailable",
                generated_at=generated_at,
                source=source,
                error=f"无法创建图表输出目录：{exc}",
                notes=notes,
            )
        html_path = _unique(folder / f"{stem}.html")
        try:
            fig.write_html(
                str(html_path),
                include_plotlyjs=True,
                full_html=True,
                config={
                    "displaylogo": False,
                    "responsive": True,
                    "scrollZoom": True,
                },
            )
        except OSError as exc:
            return ChartResult(
                chart_name=f"{asset} {chart_type}",
                symbol=spec.symbol,
                asset_name=asset,
                chart_type=chart_type,
                status="unavailable",
                generated_at=generated_at,
                source=source,
                error=f"写入 HTML 失败：{exc}",
                notes=notes,
            )
        png_path: str | None = None
        if self._settings.enable_png_export:
            candidate = _unique(folder / f"{stem}.png")
            try:
                fig.write_image(
                    str(candidate),
                    width=int(self._settings.chart_width),
                    height=int(self._settings.chart_height),
                    scale=float(self._settings.chart_scale),
                )
                png_path = str(candidate)
            except Exception as exc:  # noqa: BLE001 - PNG is auxiliary
                notes.append(
                    f"PNG 导出失败（HTML 不受影响）：{type(exc).__name__}: {exc}"
                )
        json_path: str | None = None
        try:
            candidate_json = _unique(folder / f"{stem}.json")
            candidate_json.write_text(fig.to_json(), encoding="utf-8")
            json_path = str(candidate_json)
        except OSError as exc:
            notes.append(f"图表 JSON 写出失败：{exc}")
        metadata = ChartMetadata(
            symbol=spec.symbol,
            asset_name=asset,
            chart_type=chart_type,
            period=period,
            interval=interval,
            currency=currency,
            source=source,
            retrieved_at=retrieved_at,
            data_timestamp=data_timestamp,
            data_range=data_range,
            generated_at=generated_at,
            fiscal_period=fiscal_period,
            reporting_currency=currency if fiscal_period else None,
            reporting_unit=reporting_unit,
            points=points,
            notes=notes,
        )
        return ChartResult(
            chart_name=f"{asset} {chart_type}",
            symbol=spec.symbol,
            asset_name=asset,
            chart_type=chart_type,
            status="ok" if not notes else "partial",
            data_range=data_range,
            source=source,
            generated_at=generated_at,
            html_path=str(html_path),
            png_path=png_path,
            json_path=json_path,
            metadata=metadata,
            notes=notes,
        )

    def _unavailable(
        self, spec: Any, chart_type: str, error: str, *, source: str = "unavailable"
    ) -> ChartResult:
        return ChartResult(
            chart_name=f"{spec.symbol} {chart_type}",
            symbol=spec.symbol,
            asset_name=spec.name_hint,
            chart_type=chart_type,
            status="unavailable",
            generated_at=_now(),
            source=source,
            error=error,
            notes=["没有生成任何图表文件，以免用空图或占位数据误导。"],
        )
