"""Service layer for technical indicators over real historical bars."""

from __future__ import annotations

from collections.abc import Sequence

from ..analysis.technical import (
    TechnicalInputError,
    compute_indicators,
    indicator_catalog,
    summarize,
)
from ..config import Settings, load_settings
from ..models.analysis_result import Provenance, TechnicalResult
from ..schemas import DataStatus, iso, utc_now
from ..utils import map_symbol
from .market_service import MarketService


class TechnicalService:
    """Fetches OHLCV bars through the market service and runs the indicator engine."""

    def __init__(self, settings: Settings | None = None, *, market: MarketService | None = None) -> None:
        self.settings = settings or load_settings()
        self._market = market or MarketService(settings=self.settings)
        self._names: dict[str, str | None] = {}

    def _company_name(self, spec, history) -> str | None:
        """Best-effort display name; never blocks the indicator computation."""
        if history.name:
            return history.name
        if spec.symbol in self._names:
            return self._names[spec.symbol]
        name: str | None = None
        try:
            name = self._market.get_quote(spec.symbol).name
        except Exception:  # noqa: BLE001 - a missing name must not break the tool
            name = None
        self._names[spec.symbol] = name
        return name

    def get_indicators(
        self,
        symbol: str,
        *,
        market: str | None = None,
        period: str = "6mo",
        interval: str = "1d",
        indicators: Sequence[str] | None = None,
        history_points: int = 10,
    ) -> TechnicalResult:
        spec = map_symbol(symbol, market=market)
        history = self._market.get_historical_data(spec.symbol, period=period, interval=interval)
        company_name = self._company_name(spec, history)
        window = ""
        if history.bars:
            window = f"{history.bars[0].date} 至 {history.bars[-1].date}"
        provenance = Provenance(
            symbol=spec.symbol,
            market=spec.market,
            source=history.source or "unavailable",
            retrieved_at=iso(utc_now()),
            data_timestamp=history.bars[-1].date if history.bars else None,
            reporting_period=window or None,
            currency=history.currency or "UNKNOWN",
            unit="price",
            calculation_method="technical indicators over OHLCV",
            data_status=history.data_status,
            notes=list(history.status_note.split("；")) if history.status_note else [],
        )
        if history.data_status == DataStatus.UNAVAILABLE or not history.bars:
            return TechnicalResult(
                symbol=spec.symbol,
                company_name=company_name,
                market=spec.market,
                period=period,
                interval=interval,
                bars=0,
                indicators=[],
                summary=[],
                provenance=provenance,
                data_status=DataStatus.UNAVAILABLE,
                error=history.error or "没有可用的历史行情数据",
                notes=[
                    "技术指标需要真实的 OHLCV 历史数据，未获取到数据时不会生成任何数值。",
                    "所有指标只描述历史价格状态，不构成买卖建议，也不预测未来价格。",
                ],
            )
        try:
            series = compute_indicators(history.bars, indicators, history_points=history_points)
        except TechnicalInputError as exc:
            return TechnicalResult(
                symbol=spec.symbol,
                company_name=company_name,
                market=spec.market,
                period=period,
                interval=interval,
                bars=len(history.bars),
                indicators=[],
                summary=[],
                provenance=provenance,
                data_status=DataStatus.UNAVAILABLE,
                error=str(exc),
            )
        available = [item for item in series if item.value is not None]
        notes: list[str] = []
        if history.truncated:
            notes.append("历史数据已被截断（max_history_rows），指标基于截断后的末尾区间计算。")
        notes.append("所有指标只描述历史价格状态，不构成买卖建议，也不预测未来价格。")
        return TechnicalResult(
            symbol=spec.symbol,
            company_name=company_name,
            market=spec.market,
            currency=history.currency or "UNKNOWN",
            period=period,
            interval=interval,
            window_start=history.bars[0].date,
            window_end=history.bars[-1].date,
            bars=len(history.bars),
            indicators=series,
            summary=summarize(available, bars=len(history.bars), window=window),
            provenance=provenance,
            data_status=history.data_status,
            notes=notes,
        )

    def catalog(self) -> list[dict[str, object]]:
        return indicator_catalog()


__all__ = ["TechnicalService"]
