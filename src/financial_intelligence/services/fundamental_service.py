"""Service layer for financial statements, metrics, fundamentals and comparisons."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

from ..analysis import fundamental as fundamentals_analysis
from ..analysis.financial_ratios import (
    DIMENSION_METRICS,
    DIMENSION_ORDER,
    METRIC_SPECS,
    QuoteContext,
    compute_metrics as compute_metric_series,
)
from ..config import Settings, load_settings
from ..models.analysis_result import (
    ComparisonResult,
    ComparisonRow,
    ComparisonValue,
    FundamentalsResult,
    MetricsResult,
    Provenance,
)
from ..models.financial_statements import FinancialStatement, FinancialStatements
from ..providers import build_financial_providers
from ..providers.base import FinancialDataProvider, ProviderError
from ..schemas import DataStatus, Market, iso, utc_now
from ..utils import InputError, map_symbol
from ..utils.symbol_mapper import SymbolSpec
from .market_service import MarketService


def _split_names(value: Sequence[str] | str | None) -> list[str]:
    """Accept ``"a,b"`` as well as ``["a", "b"]`` (the MCP layer passes lists)."""
    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value)
    return [part.strip() for item in raw for part in str(item or "").split(",") if part.strip()]


#: Metrics used when the caller does not name any (never "all metrics").
CORE_METRICS: tuple[str, ...] = (
    "gross_margin",
    "net_margin",
    "roe",
    "debt_ratio",
    "revenue_growth",
    "free_cash_flow",
)

SUPPORTED_PERIOD_TYPES = ("annual", "quarterly")


class FundamentalService:
    """Orchestrates financial providers, the ratio layer and the analysis rules."""

    def __init__(
        self,
        settings: Settings | None = None,
        providers: Sequence[FinancialDataProvider] | None = None,
        *,
        market: MarketService | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._providers = list(providers) if providers is not None else None
        self._market = market or MarketService(settings=self.settings)
        self._cache: dict[tuple[str, str, int], tuple[float, list[FinancialStatement], str]] = {}
        self._quotes: dict[str, object | None] = {}
        self._names: dict[str, str | None] = {}

    # ------------------------------------------------------------------ infra
    @property
    def providers(self) -> list[FinancialDataProvider]:
        if self._providers is None:
            self._providers = build_financial_providers(self.settings)
        return self._providers

    def clear_cache(self) -> None:
        self._cache.clear()
        self._quotes.clear()
        self._names.clear()

    def _prepare(self, symbol: str, market: str | None, period_type: str) -> SymbolSpec:
        period_key = str(period_type or "annual").strip().lower()
        if period_key not in SUPPORTED_PERIOD_TYPES:
            raise InputError(
                f"不支持的 period_type：{period_type}；当前只支持 annual（年度）与 quarterly（季度）。"
                "TTM 需要拼接四个季度，免费数据源不保证完整，因此本版本不提供。"
            )
        spec = map_symbol(symbol, market=market)
        if spec.asset_type.value != "stock":
            raise InputError(f"财务数据只支持股票；{spec.symbol} 被识别为 {spec.asset_type.value}。")
        if spec.market not in (Market.CN, Market.HK, Market.US):
            raise InputError(f"财务数据当前只覆盖 A 股、港股与美股；{spec.symbol} 属于 {spec.market.value}。")
        return spec

    def _fetch(
        self, spec: SymbolSpec, period_type: str, limit: int
    ) -> tuple[list[FinancialStatement], str, bool, list[str]]:
        key = (spec.symbol, period_type, limit)
        now = time.monotonic()
        cached = self._cache.get(key)
        ttl = self.settings.financial_cache_ttl
        if cached is not None and ttl > 0 and now - cached[0] <= ttl:
            return list(cached[1]), cached[2], True, []
        errors: list[str] = []
        for provider in self.providers:
            if not provider.supports_financials(spec):
                errors.append(f"{provider.name}: 不支持 {spec.market.value} 的财务报表")
                continue
            try:
                statements = provider.get_financials(spec, period_type=period_type, limit=limit)
            except ProviderError as exc:
                errors.append(f"{exc.provider} [{exc.kind}]: {exc.message}")
                continue
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller as text
                errors.append(f"{provider.name} [error]: {exc}")
                continue
            if not statements:
                errors.append(f"{provider.name}: 没有返回任何报告期")
                continue
            source = statements[0].source or provider.name
            self._cache[key] = (now, list(statements), source)
            return list(statements), source, False, []
        return [], "", False, errors

    def _quote(self, spec: SymbolSpec):
        if spec.symbol in self._quotes:
            return self._quotes[spec.symbol]
        try:
            quote = self._market.get_quote(spec.symbol)
        except Exception:  # noqa: BLE001 - valuations are optional
            quote = None
        self._quotes[spec.symbol] = quote
        return quote

    def _quote_context(self, spec: SymbolSpec) -> QuoteContext | None:
        quote = self._quote(spec)
        if quote is None or getattr(quote, "price", None) is None:
            return None
        return QuoteContext(
            price=quote.price,
            currency=quote.currency or "UNKNOWN",
            timestamp=quote.timestamp or quote.retrieved_at,
            source=quote.source,
            symbol=quote.symbol,
        )

    def _company_name(self, spec: SymbolSpec, statements: Sequence[FinancialStatement]) -> str | None:
        for statement in statements:
            if statement.company_name:
                return statement.company_name
        if spec.name_hint:
            return spec.name_hint
        if spec.symbol in self._names:
            return self._names[spec.symbol]
        quote = self._quote(spec)
        name = getattr(quote, "name", None) if quote is not None else None
        self._names[spec.symbol] = name
        return name

    def _provenance(
        self,
        spec: SymbolSpec,
        statements: Sequence[FinancialStatement],
        *,
        source: str,
        method: str,
    ) -> Provenance:
        newest = statements[0] if statements else None
        return Provenance(
            symbol=spec.symbol,
            market=spec.market,
            source=source or "unavailable",
            retrieved_at=iso(utc_now()),
            data_timestamp=newest.period_end.isoformat() if newest and newest.period_end else None,
            reporting_period=newest.fiscal_period if newest else None,
            filing_date=newest.filing_date.isoformat() if newest and newest.filing_date else None,
            currency=newest.currency if newest else "UNKNOWN",
            unit=newest.unit if newest else "unknown",
            calculation_method=method,
            data_status=DataStatus.HISTORICAL if statements else DataStatus.UNAVAILABLE,
            notes=[],
        )

    # ------------------------------------------------------------- public API
    def get_financials(
        self,
        symbol: str,
        *,
        market: str | None = None,
        period_type: str = "annual",
        limit: int = 5,
    ) -> FinancialStatements:
        spec = self._prepare(symbol, market, period_type)
        periods = max(1, min(int(limit or 5), 10))
        statements, source, from_cache, errors = self._fetch(spec, period_type.strip().lower(), periods)
        if not statements:
            return FinancialStatements(
                symbol=spec.symbol,
                company_name=self._company_name(spec, []),
                market=spec.market,
                period_type=period_type.strip().lower(),  # type: ignore[arg-type]
                data_status=DataStatus.UNAVAILABLE,
                retrieved_at=iso(utc_now()),
                error="；".join(errors) or "没有可用的财务数据源",
                notes=["数据缺失不等于公司没有披露，也不代表数值为 0。"],
            )
        newest = statements[0]
        notes = [
            f"共返回 {len(statements)} 个报告期。" + ("（来自本次运行的内存缓存）" if from_cache else ""),
            "金额单位倍数见每条记录的 unit_scale：原始金额 = 展示值 × unit_scale。",
        ]
        if any(statement.cumulative for statement in statements):
            notes.append("部分季度报告为年初至今累计口径（cumulative=true），单季数据需与上一期相减。")
        return FinancialStatements(
            symbol=spec.symbol,
            company_name=self._company_name(spec, statements),
            market=spec.market,
            currency=newest.currency,
            unit=newest.unit,
            period_type=period_type.strip().lower(),  # type: ignore[arg-type]
            source=source,
            statements=list(statements),
            data_status=DataStatus.HISTORICAL,
            retrieved_at=iso(utc_now()),
            notes=notes,
        )

    def _series_bundle(
        self,
        spec: SymbolSpec,
        statements: Sequence[FinancialStatement],
        metric_names: Iterable[str],
        *,
        periods: int | None,
    ):
        quote = self._quote_context(spec)
        series = compute_metric_series(statements, metric_names, periods=periods, quote=quote)
        return series, quote

    def compute_metrics(
        self,
        symbol: str,
        *,
        market: str | None = None,
        metrics: Sequence[str] | None = None,
        period_type: str = "annual",
        periods: int | None = None,
    ) -> MetricsResult:
        spec = self._prepare(symbol, market, period_type)
        wanted = _split_names(metrics) or list(CORE_METRICS)
        window = periods if periods is not None else (3 if period_type.strip().lower() == "annual" else 4)
        statements, source, _from_cache, errors = self._fetch(spec, period_type.strip().lower(), max(window, 2))
        if not statements:
            return MetricsResult(
                symbol=spec.symbol,
                company_name=self._company_name(spec, []),
                market=spec.market,
                period_type=period_type,
                requested_metrics=wanted,
                metrics=[],
                provenance=self._provenance(spec, [], source="unavailable", method="metric calculation"),
                data_status=DataStatus.UNAVAILABLE,
                error="；".join(errors) or "没有可用的财务数据源",
                notes=["无法获取财务报表，因此没有生成任何指标。"],
            )
        series, _quote = self._series_bundle(spec, statements, wanted, periods=window)
        notes = [f"指标基于 {len(statements)} 个可用报告期（{periods if periods is not None else window} 期为请求窗口）。"]
        unknown = [name for name in wanted if name not in {item.name for item in series}]
        if unknown:
            notes.append("以下指标无法识别或被跳过：" + "、".join(unknown) + "。")
        notes.append("所有比率均由公开财务报表按项目公式计算；缺失科目一律不补 0。")
        return MetricsResult(
            symbol=spec.symbol,
            company_name=self._company_name(spec, statements),
            market=spec.market,
            currency=statements[0].currency,
            unit=statements[0].unit,
            period_type=period_type.strip().lower(),
            requested_metrics=[item.name for item in series] or wanted,
            metrics=series,
            provenance=self._provenance(spec, statements, source=source, method="ratio calculation over canonical statements"),
            data_status=DataStatus.HISTORICAL,
            notes=notes,
        )

    def analyze(
        self,
        symbol: str,
        *,
        market: str | None = None,
        scope: str = "full",
        period_type: str = "annual",
        periods: int | None = None,
    ) -> FundamentalsResult:
        spec = self._prepare(symbol, market, period_type)
        scope_key = fundamentals_analysis.resolve_scope(scope)
        window = periods if periods is not None else (3 if period_type.strip().lower() == "annual" else 4)
        dimensions_to_run = DIMENSION_ORDER if scope_key == fundamentals_analysis.SCOPE_ALL else [scope_key]
        wanted = [metric for dimension in dimensions_to_run for metric in DIMENSION_METRICS[dimension]]
        statements, source, _from_cache, errors = self._fetch(spec, period_type.strip().lower(), max(window, 2))
        if not statements:
            return FundamentalsResult(
                symbol=spec.symbol,
                company_name=self._company_name(spec, []),
                market=spec.market,
                period_type=period_type,
                scope=scope_key,
                dimensions=[],
                observations=[],
                limitations=fundamentals_analysis.build_limitations([]),
                metric_tables={},
                valuation=[],
                provenance=self._provenance(spec, [], source="unavailable", method="fundamental analysis"),
                data_status=DataStatus.UNAVAILABLE,
                error="；".join(errors) or "没有可用的财务数据源",
            )
        series, quote = self._series_bundle(spec, statements, wanted, periods=window)
        by_dimension: dict[str, dict[str, object]] = {}
        for item in series:
            spec_meta = METRIC_SPECS.get(item.name)
            if spec_meta is None:
                continue
            by_dimension.setdefault(spec_meta.dimension, {})[item.name] = item
        dimensions = fundamentals_analysis.build_dimensions(by_dimension)  # type: ignore[arg-type]
        if scope_key != fundamentals_analysis.SCOPE_ALL:
            dimensions = [item for item in dimensions if item.dimension == scope_key]
        valuation_series = by_dimension.get("valuation", {})
        valuation_points = [item.latest for item in valuation_series.values() if item.latest is not None]  # type: ignore[union-attr]
        valuation_blocked = scope_key in (fundamentals_analysis.SCOPE_ALL, "valuation") and not any(
            point.value is not None for point in valuation_points
        )
        periods_used: list[str] = []
        for item in series:
            periods_used = [point.period or "" for point in item.points if point.period]
            if periods_used:
                break
        return FundamentalsResult(
            symbol=spec.symbol,
            company_name=self._company_name(spec, statements),
            market=spec.market,
            currency=statements[0].currency,
            unit=statements[0].unit,
            period_type=period_type.strip().lower(),
            scope=scope_key,
            fiscal_period=statements[0].fiscal_period,
            filing_date=statements[0].filing_date.isoformat() if statements[0].filing_date else None,
            periods_used=periods_used,
            dimensions=dimensions,
            observations=fundamentals_analysis.build_observations(by_dimension),  # type: ignore[arg-type]
            limitations=fundamentals_analysis.build_limitations(
                statements, valuation_blocked=valuation_blocked
            ),
            metric_tables=fundamentals_analysis.build_tables(by_dimension),  # type: ignore[arg-type]
            valuation=valuation_points,
            provenance=self._provenance(spec, statements, source=source, method="rule-based fundamental analysis"),
            data_status=DataStatus.HISTORICAL,
        )

    def compare(
        self,
        symbols: Sequence[str],
        *,
        market: str | None = None,
        metrics: Sequence[str] | None = None,
        period_type: str = "annual",
    ) -> ComparisonResult:
        raw_items = _split_names(symbols)
        if not raw_items:
            raise InputError("至少需要一个公司代码才能比较，例如 AAPL,MSFT。")
        wanted = _split_names(metrics) or ["gross_margin", "operating_margin", "net_margin", "roe", "roa"]
        frames: list[tuple[SymbolSpec, list[FinancialStatement], dict[str, object], str]] = []
        provenance: list[Provenance] = []
        warnings: list[str] = []
        for raw_symbol in raw_items:
            spec = self._prepare(raw_symbol, market, period_type)
            statements, source, _from_cache, errors = self._fetch(spec, period_type.strip().lower(), 2)
            if not statements:
                warnings.append(f"{spec.symbol}：无法获取财务报表（{'；'.join(errors) or '无数据'}）。")
                provenance.append(self._provenance(spec, [], source="unavailable", method="comparison"))
                continue
            series, _quote = self._series_bundle(spec, statements, wanted, periods=2)
            frames.append((spec, statements, {item.name: item for item in series}, source))
            provenance.append(self._provenance(spec, statements, source=source, method="comparison"))
        rows: list[ComparisonRow] = []
        comparability_notes: list[str] = []
        if frames:
            fiscal_periods = {frame[1][0].fiscal_period for frame in frames}
            currencies = {frame[1][0].currency for frame in frames}
            scales = {frame[1][0].unit_scale for frame in frames}
            comparable = len(fiscal_periods) == 1 and len(currencies) == 1 and len(scales) == 1
            if len(fiscal_periods) > 1:
                comparability_notes.append(
                    "报告期不一致（" + "、".join(sorted(fiscal_periods)) + "）：各公司财年结束日不同，横向比较需要先对齐期间。"
                )
            if len(currencies) > 1:
                comparability_notes.append(
                    "币种不一致（" + "、".join(sorted(currencies)) + "）：项目没有免费汇率来源，金额类指标不可直接比较。"
                )
            if len(scales) > 1:
                comparability_notes.append("金额单位倍数不一致：" + "、".join(str(scale) for scale in sorted(scales)) + "。")
            for metric_name in [name for name in wanted]:
                label = ""
                unit = "ratio"
                formula = ""
                values: list[ComparisonValue] = []
                for spec, statements, series_map, _source in frames:
                    series = series_map.get(metric_name)
                    if series is None:
                        continue
                    point = series.latest  # type: ignore[union-attr]
                    label = series.label  # type: ignore[union-attr]
                    unit = series.unit  # type: ignore[union-attr]
                    formula = series.formula  # type: ignore[union-attr]
                    values.append(
                        ComparisonValue(
                            symbol=spec.symbol,
                            company_name=self._company_name(spec, statements),
                            fiscal_period=point.period,
                            filing_date=statements[0].filing_date.isoformat() if statements[0].filing_date else None,
                            currency=statements[0].currency,
                            unit=series.unit,  # type: ignore[union-attr]
                            value=point.value,
                            display=point.display,
                            calculation_status=point.calculation_status,
                            explanation=point.explanation,
                        )
                    )
                if values:
                    rows.append(
                        ComparisonRow(
                            metric=metric_name,
                            label=label or metric_name,
                            unit=unit,
                            formula=formula,
                            values=values,
                            comparable=comparable,
                            notes=list(comparability_notes) if not comparable else [],
                        )
                    )
        if not frames:
            comparability_notes.append("没有可比较的公司：所有请求的代码都未能取得财务报表。")
        notes = comparability_notes or []
        return ComparisonResult(
            requested_symbols=[frame[0].symbol for frame in frames] or list(raw_items),
            metric_names=[row.metric for row in rows],
            rows=rows,
            comparability_notes=notes,
            warnings=warnings + ([] if notes else ["报告期、币种与单位倍数一致，比例指标可直接比较。"]),
            provenance=provenance,
            data_status=DataStatus.HISTORICAL if rows else DataStatus.UNAVAILABLE,
            error=None if rows else "；".join(warnings) or "没有可比较的数据",
        )


__all__ = ["CORE_METRICS", "FundamentalService", "SUPPORTED_PERIOD_TYPES"]
