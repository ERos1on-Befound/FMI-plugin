"""Service layer for the formula engine and the capability catalogue."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..analysis.financial_formulas import FORMULA_VERSION, compute_formula, list_formulas
from ..analysis.financial_ratios import metric_catalog
from ..analysis.technical import indicator_catalog
from ..config import Settings, load_settings
from ..models.analysis_result import FormulaResult, IndicatorSpec, SupportedIndicatorsResult
from ..schemas import DataStatus


class CalculationService:
    """Thin orchestration around the registered formulas (no eval, no user code)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def calculate(self, formula_name: str, inputs: Mapping[str, Any] | None = None) -> FormulaResult:
        return compute_formula(formula_name, inputs)

    def supported(self) -> SupportedIndicatorsResult:
        formulas = [
            IndicatorSpec(
                name=spec.name,
                label=spec.label,
                category=spec.category,
                description=spec.description,
                formula=spec.expression,
                inputs=list(spec.input_names()),
                unit=spec.output_unit,
                available=True,
                limitations=list(spec.notes),
            )
            for spec in list_formulas()
        ]
        metrics = [
            IndicatorSpec(
                name=str(item["name"]),
                label=str(item["label"]),
                category=str(item["dimension"]),
                description=str(item.get("description") or ""),
                formula=str(item.get("formula") or ""),
                inputs=[str(name) for name in item.get("inputs") or []],
                unit=str(item.get("unit") or "ratio"),
                available=True,
                limitations=[str(note) for note in item.get("limitations") or []],
            )
            for item in metric_catalog()
        ]
        technical = [
            IndicatorSpec(
                name=str(item["name"]),
                label=str(item["label"]),
                category=str(item["category"]),
                description=f"最少需要 {item['min_bars']} 根 K 线。",
                formula=str(item.get("formula") or ""),
                inputs=[str(name) for name in item.get("inputs") or []],
                unit="price" if item["name"] != "rsi" else "index",
                available=True,
                limitations=[f"历史不足 {item['min_bars']} 根时返回 insufficient_data。"],
            )
            for item in indicator_catalog()
        ]
        return SupportedIndicatorsResult(
            formulas=formulas,
            metrics=metrics,
            technical=technical,
            data_sources=[
                {
                    "name": "sina",
                    "kind": "quote",
                    "markets": "CN, HK, US, COMMODITY",
                    "status": "available",
                    "notes": "实时性无法证明，一律标记 delayed。",
                },
                {
                    "name": "akshare",
                    "kind": "quote, history, financials",
                    "markets": "CN, HK, US, COMMODITY",
                    "status": "available",
                    "notes": "历史行情与 A 股/港股财务报表（交易所与披露易定期报告镜像）。",
                },
                {
                    "name": "sec",
                    "kind": "financials",
                    "markets": "US",
                    "status": "available",
                    "notes": "SEC EDGAR companyfacts XBRL；需要 User-Agent，遵守 SEC 访问频率要求。",
                },
                {
                    "name": "eastmoney",
                    "kind": "quote, history",
                    "markets": "CN",
                    "status": "throttled",
                    "notes": "IP 被限流时会直接断开连接；熔断后不再重试，需冷却。",
                },
                {
                    "name": "yfinance",
                    "kind": "quote, history",
                    "markets": "US, HK, CN, COMMODITY",
                    "status": "rate_limited",
                    "notes": "本机 IP 被 Yahoo 限流，仅作为可移植回退。",
                },
            ],
            notes=[
                f"公式版本 {FORMULA_VERSION}；高级计算请使用 calculate_financial_formula 的自定义表达式（仅允许基础算术与白名单函数）。",
                "所有指标都要求真实数据；数据缺失时返回 insufficient_data / not_applicable，绝不用 0 填充。",
                "技术指标只描述历史状态，不构成买卖建议。",
            ],
            data_status=DataStatus.HISTORICAL,
        )


__all__ = ["CalculationService"]
