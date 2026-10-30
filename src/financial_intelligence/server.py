"""MCP server exposing the Financial Market Intelligence tool surface.

Nineteen tools are registered here; every one of them returns a typed model
from :mod:`financial_intelligence.schemas` or
:mod:`financial_intelligence.models`, so MCP clients get a real output schema
(not a free-form string).

Run it with::

    financial-intelligence-mcp            # stdio, for MCP hosts
    python -m financial_intelligence.server --transport streamable-http --port 8000

``stdout`` is the MCP wire on stdio transport: logging always goes to stderr.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Annotated, Any

from pydantic import Field

from .config import (
    DISPLAY_NAME,
    SUBTITLE,
    VERSION,
    SUPPORTED_INTERVALS,
    SUPPORTED_PERIODS,
    configure_logging,
    load_settings,
)
from .models.analysis_result import (
    ComparisonResult,
    FormulaResult,
    FundamentalsResult,
    MetricsResult,
    Provenance,
    SupportedIndicatorsResult,
    TechnicalResult,
)
from .models.chart_result import (
    AvailableChartsResult,
    ChartResult,
    ReportResult,
    ReportTemplatesResult,
)
from .models.financial_statements import FinancialStatements
from .schemas import (
    AssetType,
    DataStatus,
    HistoricalData,
    Market,
    MarketOverview,
    Quote,
    ServerStatus,
    SupportedMarkets,
    iso,
    utc_now,
)
from .services import (
    CalculationService,
    ChartService,
    FundamentalService,
    MarketService,
    ReportService,
    TechnicalService,
)
from .utils.symbol_mapper import SYMBOL_HELP
from .utils.validators import InputError

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""\
{DISPLAY_NAME} — {SUBTITLE}

使用顺序建议：
1. 先调用 get_server_status 确认服务可用、看看启用了哪些数据源；
   不确定指标名/公式名时调用 get_supported_indicators。
2. 不确定代码格式时调用 get_supported_markets，或直接参考下列约定：
{SYMBOL_HELP}
3. 单只资产 → get_stock_quote（股票/ETF/指数）或 get_asset_quote（指数/商品等通用）。
4. 历史K线 → get_historical_data（period ∈ {", ".join(SUPPORTED_PERIODS)}；
   interval ∈ {", ".join(SUPPORTED_INTERVALS)}）。
5. 需要横向看盘 → get_market_overview（固定覆盖 A股/港股/美股/大宗商品）。
6. 财务报表 → get_company_financials（annual / quarterly）。
7. 算指标 → calculate_financial_metrics（多指标多期）；自定义口径 → calculate_financial_formula。
8. 技术指标 → get_technical_indicators、analyze_technicals。
9. 结构化分析 → analyze_fundamentals、compare_companies。
10. 出图 → generate_financial_chart（单标的）、generate_comparison_chart（多标的）；
    不确定有哪些图表类型时先调用 get_available_chart_types。
11. 出报告 → generate_analysis_report（fundamental / technical / comprehensive /
    comparison）；想知道报告包含哪些章节时先调用 get_report_templates。
12. 图表与报告都写在 outputs/ 下，返回的 html_path / png_path / markdown_path
    是本次真实生成的文件路径；本机路径不保证对其他机器可访问。

硬性约束（回答用户时必须遵守）：
- 数据全部来自免费公开接口，不能声称是实时行情；yfinance / AKShare / 新浪快照一律按 delayed 处理。
- 必须同时给出「行情时间 timestamp」和「获取时间 retrieved_at」，不能混为一谈。
- 期货代码（GC=F / CL=F / BZ=F）是期货合约参考价，不是现货价格。
- 价格为 null 时就是没有数据，禁止编造或用 0 填充；把 error 字段里的真实原因告诉用户。
- 财报必须区分报告期（period_end）、报告发布日（filing_date）与获取时间（retrieved_at）；
  A股季报是年初至今累计口径，跨境比较前先检查币种与报告期是否一致。
- 指标要么是原始数据、要么是公式计算结果，都要给出公式与来源；数据不足就说 insufficient_data，
  公式不适用就说 not_applicable，禁止拿 0 顶替缺失值。
- 技术指标只描述历史状态，不预测涨跌，不给目标价与买卖信号。
- 图表必须来自本次真实生成的文件，不要编造路径或声称图片已在别处显示；
  报告中缺失的数据以“—”标注，不要用推测值补全。
- 只做数据检索、计算与整理，不构成投资建议。
"""

_service: MarketService | None = None
_fundamental_service: FundamentalService | None = None
_technical_service: TechnicalService | None = None
_calculation_service: CalculationService | None = None
_chart_service: ChartService | None = None
_report_service: ReportService | None = None


def get_service() -> MarketService:
    """Return the process-wide :class:`MarketService` (lazily built)."""
    global _service
    if _service is None:
        _service = MarketService(load_settings())
    return _service


def get_fundamental_service() -> FundamentalService:
    global _fundamental_service
    if _fundamental_service is None:
        _fundamental_service = FundamentalService(load_settings())
    return _fundamental_service


