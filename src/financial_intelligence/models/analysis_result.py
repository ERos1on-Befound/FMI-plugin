"""Result models shared by the formula engine, fundamental and technical analysis.

Every model carries the provenance fields the task requires: source, retrieved
time, reporting period, filing date, currency/unit, calculation method and the
distinction between raw data (``inputs``), computed values (``value``) and
explanations produced by rule-based code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..schemas import DataStatus, Market, iso, utc_now

CalculationStatus = Literal["ok", "insufficient_data", "not_applicable", "unavailable"]
FindingLabel = Literal["positive", "negative", "neutral", "insufficient_data", "not_applicable"]
TrendLabel = Literal["rising", "falling", "stable", "insufficient_data", "not_applicable"]


class Provenance(BaseModel):
    """Where a number or an analysis came from."""

    model_config = ConfigDict(extra="forbid")

    symbol: str | None = None
    market: Market = Market.UNKNOWN
    source: str = "unavailable"
    retrieved_at: str = Field(default_factory=lambda: iso(utc_now()))
    data_timestamp: str | None = None
    reporting_period: str | None = None
    filing_date: str | None = None
    currency: str = "UNKNOWN"
    unit: str = "unknown"
    calculation_method: str | None = None
    formula_version: str = "fmi-formulas/1.0"
    data_status: DataStatus = DataStatus.UNKNOWN
    notes: list[str] = Field(default_factory=list)


class MetricValue(BaseModel):
    """One computed (or non-computable) metric for one period."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    category: str
    period: str
    period_type: str = "annual"
    value: float | None = None
    unit: str = "ratio"
    display: str | None = None
    formula: str = ""
    input_fields: list[str] = Field(default_factory=list)
    inputs: dict[str, float | None] = Field(default_factory=dict)
    calculation_status: CalculationStatus = "ok"
    explanation: str = ""
    source: str = "unknown"
    filing_date: str | None = None
    currency: str = "UNKNOWN"
    derived: bool = False
    notes: list[str] = Field(default_factory=list)

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value


class MetricSeries(BaseModel):
    """A metric across several periods, with a rule-based trend label."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    category: str
    unit: str = "ratio"
    formula: str = ""
    points: list[MetricValue] = Field(default_factory=list)
    trend: TrendLabel = "insufficient_data"
    trend_note: str = ""

    @property
    def latest(self) -> MetricValue | None:
        return self.points[0] if self.points else None


class MetricsResult(BaseModel):
    """Tool-facing payload for ``calculate_financial_metrics``."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str | None = None
    market: Market = Market.UNKNOWN
    currency: str = "UNKNOWN"
    unit: str = "unknown"
    period_type: str = "annual"
    requested_metrics: list[str] = Field(default_factory=list)
    metrics: list[MetricSeries] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    data_status: DataStatus = DataStatus.UNAVAILABLE
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class FormulaResult(BaseModel):
    """Tool-facing payload for ``calculate_financial_formula``."""

    model_config = ConfigDict(extra="forbid")

    formula_name: str
    category: str = "general"
    description: str = ""
    expression: str = ""
    inputs: dict[str, float | None] = Field(default_factory=dict)
    value: float | None = None
    unit: str = "ratio"
    display: str | None = None
    calculation_status: CalculationStatus = "ok"
    explanation: str = ""
    formula_version: str = "fmi-formulas/1.0"
    notes: list[str] = Field(default_factory=list)


class IndicatorSeries(BaseModel):
    """One technical indicator, its parameters and recent history."""

    model_config = ConfigDict(extra="forbid")

    name: str
    category: str = "trend"
    params: dict[str, float] = Field(default_factory=dict)
    unit: str = "price"
    value: float | None = None
    previous_value: float | None = None
    change: float | None = None
    change_pct: float | None = None
    date: str | None = None
    history: list[dict[str, float | str | None]] = Field(default_factory=list)
    status: CalculationStatus = "ok"
    explanation: str = ""
    notes: list[str] = Field(default_factory=list)


class TechnicalResult(BaseModel):
    """Tool-facing payload for ``get_technical_indicators`` / ``analyze_technicals``."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str | None = None
    market: Market = Market.UNKNOWN
    currency: str = "UNKNOWN"
    period: str = "6mo"
    interval: str = "1d"
    window_start: str | None = None
    window_end: str | None = None
    bars: int = 0
    indicators: list[IndicatorSeries] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    data_status: DataStatus = DataStatus.UNAVAILABLE
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    """A single rule-evaluated observation inside a fundamental dimension."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    label: str
    value: float | None = None
    unit: str = "ratio"
    display: str | None = None
    status: FindingLabel = "insufficient_data"
    rule: str = ""
    explanation: str = ""
    period: str | None = None


class DimensionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    label: str
    status: FindingLabel = "insufficient_data"
    findings: list[Finding] = Field(default_factory=list)
    metrics: list[MetricSeries] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FundamentalsResult(BaseModel):
    """Tool-facing payload for ``analyze_fundamentals``."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str | None = None
    market: Market = Market.UNKNOWN
    currency: str = "UNKNOWN"
    unit: str = "unknown"
    period_type: str = "annual"
    scope: str = "full"
    fiscal_period: str | None = None
    filing_date: str | None = None
    periods_used: list[str] = Field(default_factory=list)
    dimensions: list[DimensionAnalysis] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metric_tables: dict[str, list[list[str]]] = Field(default_factory=dict)
    valuation: list[MetricValue] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    data_status: DataStatus = DataStatus.UNAVAILABLE
    error: str | None = None


class ComparisonValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str | None = None
    fiscal_period: str | None = None
    filing_date: str | None = None
    currency: str = "UNKNOWN"
    unit: str = "unknown"
    value: float | None = None
    display: str | None = None
    calculation_status: CalculationStatus = "ok"
    explanation: str = ""


class ComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    label: str
    unit: str = "ratio"
    formula: str = ""
    values: list[ComparisonValue] = Field(default_factory=list)
    comparable: bool = True
    notes: list[str] = Field(default_factory=list)


class ComparisonResult(BaseModel):
    """Tool-facing payload for ``compare_companies``."""

    model_config = ConfigDict(extra="forbid")

    requested_symbols: list[str] = Field(default_factory=list)
    metric_names: list[str] = Field(default_factory=list)
    rows: list[ComparisonRow] = Field(default_factory=list)
    comparability_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.UNAVAILABLE
    error: str | None = None


class IndicatorSpec(BaseModel):
    """Catalogue entry returned by ``get_supported_indicators``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    category: str
    description: str = ""
    formula: str = ""
    inputs: list[str] = Field(default_factory=list)
    unit: str = "ratio"
    available: bool = True
    limitations: list[str] = Field(default_factory=list)


class SupportedIndicatorsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formulas: list[IndicatorSpec] = Field(default_factory=list)
    metrics: list[IndicatorSpec] = Field(default_factory=list)
    technical: list[IndicatorSpec] = Field(default_factory=list)
    data_sources: list[dict[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.HISTORICAL
