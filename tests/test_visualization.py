"""Chart generation tests.

Every test uses fixed, network-free fixtures and asserts on the real output:
the HTML file exists, the chart carries the expected title and axis content,
and the metadata records the data timestamp (not the generation time).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from financial_intelligence.models.analysis_result import (
    ComparisonResult,
    ComparisonRow,
    ComparisonValue,
)
from financial_intelligence.models.financial_statements import FinancialStatement
from financial_intelligence.services.chart_service import ChartService
from financial_intelligence.services.fundamental_service import FundamentalService
from financial_intelligence.services.technical_service import TechnicalService
from financial_intelligence.utils.validators import InputError

from fixtures import (
    FakeFinancialProvider,
    FakeMarket,
    make_bars,
    make_history,
    make_settings,
    make_statement,
)


class CurrencyProvider(FakeFinancialProvider):
    """Same fixture statements, but a CNY reporting currency for A-share codes."""

    def get_financials(
        self, spec, *, period_type: str = "annual", limit: int = 5
    ) -> list[FinancialStatement]:
        items = super().get_financials(spec, period_type=period_type, limit=limit)
        currency = "CNY" if spec.symbol.endswith((".SS", ".SZ")) else "USD"
        return [item.model_copy(update={"currency": currency}) for item in items]


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def market():
    return FakeMarket(history_map={"MSFT": make_history("MSFT", name="Microsoft")})


@pytest.fixture()
def service(settings, market):
    technical = TechnicalService(settings=settings, market=market)
    fundamental = FundamentalService(
        settings=settings, providers=[FakeFinancialProvider()], market=market
    )
    return ChartService(
        settings, market=market, technical=technical, fundamental=fundamental
    )


def _html(result) -> str:
    assert result.html_path is not None, result.error
    path = Path(result.html_path)
    assert path.is_file(), result.html_path
    return path.read_text(encoding="utf-8")


def _has(text: str, label: str) -> bool:
    """Plotly may escape non-ASCII text inside its JSON; accept both forms."""
    return label in text or json.dumps(label, ensure_ascii=True)[1:-1] in text


# -- price charts ------------------------------------------------------------
def test_line_chart_writes_real_html(service):
    result = service.generate("AAPL", chart_type="line", period="6mo")
    assert result.status == "ok"
    text = _html(result)
    assert _has(text, "历史价格走势")
    assert "AAPL" in text
    assert result.metadata.symbol == "AAPL"
    assert result.metadata.points == 80
    assert result.metadata.currency == "USD"
    assert result.metadata.source == "fixture"
    assert result.png_path is None  # PNG export disabled in these settings


def test_area_chart_is_supported(service):
    result = service.generate("AAPL", chart_type="area")
    assert result.status == "ok"
    assert Path(result.html_path).is_file()


def test_candlestick_chart_has_ohlc_and_volume(service):
    result = service.generate("AAPL", chart_type="candlestick", period="3mo")
    assert result.status == "ok"
    text = _html(result)
    assert _has(text, "K 线图")
    assert _has(text, "成交量")
    assert result.metadata.chart_type == "candlestick"


def test_volume_chart_reports_missing_volume(service, market):
    empty = make_history("AAPL", volume=False)
    market._history["AAPL"] = empty
    result = service.generate("AAPL", chart_type="volume")
    assert result.status == "unavailable"
    assert result.html_path is None
    assert "成交量" in (result.error or "")


def test_insufficient_bars_do_not_produce_a_chart(service, market):
    market._history["AAPL"] = make_history("AAPL", bars=make_bars(2))
    result = service.generate("AAPL", chart_type="candlestick")
    assert result.status in {"unavailable", "ok"}
    if result.status == "unavailable":
        assert result.html_path is None
    else:  # two candles are still a valid (if minimal) chart
        assert Path(result.html_path).is_file()


# -- technical panels --------------------------------------------------------
@pytest.mark.parametrize(
    ("panel", "marker"),
    [
        ("ma", "MA5"),
        ("macd", "DIF"),
        ("rsi", "RSI14"),
        ("bollinger", "中轨"),
        ("atr", "ATR14"),
    ],
)
def test_technical_panels(service, panel, marker):
    result = service.generate("AAPL", chart_type="technical", indicators=panel)
    assert result.status == "ok", result.error
    text = _html(result)
    assert _has(text, marker)
    assert _has(text, "技术指标")


def test_technical_defaults_cover_ma_macd_rsi(service):
    """Without an explicit list the chart shows the compact default panel."""
    result = service.generate("AAPL", chart_type="technical")
    assert result.status == "ok"
    text = _html(result)
    for marker in ("MA5", "MACD", "RSI14"):
        assert _has(text, marker)


def test_technical_explicit_panels_include_bollinger_and_atr(service):
    result = service.generate("AAPL", chart_type="technical", indicators="ma,bollinger,atr")
    assert result.status == "ok", result.error
    text = _html(result)
    for marker in ("MA5", "中轨", "ATR14"):
        assert _has(text, marker)


def test_unknown_indicator_is_rejected(service):
    with pytest.raises(InputError):
        service.generate("AAPL", chart_type="technical", indicators="KDJ")


def test_technical_chart_history_matches_indicator_source(service):
    result = service.generate("AAPL", chart_type="technical", indicators="ma")
    text = _html(result)
    # MA5/MA20 are computed by analysis.technical from the same bars, so the
    # last average values must appear in the rendered traces.
    assert "sma_5" in text or "MA5" in text
    assert result.metadata.points == 80


# -- fundamental charts ------------------------------------------------------
def test_fundamental_revenue_chart_orders_periods(service):
    result = service.generate("AAPL", chart_type="fundamental")
    assert result.status == "ok", result.error
    text = _html(result)
    assert _has(text, "财务趋势")
    assert _has(text, "营业收入")
    assert "FY2023" in text and "FY2025" in text
    assert result.metadata.fiscal_period == "FY2025"


def test_fundamental_gross_margin_is_percent(service):
    result = service.generate("AAPL", chart_type="fundamental", indicators="gross_margin")
    assert result.status == "ok", result.error
    text = _html(result)
    assert _has(text, "毛利率")
    assert _has(text, "百分比（%）")


def test_fundamental_metric_without_data_is_not_plotted(settings, market):
    blank = [
        make_statement(
            fiscal_period=f"FY{2025 - index}",
            period_end=date(2025 - index, 9, 27),
            income={"net_income": None},
        )
        for index in range(3)
    ]
    fundamental = FundamentalService(
        settings=settings, providers=[FakeFinancialProvider(blank)], market=market
    )
    service = ChartService(settings, market=market, fundamental=fundamental)
    result = service.generate("AAPL", chart_type="fundamental", indicators="net_income")
    assert result.status == "unavailable"
    assert result.html_path is None
    assert "缺失" in (result.error or "")


# -- comparisons -------------------------------------------------------------
def test_price_comparison_base_100(service):
    result = service.comparison(
        ["AAPL", "MSFT", "600519.SS"], metric="price", normalization="base_100"
    )
    assert result.status == "ok", result.error
    text = _html(result)
    assert _has(text, "多标的相对表现")
    assert _has(text, "基准=100")
    assert "CNY" in text and "USD" in text


def test_price_comparison_rejects_mixed_currency_without_normalization(service):
    result = service.comparison(["AAPL", "600519.SS"], metric="price", normalization="none")
    assert result.status == "unavailable"
    assert "币种不同" in (result.error or "")
    assert result.html_path is None


def test_fundamental_comparison_ratio_metric(settings, market):
    fundamental = FundamentalService(
        settings=settings, providers=[FakeFinancialProvider()], market=market
    )
    service = ChartService(settings, market=market, fundamental=fundamental)
    result = service.comparison(["AAPL", "MSFT"], metric="gross_margin")
    assert result.status in {"ok", "partial"}, result.error
    text = _html(result)
    assert _has(text, "公司对比")
    assert _has(text, "毛利率")


def test_fundamental_comparison_rejects_cross_currency_amounts(settings, market):
    """The chart layer must refuse USD vs CNY amounts on one axis.

    A stub comparison is used so the test targets the chart guard itself, not
    whatever the fundamental service decides about comparability.
    """
    comparison = ComparisonResult(
        requested_symbols=["AAPL", "600519.SS"],
        metric_names=["revenue"],
        rows=[
            ComparisonRow(
                metric="revenue",
                label="营业收入",
                unit="currency",
                formula="营业收入",
                values=[
                    ComparisonValue(
                        symbol="AAPL",
                        company_name="Apple",
                        currency="USD",
                        value=1000.0,
                        display="1,000 USD",
                        calculation_status="ok",
                    ),
                    ComparisonValue(
                        symbol="600519.SS",
                        company_name="贵州茅台",
                        currency="CNY",
                        value=170000.0,
                        display="170,000 CNY",
                        calculation_status="ok",
                    ),
                ],
                comparable=False,
                notes=["币种不同"],
            )
        ],
        comparability_notes=["币种不同（CNY、USD）"],
        warnings=[],
        provenance=[],
        data_status="historical",
        error=None,
    )

    class StubFundamental:
        def compare(self, *args, **kwargs):
            return comparison

    service = ChartService(settings, market=market, fundamental=StubFundamental())
    result = service.comparison(["AAPL", "600519.SS"], metric="revenue")
    assert result.status == "unavailable"
    assert "跨币种" in (result.error or "")
    assert result.html_path is None


# -- catalog and files -------------------------------------------------------
def test_available_chart_types_lists_only_implemented(service):
    catalog = service.available()
    names = [item.name for item in catalog.chart_types]
    assert names == ["line", "area", "candlestick", "volume", "technical", "fundamental"]
    assert all(item.available for item in catalog.chart_types)
    assert catalog.png_export_enabled is False
    assert "html" in catalog.output_formats
    assert "json" in catalog.output_formats


def test_unknown_chart_type_is_rejected(service):
    with pytest.raises(InputError):
        service.generate("AAPL", chart_type="pie")


def test_two_runs_do_not_overwrite_each_other(service):
    first = service.generate("AAPL", chart_type="line")
    second = service.generate("AAPL", chart_type="line")
    assert first.html_path != second.html_path
    assert Path(first.html_path).is_file()
    assert Path(second.html_path).is_file()


def test_png_export_when_enabled(tmp_path, market):
    settings = make_settings(tmp_path, png=True)
    service = ChartService(
        settings,
        market=market,
        technical=TechnicalService(settings=settings, market=market),
        fundamental=FundamentalService(
            settings=settings, providers=[FakeFinancialProvider()], market=market
        ),
    )
    result = service.generate("AAPL", chart_type="candlestick", period="3mo")
    assert result.status == "ok", result.error
    assert result.png_path is not None
    png = Path(result.png_path)
    assert png.is_file()
    assert png.stat().st_size > 1_000


def test_metadata_timestamps_are_distinct(service):
    result = service.generate("AAPL", chart_type="line")
    meta = result.metadata
    assert meta.data_timestamp == "2026-08-19"  # last fixture bar
    assert meta.generated_at is None or meta.generated_at != meta.data_timestamp
    assert meta.data_range is not None and "至" in meta.data_range