def get_technical_service() -> TechnicalService:
    global _technical_service
    if _technical_service is None:
        _technical_service = TechnicalService(load_settings())
    return _technical_service


def get_calculation_service() -> CalculationService:
    global _calculation_service
    if _calculation_service is None:
        _calculation_service = CalculationService(load_settings())
    return _calculation_service


def get_chart_service() -> ChartService:
    global _chart_service
    if _chart_service is None:
        _chart_service = ChartService(load_settings())
    return _chart_service


def get_report_service() -> ReportService:
    global _report_service
    if _report_service is None:
        _report_service = ReportService(load_settings())
    return _report_service


def _unavailable_chart(
    symbol: str, exc: Exception, chart_type: str, *, notes: list[str] | None = None
) -> ChartResult:
    hint = f" {SYMBOL_HELP}" if isinstance(exc, InputError) else ""
    return ChartResult(
        chart_name=f"{str(symbol).strip().upper()} {chart_type}",
        symbol=str(symbol).strip().upper(),
        chart_type=chart_type,
        status="unavailable",
        source="unavailable",
        generated_at=iso(utc_now()) or "",
        error=f"{exc}{hint}",
        notes=list(notes or []) + ["本次没有生成任何文件，因此没有返回文件路径。"],
    )


def _unavailable_report(
    symbol: str, exc: Exception, report_type: str, output_format: str
) -> ReportResult:
    hint = f" {SYMBOL_HELP}" if isinstance(exc, InputError) else ""
    return ReportResult(
        report_name=f"{str(symbol).strip().upper()} {report_type} report",
        symbol=str(symbol).strip().upper(),
        report_type=report_type,
        output_format=output_format,
        status="unavailable",
        generated_at=iso(utc_now()) or "",
        error=f"{exc}{hint}",
        missing_data=[],
        notes=["本次没有生成报告文件，因此没有返回文件路径。"],
    )


def _unavailable_provenance(symbol: str, *, source: str = "unavailable") -> Provenance:
    return Provenance(
        symbol=str(symbol).strip().upper(),
        source=source,
        retrieved_at=iso(utc_now()) or "",
        data_status=DataStatus.UNAVAILABLE,
        notes=[],
    )


def _unavailable_financials(symbol: str, exc: Exception, period_type: str) -> FinancialStatements:
    hint = f" {SYMBOL_HELP}" if isinstance(exc, InputError) else ""
    return FinancialStatements(
        symbol=str(symbol).strip().upper(),
        market=Market.UNKNOWN,
        period_type=period_type,  # type: ignore[arg-type]
        data_status=DataStatus.UNAVAILABLE,
        retrieved_at=iso(utc_now()) or "",
        error=f"{exc}{hint}",
        notes=["数据缺失不等于公司没有披露，也不代表数值为 0。"],
    )


def _unavailable_metrics(symbol: str, exc: Exception, period_type: str) -> MetricsResult:
    hint = f" {SYMBOL_HELP}" if isinstance(exc, InputError) else ""
    return MetricsResult(
        symbol=str(symbol).strip().upper(),
        period_type=period_type,
        requested_metrics=[],
        metrics=[],
        provenance=_unavailable_provenance(symbol),
        data_status=DataStatus.UNAVAILABLE,
        error=f"{exc}{hint}",
        notes=[],
    )


def _unavailable_technicals(symbol: str, exc: Exception, period: str, interval: str) -> TechnicalResult:
    hint = f" {SYMBOL_HELP}" if isinstance(exc, InputError) else ""
    return TechnicalResult(
        symbol=str(symbol).strip().upper(),
        period=period,
        interval=interval,
        indicators=[],
        summary=[],
        provenance=_unavailable_provenance(symbol),
        data_status=DataStatus.UNAVAILABLE,
        error=f"{exc}{hint}",
        notes=[],
    )


def _unavailable_fundamentals(symbol: str, exc: Exception) -> FundamentalsResult:
    hint = f" {SYMBOL_HELP}" if isinstance(exc, InputError) else ""
    return FundamentalsResult(
        symbol=str(symbol).strip().upper(),
        market=Market.UNKNOWN,
        dimensions=[],
        observations=[],
        limitations=["无法获取财务报表，因此没有生成任何结论。"],
        metric_tables={},
        valuation=[],
        provenance=_unavailable_provenance(symbol),
        data_status=DataStatus.UNAVAILABLE,
        error=f"{exc}{hint}",
    )


def _unavailable_quote(raw_symbol: str, exc: Exception, *, asset_type: str | None = None) -> Quote:
    hint = ""
    message = str(exc)
    if isinstance(exc, InputError):
        hint = f" {SYMBOL_HELP}"
    return Quote.unavailable(
        symbol=str(raw_symbol).strip().upper(),
        market=Market.UNKNOWN,
        asset_type=AssetType.UNKNOWN,
        source="unavailable",
        error=f"{message}{hint}",
    )


