"""Report generation tests.

Reports are generated from the same deterministic fixtures as the chart tests,
so every asserted number and section comes from real computation, not from a
mocked template.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fixtures import (
    FakeFinancialProvider,
    FakeMarket,
    make_settings,
    make_statement,
    make_statements,
)

from financial_intelligence.services.chart_service import ChartService
from financial_intelligence.services.fundamental_service import FundamentalService
from financial_intelligence.services.report_service import ReportService
from financial_intelligence.services.technical_service import TechnicalService
from financial_intelligence.utils.validators import InputError


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def market():
    return FakeMarket()


@pytest.fixture()
def service(settings, market):
    fundamentals = FundamentalService(
        settings=settings, providers=[FakeFinancialProvider()], market=market
    )
    technicals = TechnicalService(settings=settings, market=market)
    charts = ChartService(
        settings,
        market=market,
        technical=technicals,
        fundamental=fundamentals,
    )
    return ReportService(
        settings,
        fundamentals=fundamentals,
        technicals=technicals,
        charts=charts,
        market=market,
    )


def _text(path: str | None) -> str:
    assert path, "报告没有返回文件路径"
    file = Path(path)
    assert file.is_file(), path
    return file.read_text(encoding="utf-8")


# -- fundamental report ------------------------------------------------------
def test_fundamental_report_markdown_has_all_sections(service):
    result = service.generate("AAPL", report_type="fundamental", output_format="markdown")
    assert result.status == "ok", result.error
    assert result.report_type == "fundamental"
    assert result.data_sources == ["fixture"]
    text = _text(result.markdown_path)
    for heading in (
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
    ):
        assert heading in text
    assert "毛利率" in text
    assert "ROE" in text
    assert "不构成任何投资" in text
    assert "FY2025" in text


def test_fundamental_report_html_is_standalone(service):
    result = service.generate("AAPL", report_type="fundamental", output_format="html")
    assert result.status == "ok", result.error
    html = _text(result.html_path)
    assert "<html" in html.lower()
    assert "<table" in html
    assert "免责声明" in html
    assert "cdn" not in html.lower()  # no external assets


def test_fundamental_report_references_charts(service):
    result = service.generate("AAPL", report_type="fundamental", include_charts=True)
    assert result.status == "ok", result.error
    assert result.charts, "基本面报告应包含图表"
    for path in result.charts:
        assert Path(path).is_file(), path
    text = _text(result.markdown_path)
    assert "../charts/" in text
    assert ".png" not in text or "../charts/" in text


def test_fundamental_report_marks_missing_data(settings, market):
    statements = [
        make_statement(
            fiscal_period="FY2025",
            period_end=date(2025, 9, 27),
            income={"interest_expense": None},
            missing_fields=["interest_expense"],
        )
    ]
    fundamentals = FundamentalService(
        settings=settings, providers=[FakeFinancialProvider(statements)], market=market
    )
    service = ReportService(settings, fundamentals=fundamentals, market=market)
    result = service.generate("AAPL", report_type="fundamental", include_charts=False)
    assert result.status == "partial"
    assert any("interest_expense" in item for item in result.missing_data)
    text = _text(result.markdown_path)
    assert "不可计算" in text or "缺失" in text


# -- technical report --------------------------------------------------------
def test_technical_report_has_all_sections(service):
    result = service.generate("AAPL", report_type="technical", period="6mo")
    assert result.status == "ok", result.error
    text = _text(result.markdown_path)
    for heading in (
        "历史行情概览",
        "均线系统",
        "RSI",
        "MACD",
        "Bollinger",
        "ATR",
        "技术状态总结",
        "图表",
        "数据来源与免责声明",
    ):
        assert heading in text
    assert "MA5" in text and "MA20" in text and "MA60" in text
    assert "ATR14" in text
    assert "不构成买卖建议" in text


def test_technical_report_contains_no_advice_or_predictions(service):
    result = service.generate("AAPL", report_type="technical")
    text = _text(result.markdown_path)
    for banned in ("建议买入", "建议卖出", "目标价", "必然上涨", "必然下跌", "保证收益"):
        assert banned not in text


def test_technical_report_quotes_last_session_and_source(service):
    result = service.generate("AAPL", report_type="technical")
    text = _text(result.markdown_path)
    assert "2026-08-19" in text  # last fixture bar
    assert "fixture" in text


# -- comprehensive report ----------------------------------------------------
def test_comprehensive_report_combines_both_parts(service):
    result = service.generate("AAPL", report_type="comprehensive")
    assert result.status in {"ok", "partial"}, result.error
    text = _text(result.markdown_path)
    assert "综合分析报告" in text
    assert "盈利能力" in text
    assert "均线系统" in text
    assert "基本面" in text and "技术面" in text


# -- comparison report -------------------------------------------------------
def test_comparison_report_lists_companies_without_ranking(service):
    result = service.generate(
        "AAPL", report_type="comparison", symbols="AAPL,MSFT"
    )
    assert result.status in {"ok", "partial"}, result.error
    text = _text(result.markdown_path)
    assert "公司对比报告" in text
    assert "AAPL" in text and "MSFT" in text
    for heading in ("公司基本信息", "可比性说明", "指标对比表", "主要差异", "数据来源与免责声明"):
        assert heading in text
    for banned in ("排名第一", "最优", "最差"):
        assert banned not in text


def test_comparison_report_notes_incomparable_periods(settings, market):
    first = make_statements(3)
    second = [
        item.model_copy(update={"fiscal_period": f"FY{int(item.fiscal_period[2:]) + 1}"})
        for item in make_statements(3)
    ]

    class SplitProvider(FakeFinancialProvider):
        def get_financials(self, spec, *, period_type="annual", limit=5):
            items = second if spec.symbol == "MSFT" else first
            return [item.model_copy(update={"symbol": spec.symbol}) for item in items[:limit]]

    fundamentals = FundamentalService(
        settings=settings, providers=[SplitProvider()], market=market
    )
    service = ReportService(settings, fundamentals=fundamentals, market=market)
    result = service.generate("AAPL", report_type="comparison", symbols="AAPL,MSFT")
    text = _text(result.markdown_path)
    assert "报告期" in text and ("不一致" in text or "不可比" in text)


# -- generic behaviour -------------------------------------------------------
def test_unknown_report_type_is_rejected(service):
    with pytest.raises(InputError):
        service.generate("AAPL", report_type="podcast")


def test_reports_do_not_overwrite(service):
    first = service.generate("AAPL", report_type="fundamental", include_charts=False)
    second = service.generate("AAPL", report_type="fundamental", include_charts=False)
    assert first.markdown_path != second.markdown_path
    assert Path(first.markdown_path).is_file()
    assert Path(second.markdown_path).is_file()


def test_report_templates_catalog(service):
    catalog = service.templates()
    names = [item.name for item in catalog.templates]
    assert names == ["fundamental", "technical", "comprehensive", "comparison"]
    for item in catalog.templates:
        assert item.sections
        assert item.required_data
        assert item.report_type
    assert "markdown" in catalog.output_formats


def test_report_metadata_is_recorded(service):
    result = service.generate("AAPL", report_type="technical")
    assert result.report_name
    assert result.symbol == "AAPL"
    assert result.generated_at
    assert result.data_period
    assert result.data_sources
    assert result.markdown_path and Path(result.markdown_path).is_file()
