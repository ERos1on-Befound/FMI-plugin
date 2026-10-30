"""MCP server exposing the Financial Market Intelligence tool surface.

Six tools are registered here; every one of them returns a typed model from
:mod:`financial_intelligence.schemas`, so the MCP clients get a real output
schema (not a free-form string).

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
from .services import MarketService
from .utils.symbol_mapper import SYMBOL_HELP
from .utils.validators import InputError

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""\
{DISPLAY_NAME} — {SUBTITLE}

使用顺序建议：
1. 先调用 get_server_status 确认服务可用、看看启用了哪些数据源。
2. 不确定代码格式时调用 get_supported_markets，或直接参考下列约定：
{SYMBOL_HELP}
3. 单只资产 → get_stock_quote（股票/ETF/指数）或 get_asset_quote（指数/商品等通用）。
4. 历史K线 → get_historical_data（period ∈ {", ".join(SUPPORTED_PERIODS)}；
   interval ∈ {", ".join(SUPPORTED_INTERVALS)}）。
5. 需要横向看盘 → get_market_overview（固定覆盖 A股/港股/美股/大宗商品）。

硬性约束（回答用户时必须遵守）：
- 数据全部来自免费公开接口，不能声称是实时行情；yfinance / AKShare / 新浪快照一律按 delayed 处理。
- 必须同时给出「行情时间 timestamp」和「获取时间 retrieved_at」，不能混为一谈。
- 期货代码（GC=F / CL=F / BZ=F）是期货合约参考价，不是现货价格。
- 价格为 null 时就是没有数据，禁止编造或用 0 填充；把 error 字段里的真实原因告诉用户。
- 只做数据检索与整理，不构成投资建议。
"""

_service: MarketService | None = None


def get_service() -> MarketService:
    """Return the process-wide :class:`MarketService` (lazily built)."""
    global _service
    if _service is None:
        _service = MarketService(load_settings())
    return _service


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