def _unavailable_history(
    raw_symbol: str, period: str, interval: str, exc: Exception
) -> HistoricalData:
    hint = ""
    if isinstance(exc, InputError):
        hint = f" {SYMBOL_HELP}"
    return HistoricalData(
        symbol=str(raw_symbol).strip().upper(),
        market=Market.UNKNOWN,
        asset_type=AssetType.UNKNOWN,
        period=str(period),
        interval=str(interval),
        source="unavailable",
        data_status=DataStatus.UNAVAILABLE,
        retrieved_at=iso(utc_now()) or "",
        bars=[],
        count=0,
        error=f"{str(exc)}{hint}",
    )


def build_server() -> Any:
    """Create and configure the MCP server instance."""
    from mcp.server import MCPServer

    mcp = MCPServer(
        name=load_settings().server_name,
        title=DISPLAY_NAME,
        description=SUBTITLE,
        instructions=INSTRUCTIONS,
        version=VERSION,
        log_level=str(load_settings().log_level),
    )

    @mcp.tool()
    def get_server_status() -> ServerStatus:
        """检查 FMI MCP 服务是否可用：返回版本、启用中的数据源、支持的市场与核心模块。

        这是调用其他工具前的健康检查，也能告诉你当前启用/禁用了哪些数据源
        （由 FMI_ENABLE_SINA / FMI_ENABLE_AKSHARE / FMI_ENABLE_YFINANCE / FMI_PROVIDER_ORDER 控制）。
        """
        return get_service().get_server_status()

    @mcp.tool()
    def get_supported_markets() -> SupportedMarkets:
        """列出当前真正支持的市场、资产类型、数据源、历史周期与K线间隔，并给出代码格式说明。

        「真正支持」指已经接通并且能取到数据的能力，不包含计划中的功能。
        不确定某个代码怎么写时先调用本工具，不要靠猜。
        """
        return get_service().get_supported_markets()

    @mcp.tool()
    def get_stock_quote(
        symbol: Annotated[
            str,
            Field(
                description=(
                    "资产代码。美股 AAPL / NVDA；港股 0700.HK（可写 700.HK）；"
                    "A股 600519.SS / 000001.SZ；指数 000001.SS / ^GSPC / ^HSI；"
                    "期货 GC=F / CL=F。也接受中文别名：上证指数、深证成指、沪深300、"
                    "恒生指数、恒生科技、标普500、纳指、道指、黄金、WTI原油、布伦特原油。"
                )
            ),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US / COMMODITY；与代码本身冲突时会明确报错。"),
        ] = None,
    ) -> Quote:
        """获取单只股票/ETF/指数的统一报价快照。

        Args:
            symbol: 资产代码，如 AAPL、NVDA、0700.HK、600519.SS、000001.SS、^GSPC。
                也接受中文别名，如 上证指数、沪深300、恒生科技、标普500、黄金。
            market: 可选市场提示（CN / HK / US / COMMODITY），仅在代码本身有歧义时使用。

        返回字段包含 price、previous_close、change、change_percent、timestamp（行情时间）、
        retrieved_at（获取时间）、source、data_status 与 error。
        价格不可得时 price 为 null 且 error 说明真实原因——不要自行补 0 或猜测。
        data_status 只为 delayed / stale / unknown / unavailable，绝不声称实时。
        """
        try:
            return get_service().get_quote(symbol, market=market)
        except InputError as exc:
            logger.info("bad quote input %r: %s", symbol, exc)
            return _unavailable_quote(symbol, exc)

    @mcp.tool()
    def get_historical_data(
        symbol: Annotated[
            str,
            Field(description="资产代码，规则与 get_stock_quote 相同（支持 .SS/.SZ/.HK/^指数/=F 期货与中文别名）。"),
        ],
        period: Annotated[
            str,
            Field(description="历史窗口，可选 " + " / ".join(SUPPORTED_PERIODS) + "，默认 1mo。"),
        ] = "1mo",
        interval: Annotated[
            str,
            Field(
                description=(
                    "K线间隔，可选 " + " / ".join(SUPPORTED_INTERVALS) + "，默认 1d。"
                    "1wk / 1mo 由日线聚合；period=1mo 时不能配 interval=1mo。"
                )
            ),
        ] = "1d",
    ) -> HistoricalData:
        """获取历史K线数据（OHLCV）。

        Args:
            symbol: 资产代码，规则同 get_stock_quote。
            period: 历史窗口，可选 1mo / 3mo / 6mo / 1y / 2y / 5y，默认 1mo。
            interval: K线间隔，可选 1d / 1wk / 1mo，默认 1d。
                period=1mo 时 interval 不能是 1mo（窗口太小，会明确报错）。

        返回 bars（最旧在前）、count、truncated，以及数据源与获取时间。
        1wk / 1mo 的K线由日线在服务端聚合（开=首日开、高=最高、低=最低、收=末日收、量=合计），
        这一点会写进 status_note。取不到数据时 bars 为空且 error 给出真实原因。
        """
        try:
            return get_service().get_historical_data(symbol, period=period, interval=interval)
        except InputError as exc:
            logger.info("bad history input %r: %s", symbol, exc)
            return _unavailable_history(symbol, period, interval, exc)

    @mcp.tool()
    def get_market_overview() -> MarketOverview:
        """获取固定参考资产的市场概览：A股、港股、美股、大宗商品四个分组。

        覆盖范围（每个资产独立请求，单个失败不影响其他）：
        - A股：上证指数 000001.SS、深证成指 399001.SZ、沪深300 000300.SS
        - 港股：恒生指数 ^HSI、恒生科技指数 ^HSTECH
        - 美股：标普500 ^GSPC、纳斯达克综合 ^IXIC、道琼斯工业 ^DJI
        - 大宗商品：黄金 GC=F、WTI原油 CL=F、布伦特原油 BZ=F（期货参考价，非现货）

        返回 groups（每组的 items 是 Quote）、ok_count、unavailable_count 与整体 notes。
        引用这些数字时需要说明它们是延迟行情，并给出各条目的行情时间。
        """
        return get_service().get_market_overview()

    @mcp.tool()
    def get_asset_quote(
        symbol: Annotated[
            str,
            Field(
                description=(
                    "资产代码，支持中文别名（黄金 / WTI原油 / 布伦特原油 / 上证指数 / 纳指 等）"
                    "与股指、商品期货合约（GC=F / CL=F / BZ=F）。"
                )
            ),
        ],
        asset_type: Annotated[
            str | None,
            Field(description="可选资产类型提示，取值 stock / index / commodity，用于消除代码歧义。"),
        ] = None,
    ) -> Quote:
        """通用资产报价（股票、指数、商品期货参考合约都能查），与 get_stock_quote 共用底层服务。

        Args:
            symbol: 资产代码，支持中文别名（黄金 / WTI原油 / 布伦特原油 / 上证指数 / 纳指 等）。
            asset_type: 可选资产类型提示：stock / index / commodity。
                用于消除歧义，例如 000001 默认按深市股票（平安银行）解释，
                想查上证指数请写 000001.SS 或 asset_type="index" 或直接用别名「上证指数」。

        规则与保证同 get_stock_quote：不编造价格、不把期货价标成现货价、不声称实时。
        """
        try:
            return get_service().get_quote(symbol, asset_type=asset_type)
        except InputError as exc:
            logger.info("bad asset input %r: %s", symbol, exc)
            return _unavailable_quote(symbol, exc, asset_type=asset_type)

    @mcp.tool()
    def get_company_financials(
        symbol: Annotated[
            str,
            Field(description="公司代码：美股 AAPL / MSFT / NVDA；港股 0700.HK；A股 600519.SS / 000001.SZ。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US；省略时按代码自动判断。"),
        ] = None,
        period_type: Annotated[
            str,
            Field(description="报告期类型：annual（年报）或 quarterly（季报）。ttm 目前不支持（免费源无法可靠拼接）。"),
        ] = "annual",
        limit: Annotated[
            int,
            Field(description="返回最近多少期，1–10，默认 5。"),
        ] = 5,
    ) -> FinancialStatements:
        """获取公司的标准化财务报表（利润表 / 资产负债表 / 现金流量表）。

        Args:
            symbol: 公司代码，如 AAPL、MSFT、0700.HK、600519.SS。
            market: 可选市场提示（CN / HK / US）。
            period_type: annual（年报）或 quarterly（季报）；A股季报是年初至今累计口径，
                结果里的 cumulative=True 会明确标注。
            limit: 返回最近多少期（1–10）。

        返回字段包含 symbol、company_name、currency、unit、unit_scale、fiscal_period、
        period_type、period_start、period_end、filing_date、source、三张报表的标准化字段
        （income_statement / balance_sheet / cash_flow_statement）、raw_items（原始科目）、
        matched_items（命中的原始科目名）、derived_fields（推导出来的字段）、missing_fields、
        data_status 与 retrieved_at。

        必须区分：报告期（period_end）、报告发布日（filing_date，可能为空）、本次获取时间
        （retrieved_at）。缺失字段是 null——不要当成 0，也不要说公司没有披露。
        """
        try:
            return get_fundamental_service().get_financials(
                symbol, market=market, period_type=period_type, limit=limit
            )
        except (InputError, ValueError) as exc:
            logger.info("bad financials input %r: %s", symbol, exc)
            return _unavailable_financials(symbol, exc, period_type)

    @mcp.tool()
    def calculate_financial_metrics(
        symbol: Annotated[
            str,
            Field(description="公司代码，如 AAPL、0700.HK、600519.SS。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US。"),
        ] = None,
        metrics: Annotated[
            str | None,
            Field(
                description=(
                    "逗号分隔的指标名，例如 gross_margin,net_margin,roe。"
                    "可选：gross_margin / operating_margin / net_margin / roe / roa / debt_ratio / "
                    "current_ratio / quick_ratio / interest_coverage / asset_turnover / "
                    "receivables_turnover / inventory_turnover / revenue_growth / net_income_growth / "
                    "eps_growth / free_cash_flow / cfo_to_net_income / pe / pb / ps / dividend_yield。"
                    "省略时只计算与常见问题相关的一组核心指标，不会默认算全部。"
                )
            ),
        ] = None,
        period_type: Annotated[
            str,
            Field(description="报告期类型：annual 或 quarterly。"),
        ] = "annual",
        periods: Annotated[
            int | None,
            Field(description="返回最近多少期（annual 默认 3，quarterly 默认 4）。"),
        ] = None,
    ) -> MetricsResult:
        """从真实财务报表计算一组财务指标（含多期序列与趋势）。

        Args:
            symbol: 公司代码。
            market: 可选市场提示。
            metrics: 逗号分隔的指标名；省略时只算核心指标（gross_margin、net_margin、roe、
                debt_ratio、revenue_growth、free_cash_flow）。
            period_type: annual 或 quarterly。
            periods: 期数。

        每个指标返回 value、unit、display、formula、inputs、input_fields、calculation_status、
        explanation、source、currency、filing_date。calculation_status 取 ok /
        insufficient_data（数据不全）/ not_applicable（公式不适用，例如亏损公司的 PE）。
        估值指标需要同币种的最新股价，币种不一致时不会生成（港股以人民币报表、港币交易的
        情况就是这样），这是刻意的，不要用其他市场的价格替代。
        """
        try:
            service = get_fundamental_service()
            names = [item.strip() for item in (metrics or "").split(",") if item.strip()]
            return service.compute_metrics(
                symbol, market=market, metrics=names or None, period_type=period_type, periods=periods
            )
        except (InputError, ValueError) as exc:
            logger.info("bad metrics input %r: %s", symbol, exc)
            return _unavailable_metrics(symbol, exc, period_type)

    @mcp.tool()
    def calculate_financial_formula(
        formula_name: Annotated[
            str,
            Field(
                description=(
                    "已注册公式名，例如 compound_interest / yoy_growth / qoq_growth / cagr / "
                    "free_cash_flow / return_on_equity / gross_margin / pe / roe。"
                    "填 custom_expression 时用 expression 传入自定义表达式。"
                )
            ),
        ],
        inputs: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "公式输入，JSON 对象，例如 {\"current\": 120, \"previous\": 100}。"
                    "custom_expression 时用 {\"expression\": \"(a - b) / b * 100\", \"variables\": {\"a\": 120, \"b\": 100}}。"
                )
            ),
        ] = None,
    ) -> FormulaResult:
        """用注册好的公式做安全的金融计算（不执行任意代码）。

        Args:
            formula_name: 公式名（见 get_supported_indicators 的 formulas）。
            inputs: 输入值字典；缺失或非数值会返回 insufficient_data 并说明。

        每个公式都带名称、描述、表达式、输入定义、单位、适用条件与缺失数据处理规则。
        未注册的公式不会被猜测执行；想算自定义口径请用 custom_expression，
        它只允许基础算术（+ - * / ** %）与 sqrt/abs/min/max/round/log/ln/log10/exp，
        禁止属性访问、下标、导入或任何 Python 代码。
        """
        try:
            return get_calculation_service().calculate(formula_name, inputs or {})
        except ValueError as exc:
            logger.info("bad formula input %r: %s", formula_name, exc)
            return FormulaResult(
                formula_name=str(formula_name),
                inputs={},
                calculation_status="not_applicable",
                explanation=str(exc),
            )

    @mcp.tool()
    def get_technical_indicators(
        symbol: Annotated[
            str,
            Field(description="资产代码，如 NVDA、AAPL、0700.HK、600519.SS。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US。"),
        ] = None,
        period: Annotated[
            str,
            Field(description="历史窗口，可选 1mo / 3mo / 6mo / 1y / 2y / 5y，默认 6mo。"),
        ] = "6mo",
        interval: Annotated[
            str,
            Field(description="K线间隔，可选 1d / 1wk / 1mo，默认 1d。"),
        ] = "1d",
        indicators: Annotated[
            str | None,
            Field(
                description=(
                    "逗号分隔的指标族或具体指标，例如 sma,rsi,macd 或 sma_20,macd_histogram。"
                    "可选族：sma（5/10/20/60）、ema（12/26）、rsi（14）、macd（12,26,9）、"
                    "bollinger（20,2σ）、atr（14）。省略时返回全部默认指标。"
                )
            ),
        ] = None,
    ) -> TechnicalResult:
        """计算真实历史行情上的技术指标（SMA / EMA / RSI / MACD / 布林带 / ATR）。

        Args:
            symbol: 资产代码。
            market: 可选市场提示。
            period: 历史窗口。
            interval: K线间隔。
            indicators: 指标选择（族名或具体名，逗号分隔）；省略时用默认组合。

        返回 window_start / window_end / bars / indicators / summary 与 provenance。
        每个指标含 params、value、previous_value、change、change_pct、date、history、status、
        explanation。计算口径：EMA 以首个收盘价为种子（adjust=False，α=2/(n+1)）；RSI 用
        Wilder 平滑（首值为前 14 期简单平均）；MACD 柱 = DIF − DEA；布林带 σ 用总体标准差
        （ddof=0）；ATR 的 TR 从第二根 K 线起算。历史不足时该指标返回 insufficient_data。

        这些数字只描述历史状态，不能用来预测涨跌；不要据此给出买卖信号或目标价。
        """
        try:
            service = get_technical_service()
            selection = [item.strip() for item in (indicators or "").split(",") if item.strip()]
            return service.get_indicators(
                symbol,
                market=market,
                period=period,
                interval=interval,
                indicators=selection or None,
            )
        except (InputError, ValueError) as exc:
            logger.info("bad indicator input %r: %s", symbol, exc)
            return _unavailable_technicals(symbol, exc, period, interval)

    @mcp.tool()
    def analyze_fundamentals(
        symbol: Annotated[
            str,
            Field(description="公司代码，如 AAPL、0700.HK、600519.SS。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US。"),
        ] = None,
        analysis_scope: Annotated[
            str,
            Field(
                description=(
                    "分析范围：full（全部维度）/ profitability（盈利）/ solvency（偿债）/ "
                    "efficiency（运营）/ growth（成长）/ cash_flow（现金流）/ valuation（估值）。"
                )
            ),
        ] = "full",
        period_type: Annotated[
            str,
            Field(description="报告期类型：annual 或 quarterly。"),
        ] = "annual",
        periods: Annotated[
            int | None,
            Field(description="多期比较的期数（annual 默认 3，quarterly 默认 4）。"),
        ] = None,
    ) -> FundamentalsResult:
        """基于真实财报做结构化基本面分析（盈利 / 偿债 / 运营 / 成长 / 现金流 / 估值）。

        Args:
            symbol: 公司代码。
            market: 可选市场提示。
            analysis_scope: full 或单个维度。
            period_type: annual 或 quarterly。
            periods: 期数。

        返回公司信息、currency/unit、fiscal_period、filing_date、dimensions（每维度含
        findings 与状态）、observations（多期变化）、metric_tables（指标|各期|趋势）、
        valuation、limitations 与 provenance。

        状态只会是 positive / negative / neutral / insufficient_data / not_applicable，
        并且由明确规则与真实数据决定。本项目不提供行业基准，所以不会给出“优秀/糟糕”的
        绝对评价，也不会编造行业平均值；数据不足时会明确说明无法判断。
        """
        try:
            return get_fundamental_service().analyze(
                symbol,
                market=market,
                scope=analysis_scope,
                period_type=period_type,
                periods=periods,
            )
        except (InputError, ValueError) as exc:
            logger.info("bad fundamentals input %r: %s", symbol, exc)
            return _unavailable_fundamentals(symbol, exc)

    @mcp.tool()
    def analyze_technicals(
        symbol: Annotated[
            str,
            Field(description="资产代码，如 NVDA、AAPL、0700.HK、600519.SS。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US。"),
        ] = None,
        period: Annotated[
            str,
            Field(description="历史窗口，可选 1mo / 3mo / 6mo / 1y / 2y / 5y，默认 6mo。"),
        ] = "6mo",
        interval: Annotated[
            str,
            Field(description="K线间隔，可选 1d / 1wk / 1mo，默认 1d。"),
        ] = "1d",
        indicators: Annotated[
            str | None,
            Field(description="逗号分隔的指标选择，省略时使用默认组合。"),
        ] = None,
    ) -> TechnicalResult:
        """在真实历史行情上做技术面分析，输出指标数值与历史状态描述。

        Args:
            symbol: 资产代码。
            market: 可选市场提示。
            period: 历史窗口。
            interval: K线间隔。
            indicators: 指标选择。

        与 get_technical_indicators 返回同一套结构，summary 是可直接引用的状态描述，
        例如“收盘价高于 MA20”“MA5 高于 MA20”“RSI(14) 处于相对高位区间”。
        这些描述只说明历史状态，不预测未来，也不构成买卖建议。
        """
        try:
            service = get_technical_service()
            selection = [item.strip() for item in (indicators or "").split(",") if item.strip()]
            return service.get_indicators(
                symbol,
                market=market,
                period=period,
                interval=interval,
                indicators=selection or None,
            )
        except (InputError, ValueError) as exc:
            logger.info("bad technical input %r: %s", symbol, exc)
            return _unavailable_technicals(symbol, exc, period, interval)

    @mcp.tool()
    def compare_companies(
        symbols: Annotated[
            str,
            Field(description="逗号分隔的公司代码，例如 AAPL,MSFT 或 600519.SS,000858.SZ。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示；混市场比较时省略。"),
        ] = None,
        metrics: Annotated[
            str | None,
            Field(
                description=(
                    "逗号分隔的指标名；省略时用 gross_margin,operating_margin,net_margin,roe,roa。"
                )
            ),
        ] = None,
        period_type: Annotated[
            str,
            Field(description="报告期类型：annual 或 quarterly。"),
        ] = "annual",
    ) -> ComparisonResult:
        """横向比较多家公司同一组财务指标，并检查可比性。

        Args:
            symbols: 逗号分隔的代码列表。
            market: 可选市场提示。
            metrics: 指标名列表，省略时用盈利能力五项。
            period_type: annual 或 quarterly。

        返回 rows（每行是一个指标在多家公司上的值）、comparability_notes 与 warnings。
        只有在报告期、币种、金额单位与口径都一致时 comparable 才为 True；不一致会
        明确列出原因（例如报告期不同、币种不同），并且不会强行排名或选出“最好的公司”。
        """
        try:
            names = [item.strip() for item in str(symbols or "").split(",") if item.strip()]
            return get_fundamental_service().compare(
                names, market=market, metrics=metrics, period_type=period_type
            )
        except (InputError, ValueError) as exc:
            logger.info("bad compare input %r: %s", symbols, exc)
            return ComparisonResult(
                requested_symbols=[item.strip() for item in str(symbols or "").split(",") if item.strip()],
                metric_names=[],
                rows=[],
                comparability_notes=[],
                warnings=[],
                provenance=_unavailable_provenance(symbols),
                data_status=DataStatus.UNAVAILABLE,
                error=str(exc),
            )

    @mcp.tool()
    def get_supported_indicators() -> SupportedIndicatorsResult:
        """列出所有可用的公式、财务指标与技术指标，以及各数据源的当前状态。

        返回 formulas（公式名、说明、输入、单位）、metrics（21 个财务指标）、
        technical（6 族技术指标）与 data_sources（含 available / throttled /
        rate_limited 状态）。不确定某个指标名怎么写、或想知道某个数据源是否可用时
        先调用本工具。
        """
        return get_calculation_service().supported()

    @mcp.tool()
    def generate_financial_chart(
        symbol: Annotated[
            str,
            Field(description="资产代码，如 AAPL、NVDA、0700.HK、600519.SS、^GSPC、GC=F。"),
        ],
        chart_type: Annotated[
            str,
            Field(
                description=(
                    "图表类型：line（走势折线）/ area（面积）/ candlestick（K线）/ "
                    "volume（成交量）/ technical（技术指标面板）/ fundamental（财务趋势）。默认 line。"
                )
            ),
        ] = "line",
        period: Annotated[
            str,
            Field(description="历史窗口，可选 1mo / 3mo / 6mo / 1y / 2y / 5y，默认 6mo。"),
        ] = "6mo",
        interval: Annotated[
            str,
            Field(description="K线间隔，可选 1d / 1wk / 1mo，默认 1d。"),
        ] = "1d",
        indicators: Annotated[
            str | None,
            Field(
                description=(
                    "逗号分隔的选择：technical 图可指定 ma,macd,rsi,bollinger,atr；"
                    "fundamental 图可指定 revenue,net_income,gross_margin,roe,cash_flow。"
                )
            ),
        ] = None,
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US。"),
        ] = None,
    ) -> ChartResult:
        """在真实数据上生成一张图表文件（HTML + 可选 PNG），并返回可核对的元数据。

        Args:
            symbol: 资产代码。
            chart_type: 图表类型。
            period: 历史窗口。
            interval: K线间隔。
            indicators: 技术面板或财务指标选择。
            market: 可选市场提示。

        图表只画已经取到的数据：技术指标由与技术面工具完全相同的代码计算，
        不会在绘图时另算一套。返回的 html_path / png_path 是本次真实的文件路径，
        文件不存在时不会返回路径；数据不足时 status 为 unavailable 并在 error 中说明原因。
        """
        selection = [item.strip() for item in (indicators or "").split(",") if item.strip()]
        try:
            return get_chart_service().generate(
                symbol,
                chart_type=chart_type,
                period=period,
                interval=interval,
                indicators=selection or None,
                market=market,
            )
        except (InputError, ValueError) as exc:
            logger.info("bad chart input %r: %s", symbol, exc)
            return _unavailable_chart(symbol, exc, chart_type)
        except Exception as exc:  # noqa: BLE001 - a chart failure must not kill the server
            logger.exception("chart generation failed for %r", symbol)
            return _unavailable_chart(symbol, exc, chart_type)

    @mcp.tool()
    def generate_comparison_chart(
        symbols: Annotated[
            str,
            Field(description="逗号分隔的多个代码，例如 AAPL,MSFT,NVDA。"),
        ],
        metric: Annotated[
            str,
            Field(
                description=(
                    "比较口径：price（价格相对表现，可配合 normalization）/ "
                    "revenue / net_income / gross_margin / net_margin / roe / debt_ratio / free_cash_flow。"
                )
            ),
        ] = "price",
        period: Annotated[
            str,
            Field(description="历史窗口（price 口径下使用），默认 6mo。"),
        ] = "6mo",
        normalization: Annotated[
            str,
            Field(description="归一化方式：none 或 base_100（以首个数据点为 100）。默认 none。"),
        ] = "none",
        market: Annotated[
            str | None,
            Field(description="可选市场提示；跨市场比较时省略。"),
        ] = None,
    ) -> ChartResult:
        """比较多个标的的价格相对表现或同一财务指标。

        Args:
            symbols: 逗号分隔的代码。
            metric: price 或其他财务指标。
            period: 历史窗口。
            normalization: none 或 base_100。
            market: 可选市场提示。

        价格比较按 base_100 归一化时只表示相对涨跌，不代表汇率调整后的投资回报。
        金额类指标（revenue / net_income / free_cash_flow）在不同币种之间会直接拒绝
        生成图表并说明原因，不会把人民币与美元金额画在同一纵轴上；报告期不一致时
        会在 notes 中提示。
        """
        try:
            return get_chart_service().comparison(
                symbols,
                metric=metric,
                period=period,
                normalization=normalization,
                market=market,
            )
        except (InputError, ValueError) as exc:
            logger.info("bad comparison chart input %r: %s", symbols, exc)
            return _unavailable_chart(symbols, exc, f"comparison:{metric}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("comparison chart failed for %r", symbols)
            return _unavailable_chart(symbols, exc, f"comparison:{metric}")

    @mcp.tool()
    def get_available_chart_types() -> AvailableChartsResult:
        """列出当前真实可用的图表类型、可用指标选项与输出目录。

        返回 chart_types（每项含 name / label / category / requires / options /
        available）、technical_indicators、fundamental_metrics、normalizations、
        output_formats 与 png_export_enabled。只列出已实现且经过测试的类型，
        未实现的类型不会出现在结果里。
        """
        return get_chart_service().available()

    @mcp.tool()
    def generate_analysis_report(
        symbol: Annotated[
            str,
            Field(description="资产代码；对比报告可传逗号分隔的多个代码，如 AAPL,MSFT,NVDA。"),
        ],
        market: Annotated[
            str | None,
            Field(description="可选市场提示，取值 CN / HK / US。"),
        ] = None,
        report_type: Annotated[
            str,
            Field(description="报告类型：fundamental / technical / comprehensive / comparison。"),
        ] = "fundamental",
        period: Annotated[
            str,
            Field(description="技术面窗口（technical / comprehensive 使用），默认 6mo。"),
        ] = "6mo",
        interval: Annotated[
            str,
            Field(description="K线间隔，默认 1d。"),
        ] = "1d",
        output_format: Annotated[
            str,
            Field(description="主输出格式：markdown 或 html；两个版本都会写在同一个目录。"),
        ] = "markdown",
        include_charts: Annotated[
            bool,
            Field(description="是否在报告中插入图表，默认 true。"),
        ] = True,
        include_raw_data: Annotated[
            bool,
            Field(description="是否附加原始数据字段说明，默认 false。"),
        ] = False,
        period_type: Annotated[
            str,
            Field(description="财务报告期类型：annual 或 quarterly，默认 annual。"),
        ] = "annual",
        periods: Annotated[
            int | None,
            Field(description="使用最近几期财报，省略时 annual 用 3 期、quarterly 用 4 期。"),
        ] = None,
    ) -> ReportResult:
        """生成一份基于真实数据的 Markdown + HTML 研究报告。

        Args:
            symbol: 资产代码。
            market: 可选市场提示。
            report_type: fundamental / technical / comprehensive / comparison。
            period: 技术面窗口。
            interval: K线间隔。
            output_format: 主格式。
            include_charts: 是否插图。
            include_raw_data: 是否附数据说明。
            period_type: 财务报告期类型。
            periods: 使用的财报期数。

        报告中的每个数字都来自工具返回的真实数据；缺失字段以“—”标注而不会补 0，
        数据不完整时会生成部分报告并说明缺什么。comprehensive 需要基本面与技术面
        都可用，缺一侧会标注为部分报告。返回的 markdown_path / html_path 是真实文件路径。
        """
        try:
            return get_report_service().generate(
                symbol,
                market=market,
                report_type=report_type,
                period=period,
                interval=interval,
                output_format=output_format,
                include_charts=include_charts,
                include_raw_data=include_raw_data,
                period_type=period_type,
                periods=periods,
                symbols=symbol,
            )
        except (InputError, ValueError) as exc:
            logger.info("bad report input %r: %s", symbol, exc)
            return _unavailable_report(symbol, exc, report_type, output_format)
        except Exception as exc:  # noqa: BLE001
            logger.exception("report generation failed for %r", symbol)
            return _unavailable_report(symbol, exc, report_type, output_format)

    @mcp.tool()
    def get_report_templates() -> ReportTemplatesResult:
        """列出四类报告模板（基本面 / 技术面 / 综合分析 / 公司对比）。

        返回每类模板的适用场景、所需数据、输出格式与章节结构；生成报告前可以先用
        本工具确认某类报告需要哪些数据、会包含哪些章节。PDF 不在支持范围内。
        """
        return get_report_service().templates()

    return mcp


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``financial-intelligence-mcp`` and ``python -m ...server``."""
    parser = argparse.ArgumentParser(
        prog="financial-intelligence-mcp",
        description=f"{DISPLAY_NAME} MCP server ({SUBTITLE})",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "sse", "streamable-http"),
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings)
    logger.info(
        "%s v%s starting (transport=%s, providers=%s)",
        settings.display_name,
        settings.version,
        args.transport,
        ",".join(settings.enabled_providers()) or "none",
    )

    mcp = build_server()
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
