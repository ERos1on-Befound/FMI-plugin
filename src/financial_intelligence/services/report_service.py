"""Report generation service (Phase 3).

Reports are assembled from data that the Phase 2 services already fetched and
computed: the module never invents numbers, never fills a missing value with
zero, and always states the reporting period, the source and the limitations.

Each report is written twice — Markdown (primary, diff friendly) and a
self-contained HTML document (readable in any browser) — under
``outputs/reports``.  Charts, when requested, are produced by
:class:`financial_intelligence.services.chart_service.ChartService` and are
referenced by relative path so the report folder stays portable.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import Settings, default_outputs_dir, load_settings
from ..models.chart_result import (
    ReportResult,
    ReportTemplateInfo,
    ReportTemplatesResult,
)
from ..utils import InputError, map_symbol
from ..visualization import markdown as md

REPORT_TYPES: tuple[str, ...] = ("fundamental", "technical", "comprehensive", "comparison")
OUTPUT_FORMATS: tuple[str, ...] = ("markdown", "html")

_RATIO_METRICS = frozenset(
    {"gross_margin", "operating_margin", "net_margin", "roe", "roa", "debt_ratio"}
)

_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "fundamental",
        "label": "基本面分析报告",
        "report_type": "fundamental",
        "description": "基于最近若干财务报告期，汇总公司概况、财务概览、盈利、偿债、运营、成长、现金流与估值。",
        "required_data": ["公司代码", "财务报表（利润表/资产负债表/现金流量表）", "可选：最新股价（用于估值）"],
        "output_formats": ["markdown", "html"],
        "sections": [
            "公司概况",
            "财务数据概览",
            "盈利能力",
            "偿债能力",
            "运营效率",
            "成长能力",
            "现金流",
            "估值指标",
            "主要观察点",
            "数据来源与免责声明",
        ],
    },
    {
        "name": "technical",
        "label": "技术面分析报告",
        "report_type": "technical",
        "description": "基于历史 OHLCV 与已计算指标（MA/EMA/RSI/MACD/Bollinger/ATR）描述当前与历史技术状态。",
        "required_data": ["公司代码", "历史行情（OHLCV）"],
        "output_formats": ["markdown", "html"],
        "sections": [
            "历史行情概览",
            "均线系统",
            "RSI",
            "MACD",
            "Bollinger",
            "ATR",
            "技术状态总结",
            "图表",
            "数据来源与免责声明",
        ],
    },
    {
        "name": "comprehensive",
        "label": "综合分析报告",
        "report_type": "comprehensive",
        "description": "同时组织基本面与技术面内容；只有两类数据都可用时才生成完整报告，否则明确标注缺失部分。",
        "required_data": ["公司代码", "财务报表", "历史行情"],
        "output_formats": ["markdown", "html"],
        "sections": [
            "公司概况",
            "财务数据概览",
            "盈利能力",
            "偿债能力",
            "运营效率",
            "成长能力",
            "现金流",
            "估值指标",
            "历史行情概览",
            "技术指标",
            "技术状态总结",
            "数据来源与免责声明",
        ],
    },
    {
        "name": "comparison",
        "label": "公司对比报告",
        "report_type": "comparison",
        "description": "比较两家以上公司在同一报告期的财务指标，先检查期间/币种/单位可比性，不做排名。",
        "required_data": ["至少两个公司代码", "各公司财务报表"],
        "output_formats": ["markdown", "html"],
        "sections": [
            "公司基本信息",
            "可比性说明",
            "指标对比表",
            "主要差异",
            "数据来源与免责声明",
        ],
    },
)

_CHECK = "—"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value))


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"无法为 {path.name} 找到可用的文件名")


def _money(value: float | None) -> str:
    if value is None:
        return _CHECK
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _CHECK
    if number != number:
        return _CHECK
    return f"{number:,.0f}"


def _pct(value: float | None) -> str:
    if value is None:
        return _CHECK
    try:
        return f"{float(value):,.2f}%"
    except (TypeError, ValueError):
        return _CHECK


def _num(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return _CHECK
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return _CHECK


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def _bullet_list(items: Sequence[str]) -> str:
    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def _relative(target: str | Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(Path(target), start)).as_posix()
    except ValueError:  # different drives on Windows
        return Path(target).as_posix()


class ReportService:
    """Build Markdown/HTML research reports from real tool output."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fundamentals: Any | None = None,
        technicals: Any | None = None,
        charts: Any | None = None,
        market: Any | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._fundamentals = fundamentals
        self._technicals = technicals
        self._charts = charts
        self._market = market
        base = (self._settings.outputs_dir or "").strip()
        self._outputs = Path(base).expanduser() if base else default_outputs_dir()

    # -- lazy collaborators --------------------------------------------------
    @property
    def fundamentals(self) -> Any:
        if self._fundamentals is None:
            from .fundamental_service import FundamentalService

            self._fundamentals = FundamentalService(self._settings, market=self._market)
        return self._fundamentals

    @property
    def technicals(self) -> Any:
        if self._technicals is None:
            from .technical_service import TechnicalService

            self._technicals = TechnicalService(self._settings, market=self._market)
        return self._technicals

    @property
    def charts(self) -> Any:
        if self._charts is None:
            from .chart_service import ChartService

            self._charts = ChartService(
                self._settings,
                market=self._market,
                technical=self._technicals,
                fundamental=self._fundamentals,
            )
        return self._charts

    @property
    def outputs_dir(self) -> Path:
        return self._outputs

    @property
    def market(self) -> Any:
        if self._market is None:
            from .market_service import MarketService

            self._market = MarketService(self._settings)
        return self._market

    # -- catalogue -----------------------------------------------------------
    def templates(self) -> ReportTemplatesResult:
        return ReportTemplatesResult(
            templates=[ReportTemplateInfo(**item) for item in _TEMPLATES],
            output_formats=list(OUTPUT_FORMATS),
            notes=[
                "Markdown 为主要格式，HTML 为附带的可视化版本；两者内容一致。",
                "PDF 不提供：项目不为此引入额外的重量级依赖（详见 README 的限制说明）。",
                "报告中的数字全部来自真实工具返回；缺失数据会标注为“—”，不会补 0。",
            ],
            generated_at=_now(),
        )

    # -- entry point ---------------------------------------------------------
    def generate(
        self,
        symbol: str,
        *,
        market: str | None = None,
        report_type: str = "fundamental",
        period: str = "6mo",
        interval: str = "1d",
        output_format: str = "markdown",
        include_charts: bool = True,
        include_raw_data: bool = False,
        period_type: str = "annual",
        periods: int | None = None,
        symbols: Any = None,
    ) -> ReportResult:
        kind = str(report_type or "fundamental").strip().lower()
        if kind not in REPORT_TYPES:
            raise InputError(
                f"不支持的报告类型 “{report_type}”；可用取值：{', '.join(REPORT_TYPES)}。"
            )
        fmt = str(output_format or "markdown").strip().lower()
        if fmt not in OUTPUT_FORMATS:
            raise InputError(
                f"不支持的输出格式 “{output_format}”；可用取值：{', '.join(OUTPUT_FORMATS)}。"
            )
        if kind == "comparison":
            names = self._split_symbols(symbols or symbol)
            return self._comparison_report(names, market=market, period_type=period_type, include_charts=include_charts)
        spec = map_symbol(symbol, market=market)
        if kind == "fundamental":
            body = self._fundamental_body(spec, period_type=period_type, periods=periods, include_charts=include_charts)
        elif kind == "technical":
            body = self._technical_body(spec, period=period, interval=interval, include_charts=include_charts)
        else:
            body = self._comprehensive_body(
                spec,
                period=period,
                interval=interval,
                period_type=period_type,
                periods=periods,
                include_charts=include_charts,
            )
        return self._publish(
            spec=spec,
            report_type=kind,
            title=body["title"],
            markdown_text=body["markdown"],
            sources=body["sources"],
            missing=body["missing"],
            charts=body["charts"],
            data_period=body["data_period"],
            status=body["status"],
            notes=body["notes"],
            include_raw_data=include_raw_data,
            output_format=fmt,
        )

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _split_symbols(value: Any) -> list[str]:
        if isinstance(value, str):
            items = [item.strip() for item in value.replace("，", ",").split(",")]
        elif isinstance(value, Iterable):
            items = [str(item).strip() for item in value]
        else:
            items = []
        names = [item for item in items if item]
        if len(names) < 2:
            raise InputError("对比报告至少需要两个公司代码，例如 “AAPL,MSFT”。")
        return names

    def _chart_block(self, chart: Any, reports_dir: Path) -> tuple[str, list[str]]:
        if chart is None or chart.status == "unavailable" or not chart.html_path:
            return "", []
        lines: list[str] = []
        files: list[str] = []
        if getattr(chart, "png_path", None):
            rel = _relative(chart.png_path, reports_dir)
            lines.append(f"![{chart.chart_name}]({rel})")
            files.append(chart.png_path)
        rel_html = _relative(chart.html_path, reports_dir)
        lines.append(f"[交互式图表（HTML，可缩放与悬停）：{Path(chart.html_path).name}]({rel_html})")
        files.append(chart.html_path)
        return "\n\n".join(lines), files

    # -- fundamental ---------------------------------------------------------
    def _fundamental_body(
        self, spec: Any, *, period_type: str, periods: int | None, include_charts: bool
    ) -> dict[str, Any]:
        bundle = self.fundamentals.get_financials(
            spec.symbol, period_type=period_type, limit=(periods or 3) + 2
        )
        analysis = self.fundamentals.analyze(
            spec.symbol, scope="full", period_type=period_type, periods=periods
        )
        if not analysis.dimensions:
            return {
                "title": f"{spec.symbol} 基本面分析报告",
                "markdown": f"# {spec.symbol} 基本面分析报告\n\n- 生成时间：{_now()}\n\n"
                f"未生成完整报告：{analysis.error or bundle.error or '没有取到可用的财务数据。'}",
                "sources": [],
                "missing": [analysis.error or bundle.error or "财务数据不可用"],
                "charts": [],
                "data_period": None,
                "status": "unavailable",
                "notes": [],
            }
        statements = list(bundle.statements)
        latest = statements[0] if statements else None
        company = analysis.company_name or (latest.company_name if latest else None) or spec.symbol
        market_value = getattr(analysis.market, "value", str(analysis.market))
        currency = analysis.currency or (latest.currency if latest else "UNKNOWN")
        unit_scale = latest.unit_scale if latest else 1.0
        fiscal = analysis.fiscal_period or (latest.fiscal_period if latest else None)
        filing = analysis.filing_date or (latest.filing_date if latest else None)
        data_period = (
            f"{fiscal}（截止 {latest.period_end.isoformat()}）" if latest and latest.period_end else fiscal
        )
        charts: list[str] = []
        chart_md = ""
        if include_charts:
            chart = self.charts.generate(
                spec.symbol,
                chart_type="fundamental",
                indicators="revenue,net_income,gross_margin,roe",
                period_type=period_type,
            )
            chart_md, charts = self._chart_block(chart, self._outputs / "reports")

        tables = analysis.metric_tables or {}
        parts: list[str] = [
            f"# {company}（{spec.symbol}）基本面分析报告",
            "",
            f"> 生成时间：{_now()} · 市场：{market_value} · 财务来源：{bundle.source or analysis.provenance.source}",
            f"> 财务数据截止：{data_period or _CHECK} · 披露日：{filing or '未提供'} · "
            f"币种：{currency} · 金额单位 scale={unit_scale:g}",
            "",
            "## 一、公司概况",
            "",
            _table(
                ["项目", "取值"],
                [
                    ["公司名称", company],
                    ["代码", spec.symbol],
                    ["市场", market_value],
                    ["报告期类型", "年度（annual）" if period_type == "annual" else "季度（quarterly）"],
                    ["使用报告期", "、".join(item.fiscal_period for item in statements) or _CHECK],
                    ["币种 / 单位", f"{currency} / scale={unit_scale:g}"],
                    ["行业分类", "数据源未提供可靠行业分类，报告中不做行业对比"],
                ],
            ),
            "",
            "## 二、财务数据概览",
            "",
        ]
        overview_rows: list[list[str]] = []
        labels = [
            ("营业收入", "income_statement", "revenue"),
            ("毛利润", "income_statement", "gross_profit"),
            ("营业利润", "income_statement", "operating_income"),
            ("净利润", "income_statement", "net_income"),
            ("总资产", "balance_sheet", "total_assets"),
            ("总负债", "balance_sheet", "total_liabilities"),
            ("股东权益", "balance_sheet", "total_equity"),
            ("经营现金流", "cash_flow_statement", "operating_cash_flow"),
            ("自由现金流", "cash_flow_statement", "free_cash_flow"),
        ]
        for label, kind, field in labels:
            row = [label]
            for statement in statements:
                payload = getattr(statement, kind) or {}
                value = payload.get(field)
                if field == "free_cash_flow" and value is None:
                    operating = payload.get("operating_cash_flow")
                    capex = payload.get("capital_expenditure")
                    if operating is not None and capex is not None:
                        value = operating - abs(capex)
                row.append(_money(value))
            overview_rows.append(row)
        parts.append(_table(["指标", *[item.fiscal_period for item in statements]], overview_rows))
        parts.append("")

        sections = [
            ("三、盈利能力", "profitability", "毛利率、营业利润率、净利率、ROE、ROA。"),
            ("四、偿债能力", "solvency", "资产负债率、流动比率、速动比率、利息保障倍数。"),
            ("五、运营效率", "efficiency", "总资产周转率、应收账款周转率、存货周转率，均使用平均余额口径。"),
            ("六、成长能力", "growth", "营收、净利润与 EPS 的同比增长（期间匹配）。"),
            ("七、现金流", "cash_flow", "经营现金流、自由现金流，以及经营现金流与净利润的关系。"),
        ]
        for heading, key, blurb in sections:
            parts.append(f"## {heading}")
            parts.append("")
            parts.append(blurb)
            parts.append("")
            table = tables.get(key) or []
            if table:
                parts.append(_table(table[0], table[1:]))
            else:
                parts.append("本维度没有可用指标（数据不足）。")
            parts.append("")

        parts.append("## 八、估值指标")
        parts.append("")
        if analysis.valuation:
            valuation_rows = [
                [
                    item.label,
                    _num(item.value, 2, "x" if item.unit == "x" else ("%" if item.unit == "%" else "")),
                    item.period or _CHECK,
                    item.calculation_status,
                    item.explanation or "",
                ]
                for item in analysis.valuation
            ]
            parts.append(_table(["指标", "数值", "对应期间", "状态", "说明"], valuation_rows))
        else:
            parts.append("估值指标未生成，原因见下方数据限制（通常为缺少最新股价或行情币种与财报币种不一致）。")
        parts.append("")

        parts.append("## 九、主要观察点")
        parts.append("")
        observations = list(analysis.observations or [])
        parts.append(_bullet_list(observations[:12]))
        if len(observations) > 12:
            parts.append(f"\n（其余 {len(observations) - 12} 条观察见分析的 observations 字段。）")
        if chart_md:
            parts.append("")
            parts.append("### 财务趋势图")
            parts.append("")
            parts.append(chart_md)
        parts.append("")

        parts.append("## 十、数据来源与免责声明")
        parts.append("")
        limitations = list(analysis.limitations or [])
        parts.append(
            _bullet_list(
                [
                    f"财务数据来源：{bundle.source or analysis.provenance.source}；"
                    f"获取时间：{analysis.provenance.retrieved_at}",
                    f"报告期：{data_period or _CHECK}；披露日：{filing or '数据源未提供'}",
                    f"计算口径：见 references/formula-reference.md，公式版本 {analysis.provenance.formula_version}",
                    *limitations,
                    "本报告仅用于研究与学习，不构成任何投资、交易或税务建议。",
                ]
            )
        )
        parts.append("")
        missing = list((latest.missing_fields if latest else []) or []) + [
            item for item in (analysis.limitations or []) if "missing" in item.lower()
        ]
        return {
            "title": f"{company}（{spec.symbol}）基本面分析报告",
            "markdown": "\n".join(parts),
            "sources": [bundle.source or analysis.provenance.source],
            "missing": missing,
            "charts": charts,
            "data_period": data_period,
            "status": "ok" if not missing else "partial",
            "notes": ["金额单位为报告币种原始单位，缺失值以“—”标注，未以 0 替代。"],
            "extra": None,
        }

    # -- technical -----------------------------------------------------------
    def _technical_body(
        self, spec: Any, *, period: str, interval: str, include_charts: bool
    ) -> dict[str, Any]:
        result = self.technicals.get_indicators(spec.symbol, period=period, interval=interval)
        company = result.company_name or spec.name_hint or spec.symbol
        if not result.indicators:
            return {
                "title": f"{spec.symbol} 技术面分析报告",
                "markdown": f"# {spec.symbol} 技术面分析报告\n\n- 生成时间：{_now()}\n\n"
                f"未生成报告：{result.error or '没有取到可用的历史行情或指标。'}",
                "sources": [],
                "missing": [result.error or "历史行情不可用"],
                "charts": [],
                "data_period": None,
                "status": "unavailable",
                "notes": [],
            }
        series = {item.name: item for item in result.indicators}
        window = f"{result.window_start} 至 {result.window_end}"
        currency = result.currency or "UNKNOWN"
        # The indicator payload only carries counts, so the OHLCV window itself is
        # re-read from the market service (its response is cached, so this does not
        # issue a second network request for the same window).
        history = self.market.get_historical_data(spec.symbol, period=period, interval=interval)
        bars = list(history.bars)
        closes = [bar.close for bar in bars if bar.close is not None]
        highs = [bar.high for bar in bars if bar.high is not None]
        lows = [bar.low for bar in bars if bar.low is not None]
        latest_close = closes[-1] if closes else None
        window_high = max(highs) if highs else None
        window_low = min(lows) if lows else None
        window_return = (
            (closes[-1] - closes[0]) / closes[0] * 100.0 if len(closes) >= 2 and closes[0] else None
        )
        volumes = [bar.volume for bar in bars if bar.volume is not None]
        volume_note = (
            f"区间成交量可用（{len(volumes)} 个交易日），"
            f"均值约 {sum(volumes) / len(volumes):,.0f}"
            if volumes
            else "数据源未提供成交量，本次不做成交量分析（也不会用 0 代替）"
        )
        charts: list[str] = []
        chart_md = ""
        if include_charts:
            chart = self.charts.generate(
                spec.symbol,
                chart_type="technical",
                period=period,
                interval=interval,
                indicators="ma,macd,rsi,bollinger,atr",
            )
            chart_md, charts = self._chart_block(chart, self._outputs / "reports")

        parts: list[str] = [
            f"# {company}（{spec.symbol}）技术面分析报告",
            "",
            f"> 生成时间：{_now()} · 市场：{getattr(result.market, 'value', result.market)} · "
            f"数据来源：{result.provenance.source}",
            f"> 分析区间：{window}（{result.period} / {result.interval}，共 {len(bars)} 根） · "
            f"最新行情日期：{result.window_end} · 数据获取时间：{result.provenance.retrieved_at}",
            "",
            "## 一、历史行情概览",
            "",
            _table(
                ["项目", "取值"],
                [
                    ["最新收盘价", f"{_num(latest_close, 2)} {currency}"],
                    ["区间最高价", f"{_num(window_high, 2)} {currency}"],
                    ["区间最低价", f"{_num(window_low, 2)} {currency}"],
                    ["区间涨跌幅", _pct(window_return)],
                    ["成交量", volume_note],
                ],
            ),
            "",
        ]

        def series_row(name: str, label: str, digits: int = 2) -> list[str]:
            item = series.get(name)
            if item is None or item.value is None:
                return [label, _CHECK, _CHECK, item.notes[0] if item and item.notes else "数据不足"]
            return [label, _num(item.value, digits), item.date or _CHECK, item.explanation or ""]

        parts.append("## 二、均线系统")
        parts.append("")
        parts.append(_table(["指标", "当前值", "对应日期", "说明"], [series_row(f"sma_{w}", f"MA{w}") for w in (5, 10, 20, 60)] + [series_row("ema_12", "EMA12"), series_row("ema_26", "EMA26")]))
        parts.append("")
        parts.append("## 三、RSI")
        parts.append("")
        parts.append(_table(["指标", "当前值", "对应日期", "说明"], [series_row("rsi_14", "RSI14", 2)]))
        parts.append("")
        parts.append("## 四、MACD")
        parts.append("")
        parts.append(_table(["指标", "当前值", "对应日期", "说明"], [series_row("macd", "DIF", 4), series_row("macd_signal", "DEA", 4), series_row("macd_histogram", "Histogram（DIF−DEA）", 4)]))
        parts.append("")
        parts.append("## 五、Bollinger")
        parts.append("")
        parts.append(_table(["指标", "当前值", "对应日期", "说明"], [series_row("bollinger_upper", "上轨", 2), series_row("bollinger_middle", "中轨（SMA20）", 2), series_row("bollinger_lower", "下轨", 2), series_row("bollinger_bandwidth", "带宽（%）", 2), series_row("bollinger_percent_b", "%B（%）", 2)]))
        parts.append("")
        parts.append("## 六、ATR")
        parts.append("")
        parts.append(_table(["指标", "当前值", "对应日期", "说明"], [series_row("atr_14", "ATR14", 4)]))
        parts.append("")
        parts.append("## 七、技术状态总结")
        parts.append("")
        parts.append(_bullet_list(list(result.summary or [])))
        parts.append("")
        parts.append("## 八、图表")
        parts.append("")
        parts.append(chart_md or "本次未生成图表（include_charts=false 或图表数据不足）。")
        parts.append("")
        parts.append("## 九、数据来源与免责声明")
        parts.append("")
        parts.append(
            _bullet_list(
                [
                    f"行情来源：{result.provenance.source}；获取时间：{result.provenance.retrieved_at}；"
                    f"数据状态：{result.provenance.data_status.value if hasattr(result.provenance.data_status, 'value') else result.provenance.data_status}",
                    f"指标口径：{result.provenance.calculation_method or 'analysis.technical'}；"
                    f"公式版本 {result.provenance.formula_version}",
                    *(result.notes or []),
                    "以上全部为历史数据的统计描述，不预测未来走势，不构成买卖建议。",
                ]
            )
        )
        parts.append("")
        missing = [item for item in (result.notes or []) if "不足" in item or "缺失" in item]
        return {
            "title": f"{company}（{spec.symbol}）技术面分析报告",
            "markdown": "\n".join(parts),
            "sources": [result.provenance.source],
            "missing": missing,
            "charts": charts,
            "data_period": window,
            "status": "ok" if not missing else "partial",
            "notes": ["技术指标只描述历史状态，报告不给出交易信号。"],
            "extra": None,
        }

    # -- comprehensive -------------------------------------------------------
    def _comprehensive_body(
        self,
        spec: Any,
        *,
        period: str,
        interval: str,
        period_type: str,
        periods: int | None,
        include_charts: bool,
    ) -> dict[str, Any]:
        fundamental = self._fundamental_body(
            spec, period_type=period_type, periods=periods, include_charts=include_charts
        )
        technical = self._technical_body(
            spec, period=period, interval=interval, include_charts=include_charts
        )
        has_fundamental = fundamental["status"] != "unavailable"
        has_technical = technical["status"] != "unavailable"
        company = spec.name_hint or spec.symbol
        title = f"{company}（{spec.symbol}）综合分析报告"
        head = [
            f"# {title}",
            "",
            f"> 生成时间：{_now()}",
            f"> 基本面数据：{fundamental['data_period'] or '不可用'} · "
            f"技术面区间：{technical['data_period'] or '不可用'}",
            "",
        ]
        if not (has_fundamental and has_technical):
            missing_side = []
            if not has_fundamental:
                missing_side.append("基本面")
            if not has_technical:
                missing_side.append("技术面")
            head.append(
                f"> ⚠️ 本次只生成部分报告：{'、'.join(missing_side)}数据不可用，"
                "相关章节以说明替代，没有用模拟数据填充。"
            )
            head.append("")
        body_parts = []
        if has_fundamental:
            body_parts.append(_strip_h1(fundamental["markdown"]))
        else:
            body_parts.append("## 基本面部分\n\n数据不可用：" + (fundamental.get("missing") or ["未知原因"])[0])
        if has_technical:
            body_parts.append(_strip_h1(technical["markdown"]))
        else:
            body_parts.append("## 技术面部分\n\n数据不可用：" + (technical.get("missing") or ["未知原因"])[0])
        markdown_text = "\n".join(head) + "\n" + "\n\n---\n\n".join(body_parts)
        missing = list(fundamental["missing"]) + list(technical["missing"])
        return {
            "title": title,
            "markdown": markdown_text,
            "sources": sorted({*fundamental["sources"], *technical["sources"]}),
            "missing": missing,
            "charts": [*fundamental["charts"], *technical["charts"]],
            "data_period": fundamental["data_period"],
            "status": "ok" if has_fundamental and has_technical else "partial",
            "notes": ["综合报告的两部分都来自各自的真实工具结果，没有跨模块复制数字。"],
            "extra": None,
        }

    # -- comparison ----------------------------------------------------------
    def _comparison_report(
        self, names: Sequence[str], *, market: str | None, period_type: str, include_charts: bool
    ) -> ReportResult:
        specs = [map_symbol(name, market=market) for name in names]
        result = self.fundamentals.compare(
            ",".join(spec.symbol for spec in specs), period_type=period_type
        )
        if not result.rows:
            return ReportResult(
                report_name="公司对比报告",
                symbol=", ".join(names),
                report_type="comparison",
                output_format="markdown",
                status="unavailable",
                generated_at=_now(),
                error=result.error or "没有可比较的财务数据。",
                missing_data=[result.error or "财务数据不可用"],
            )
        charts: list[str] = []
        chart_md = ""
        if include_charts:
            chart = self.charts.comparison(
                ",".join(spec.symbol for spec in specs), metric="gross_margin"
            )
            chart_md, charts = self._chart_block(chart, self._outputs / "reports")
        headers = ["指标", *[value.company_name or value.symbol for value in result.rows[0].values]]
        rows = []
        for row in result.rows:
            cells = [row.label + (f"（{row.unit}）" if row.unit and row.unit not in {"ratio"} else "")]
            for value in row.values:
                if value.value is None:
                    cells.append(_CHECK)
                elif row.unit == "%":
                    cells.append(_pct(value.value))
                elif row.unit == "x":
                    cells.append(_num(value.value, 2, "x"))
                else:
                    cells.append(_num(value.value, 2))
            rows.append(cells)
        info = [
            [
                value.symbol,
                value.company_name or value.symbol,
                value.fiscal_period or _CHECK,
                value.filing_date or "未提供",
                value.currency or "UNKNOWN",
                value.unit or _CHECK,
            ]
            for value in result.rows[0].values
        ]
        parts: list[str] = [
            "# 公司对比报告",
            "",
            f"> 生成时间：{_now()} · 报告期类型：{period_type}",
            "",
            "## 一、公司基本信息",
            "",
            _table(["公司", "名称", "报告期", "披露日", "币种", "单位"], info),
            "",
            "## 二、可比性说明",
            "",
            _bullet_list(result.comparability_notes or ["所选公司的报告期、币种与单位一致。"]),
            "",
            "## 三、指标对比表",
            "",
            _table(headers, rows),
            "",
        ]
        if chart_md:
            parts.extend(["### 对比图", "", chart_md, ""])
        parts.extend(
            [
                "## 四、主要差异",
                "",
                _bullet_list(result.warnings or ["本次对比未发现需要额外提示的差异。"]),
                "",
                "## 五、数据来源与免责声明",
                "",
                _bullet_list(
                    [
                        f"数据来源：{', '.join(sorted({item.source for item in result.provenance}))}",
                        "对比口径：同一报告期类型、同一币种、同一金额单位；不满足时已在“可比性说明”中标注。",
                        "本报告不做优劣排名，也不构成投资建议。",
                    ]
                ),
                "",
            ]
        )
        markdown_text = "\n".join(parts)
        title = "公司对比报告"
        folder = self._outputs / "reports"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ReportResult(
                report_name=title,
                symbol=", ".join(names),
                report_type="comparison",
                output_format="markdown",
                status="unavailable",
                generated_at=_now(),
                error=f"无法创建报告目录：{exc}",
            )
        stem = f"{_safe('_'.join(spec.symbol for spec in specs))}_comparison_{_stamp()}"
        md_path = _unique(folder / f"{stem}.md")
        html_path = _unique(folder / f"{stem}.html")
        md_path.write_text(markdown_text, encoding="utf-8")
        html_path.write_text(
            md.render_document(markdown_text, title=title, subtitle=f"生成时间：{_now()}"),
            encoding="utf-8",
        )
        return ReportResult(
            report_name=title,
            symbol=", ".join(spec.symbol for spec in specs),
            company_name=None,
            report_type="comparison",
            output_format="markdown",
            status="ok" if all(row.comparable for row in result.rows) else "partial",
            generated_at=_now(),
            data_period="、".join(
                sorted({value.fiscal_period for value in result.rows[0].values if value.fiscal_period})
            ),
            data_sources=sorted({item.source for item in result.provenance}),
            markdown_path=str(md_path),
            html_path=str(html_path),
            charts=charts,
            missing_data=[note for note in result.comparability_notes],
            notes=["同时生成了 Markdown 与 HTML 两个版本。"],
        )

    # -- publish -------------------------------------------------------------
    def _publish(
        self,
        *,
        spec: Any,
        report_type: str,
        title: str,
        markdown_text: str,
        sources: Sequence[str],
        missing: Sequence[str],
        charts: Sequence[str],
        data_period: str | None,
        status: str,
        notes: Sequence[str],
        include_raw_data: bool,
        output_format: str = "markdown",
    ) -> ReportResult:
        folder = self._outputs / "reports"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ReportResult(
                report_name=title,
                symbol=spec.symbol,
                report_type=report_type,
                output_format="markdown",
                status="unavailable",
                generated_at=_now(),
                error=f"无法创建报告目录：{exc}",
            )
        text = markdown_text
        if include_raw_data:
            text += "\n\n## 附：本次使用的原始数据字段说明\n\n"
            text += _bullet_list(
                [
                    "所有数值都来自 MCP 工具返回的结构化字段，字段名与口径见 references/。",
                    "缺失字段以“—”显示；未提供的报告期不会出现在表格中。",
                ]
            )
        stem = f"{_safe(spec.symbol)}_{report_type}_{_stamp()}"
        md_path = _unique(folder / f"{stem}.md")
        html_path = _unique(folder / f"{stem}.html")
        try:
            md_path.write_text(text, encoding="utf-8")
            html_path.write_text(
                md.render_document(text, title=title, subtitle=f"生成时间：{_now()}"),
                encoding="utf-8",
            )
        except OSError as exc:
            return ReportResult(
                report_name=title,
                symbol=spec.symbol,
                report_type=report_type,
                output_format="markdown",
                status="unavailable",
                generated_at=_now(),
                error=f"写入报告失败：{exc}",
            )
        return ReportResult(
            report_name=title,
            symbol=spec.symbol,
            company_name=None,
            report_type=report_type,
            output_format=output_format,
            status="ok" if status == "ok" else "partial",
            generated_at=_now(),
            data_period=data_period,
            data_sources=list(dict.fromkeys(sources)),
            markdown_path=str(md_path),
            html_path=str(html_path),
            charts=list(charts),
            missing_data=list(dict.fromkeys(missing)),
            notes=[*notes, "同时生成了 Markdown 与 HTML 两个版本。"],
        )


def _strip_h1(markdown_text: str) -> str:
    """Drop the leading ``# title`` line so combined reports keep one H1."""
    lines = markdown_text.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).lstrip("\n")
    return markdown_text
