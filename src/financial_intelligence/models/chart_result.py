"""Schemas returned by the chart and report tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChartStatus = Literal["ok", "partial", "unavailable"]
ReportStatus = Literal["ok", "partial", "unavailable"]


class ChartMetadata(BaseModel):
    """Everything needed to judge where a chart's numbers came from."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    asset_name: str | None = None
    chart_type: str
    period: str | None = None
    interval: str | None = None
    currency: str | None = None
    source: str = "unavailable"
    retrieved_at: str | None = None
    data_timestamp: str | None = None
    data_range: str | None = None
    generated_at: str
    fiscal_period: str | None = None
    reporting_currency: str | None = None
    reporting_unit: str | None = None
    points: int = 0
    notes: list[str] = Field(default_factory=list)


class ChartResult(BaseModel):
    """Result of one chart generation request."""

    model_config = ConfigDict(extra="forbid")

    chart_name: str
    symbol: str
    asset_name: str | None = None
    chart_type: str
    status: ChartStatus = "ok"
    data_range: str | None = None
    source: str = "unavailable"
    generated_at: str
    html_path: str | None = None
    png_path: str | None = None
    json_path: str | None = None
    metadata: ChartMetadata | None = None
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class ChartTypeInfo(BaseModel):
    """One entry of the chart-type catalogue."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    category: str
    description: str
    requires: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    available: bool = True
    limitations: str | None = None


class AvailableChartsResult(BaseModel):
    """What the chart module can actually do right now."""

    model_config = ConfigDict(extra="forbid")

    chart_types: list[ChartTypeInfo] = Field(default_factory=list)
    technical_indicators: list[str] = Field(default_factory=list)
    fundamental_metrics: list[str] = Field(default_factory=list)
    normalizations: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=list)
    png_export_enabled: bool = False
    outputs_dir: str | None = None
    notes: list[str] = Field(default_factory=list)
    generated_at: str


class ReportTemplateInfo(BaseModel):
    """One report template of the report module."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    description: str
    report_type: str
    required_data: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)


class ReportTemplatesResult(BaseModel):
    """Catalogue of report templates."""

    model_config = ConfigDict(extra="forbid")

    templates: list[ReportTemplateInfo] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    generated_at: str


class ReportResult(BaseModel):
    """Result of one report generation request."""

    model_config = ConfigDict(extra="forbid")

    report_name: str
    symbol: str
    company_name: str | None = None
    report_type: str
    output_format: str
    status: ReportStatus = "ok"
    generated_at: str
    data_period: str | None = None
    data_sources: list[str] = Field(default_factory=list)
    markdown_path: str | None = None
    html_path: str | None = None
    charts: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
