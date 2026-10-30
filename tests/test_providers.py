"""Provider-level tests: symbol mapping, quote parsing and bar normalisation.

Nothing here touches the network — provider parsers are pure functions over the
raw payload shapes that were captured from the live endpoints.
"""

from __future__ import annotations

import time
from datetime import date

import pandas as pd
import pytest

from financial_intelligence.config import Settings
from financial_intelligence.providers.base import ProviderError, call_with_timeout
from financial_intelligence.providers.sina import (
    _combine,
    _parse_cn,
    _parse_hf,
    _parse_hk,
    _parse_us,
    sina_code,
)
from financial_intelligence.schemas import AssetType, Market
from financial_intelligence.utils.bars import (
    cap_bars,
    frame_to_bars,
    resample_bars,
    window_bars,
)
from financial_intelligence.utils.symbol_mapper import map_symbol
from financial_intelligence.utils.validators import InputError

SETTINGS = Settings()


# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_symbol", "expected_market", "expected_type"),
    [
        ("600519", "600519.SS", Market.CN, AssetType.STOCK),
        ("600519.SS", "600519.SS", Market.CN, AssetType.STOCK),
        ("sh600519", "600519.SS", Market.CN, AssetType.STOCK),
        ("000001", "000001.SZ", Market.CN, AssetType.STOCK),
        ("000001.SS", "000001.SS", Market.CN, AssetType.INDEX),
        ("399001", "399001.SZ", Market.CN, AssetType.INDEX),
        ("000300", "000300.SS", Market.CN, AssetType.INDEX),
        ("0700.HK", "0700.HK", Market.HK, AssetType.STOCK),
        ("700.hk", "0700.HK", Market.HK, AssetType.STOCK),
        ("9988.HK", "9988.HK", Market.HK, AssetType.STOCK),
        ("AAPL", "AAPL", Market.US, AssetType.STOCK),
        ("^GSPC", "^GSPC", Market.US, AssetType.INDEX),
        ("GC=F", "GC=F", Market.COMMODITY, AssetType.COMMODITY),
        ("cl=f", "CL=F", Market.COMMODITY, AssetType.COMMODITY),
    ],
)
def test_map_symbol(raw: str, expected_symbol: str, expected_market: Market, expected_type: AssetType) -> None:
    spec = map_symbol(raw)
    assert spec.symbol == expected_symbol
    assert spec.market is expected_market
    assert spec.asset_type is expected_type


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("上证指数", "000001.SS"),
        ("沪深300", "000300.SS"),
        ("深证成指", "399001.SZ"),
        ("恒生指数", "000000.HSI_MARKER"),
        ("恒生科技", "^HSTECH"),
        ("标普500", "^GSPC"),
        ("纳指", "^IXIC"),
        ("道指", "^DJI"),
        ("黄金", "GC=F"),
        ("布伦特原油", "BZ=F"),
        ("贵州茅台", "600519.SS"),
    ],
)
def test_chinese_aliases_resolve(alias: str, expected: str) -> None:
    if expected == "000000.HSI_MARKER":
        expected = "^HSI"
    spec = map_symbol(alias)
    assert spec.symbol == expected
    assert spec.name_hint  # aliases always come with a display name


def test_currency_follows_market() -> None:
    assert map_symbol("600519.SS").currency == "CNY"
    assert map_symbol("0700.HK").currency == "HKD"
    assert map_symbol("AAPL").currency == "USD"
    assert map_symbol("GC=F").currency == "USD"


def test_yahoo_symbol_rewrites_share_class_and_shanghai_suffix() -> None:
    assert map_symbol("BRK.B").yahoo_symbol == "BRK-B"
    assert map_symbol("600519").yahoo_symbol == "600519.SS"


def test_market_hint_conflict_is_reported_not_ignored() -> None:
    with pytest.raises(InputError):
        map_symbol("600519.SS", market=Market.US)


def test_asset_type_hint_conflict_is_reported_not_ignored() -> None:
    with pytest.raises(InputError):
        map_symbol("GC=F", asset_type=AssetType.STOCK)


def test_invalid_symbol_rejected() -> None:
    with pytest.raises(InputError):
        map_symbol("!!!")
    with pytest.raises(InputError):
        map_symbol("")


# ---------------------------------------------------------------------------
# Sina code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_code", "expected_family"),
    [
        ("600519.SS", "sh600519", "cn"),
        ("000001.SZ", "sz000001", "cn"),
        ("399001.SZ", "sz399001", "cn"),
        ("0700.HK", "hk00700", "hk"),
        ("AAPL", "gb_aapl", "us"),
        ("^GSPC", "gb_$inx", "us"),
        ("^IXIC", "gb_$ixic", "us"),
        ("^DJI", "gb_$dji", "us"),
        ("^HSI", "hkHSI", "hk"),
        ("^HSTECH", "hkHSTECH", "hk"),
        ("GC=F", "hf_GC", "hf"),
        ("CL=F", "hf_CL", "hf"),
        ("BZ=F", "hf_OIL", "hf"),
    ],
)
def test_sina_code_mapping(raw: str, expected_code: str, expected_family: str) -> None:
    assert sina_code(map_symbol(raw)) == (expected_code, expected_family)


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------


def test_combine_accepts_split_and_combined_beijing_time() -> None:
    assert _combine("2026-10-30", "15:00:00").isoformat() == "2026-10-30T07:00:00+00:00"
    assert _combine("2026-10-30 15:00:00").isoformat() == "2026-10-30T07:00:00+00:00"
    assert _combine("2026/10/30", "09:30").isoformat() == "2026-10-30T01:30:00+00:00"
    assert _combine("").isoformat() if False else _combine("") is None


def test_combine_returns_none_for_garbage() -> None:
    assert _combine("not-a-date") is None


# ---------------------------------------------------------------------------
# Field parsers (payload shapes captured from hq.sinajs.cn)
# ---------------------------------------------------------------------------


def _cn_fields() -> list[str]:
    fields = [""] * 33
    fields[0] = "贵州茅台"
    fields[1] = "1354.000"
    fields[2] = "1354.000"
    fields[3] = "1371.510"
    fields[4] = "1380.000"
    fields[5] = "1350.000"
    fields[30] = "2026-10-30"
    fields[31] = "15:00:00"
    return fields


def test_parse_cn_fields() -> None:
    data = _parse_cn(_cn_fields())
    assert data["name"] == "贵州茅台"
    assert data["price"] == pytest.approx(1371.51)
    assert data["prev_close"] == pytest.approx(1354.0)
    assert data["timestamp"].isoformat() == "2026-10-30T07:00:00+00:00"


def test_parse_hk_fields() -> None:
    fields = [""] * 19
    fields[0] = "TENCENT"
    fields[1] = "腾讯控股"
    fields[2] = "377.600"
    fields[3] = "377.600"
    fields[4] = "389.000"
    fields[5] = "376.200"
    fields[6] = "385.400"
    fields[17] = "2026/10/30"
    fields[18] = "16:08"
    data = _parse_hk(fields)
    assert data["name"] == "腾讯控股"
    assert data["price"] == pytest.approx(385.4)
    assert data["timestamp"].isoformat() == "2026-10-30T08:08:00+00:00"


def test_parse_us_fields_derives_previous_close() -> None:
    fields = [""] * 36
    fields[0] = "苹果"
    fields[1] = "348.9100"
    fields[3] = "2026-10-30 09:39:32"
    fields[4] = "5.0800"
    fields[5] = "348.0000"
    fields[6] = "351.4300"
    fields[7] = "347.3000"
    data = _parse_us(fields)
    assert data["price"] == pytest.approx(348.91)
    assert data["prev_close"] == pytest.approx(343.83)  # no float noise
    assert data["timestamp"].isoformat() == "2026-10-30T01:39:32+00:00"


def test_parse_hf_fields_uses_previous_settlement() -> None:
    fields = [""] * 15
    fields[0] = "4001.876"
    fields[4] = "4040.0"
    fields[5] = "3980.0"
    fields[6] = "12:00:00"
    fields[7] = "4031.400"
    fields[8] = "4035.0"
    fields[12] = "2026-10-30"
    fields[13] = "纽约黄金"
    data = _parse_hf(fields)
    assert data["price"] == pytest.approx(4001.876)
    assert data["prev_close"] == pytest.approx(4031.4)
    assert data["name"] == "纽约黄金"
    assert data["timestamp"].isoformat() == "2026-10-30T04:00:00+00:00"


def test_parse_cn_with_empty_price_yields_none() -> None:
    fields = _cn_fields()
    fields[3] = ""
    assert _parse_cn(fields)["price"] is None


# ---------------------------------------------------------------------------
# Bar normalisation
# ---------------------------------------------------------------------------


def test_frame_to_bars_handles_chinese_columns_and_epoch_milliseconds() -> None:
    frame = pd.DataFrame(
        {
            "日期": [1759968000000, 1760054400000],
            "开盘": [10.0, 11.0],
            "收盘": [11.0, 12.0],
            "最高": [11.5, 12.5],
            "最低": [9.5, 10.5],
            "成交量": [1000, 2000],
        }
    )
    bars = frame_to_bars(frame)
    assert [bar["date"] for bar in bars] == ["2025-10-09", "2025-10-10"]
    assert bars[0]["close"] == pytest.approx(11.0)
    assert bars[1]["volume"] == pytest.approx(2000.0)


def test_frame_to_bars_accepts_yfinance_style_index() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1234],
        },
        index=pd.to_datetime(["2026-10-29"]),
    )
    bars = frame_to_bars(frame)
    assert len(bars) == 1
    assert bars[0]["date"] == "2026-10-29"
    assert bars[0]["close"] == pytest.approx(100.5)


def test_frame_to_bars_drops_rows_without_prices() -> None:
    bars = frame_to_bars([{"date": "2026-10-29"}, {"date": "2026-10-30", "close": 1.0}])
    assert len(bars) == 1


def test_resample_bars_aggregates_weekly() -> None:
    daily = [
        {"date": "2026-10-19", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0},
        {"date": "2026-10-23", "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200.0},
        {"date": "2026-10-26", "open": 11.5, "high": 13.0, "low": 11.0, "close": 12.0, "volume": 300.0},
    ]
    weekly = resample_bars(daily, "1wk")
    assert len(weekly) == 2
    assert weekly[0]["date"] == "2026-10-23"
    assert weekly[0]["open"] == pytest.approx(10.0)
    assert weekly[0]["high"] == pytest.approx(12.0)
    assert weekly[0]["low"] == pytest.approx(9.0)
    assert weekly[0]["close"] == pytest.approx(11.5)
    assert weekly[0]["volume"] == pytest.approx(300.0)


def test_resample_bars_monthly() -> None:
    daily = [
        {"date": "2026-09-30", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        {"date": "2026-10-01", "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5, "volume": 20.0},
        {"date": "2026-10-29", "open": 2.5, "high": 4.0, "low": 2.0, "close": 3.5, "volume": 30.0},
    ]
    monthly = resample_bars(daily, "1mo")
    assert [bar["date"] for bar in monthly] == ["2026-09-30", "2026-10-29"]
    assert monthly[1]["volume"] == pytest.approx(50.0)


def test_window_bars_trims_by_period() -> None:
    bars = [
        {"date": "2026-01-02", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"date": "2026-09-29", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"date": "2026-10-30", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    kept = window_bars(bars, "1mo", today=date(2026, 10, 30))
    assert [bar["date"] for bar in kept] == ["2026-09-29", "2026-10-30"]


def test_cap_bars_reports_truncation() -> None:
    bars = [{"date": f"2026-10-{day:02d}", "close": 1.0} for day in range(1, 11)]
    capped, truncated = cap_bars(bars, 4)
    assert truncated is True
    assert len(capped) == 4
    assert capped[-1]["date"] == "2026-10-10"


# ---------------------------------------------------------------------------
# Provider plumbing
# ---------------------------------------------------------------------------


class _YFRateLimitError(RuntimeError):
    pass


def test_provider_error_classifies_rate_limit() -> None:
    err = ProviderError.from_exception("yfinance", _YFRateLimitError("Too Many Requests"), symbol="AAPL")
    assert err.kind == "rate_limit"
    assert "AAPL" in str(err)
    assert "yfinance" in str(err)


def test_provider_error_classifies_timeout() -> None:
    err = ProviderError.from_exception("sina", TimeoutError("timed out"), symbol="AAPL")
    assert err.kind == "timeout"


def test_call_with_timeout_returns_value() -> None:
    assert call_with_timeout(lambda: 42, 1.0, "test") == 42


def test_call_with_timeout_enforces_hard_deadline() -> None:
    def slow() -> None:
        time.sleep(0.5)

    with pytest.raises(ProviderError) as excinfo:
        call_with_timeout(slow, 0.05, "test", symbol="AAPL")
    assert excinfo.value.kind == "timeout"


def test_call_with_timeout_wraps_provider_errors() -> None:
    def boom() -> None:
        raise ProviderError("sina", "坏掉了", kind="network")

    with pytest.raises(ProviderError) as excinfo:
        call_with_timeout(boom, 1.0, "sina")
    assert excinfo.value.message == "坏掉了"
    assert excinfo.value.kind == "network"


def test_settings_reads_environment_overrides() -> None:
    from financial_intelligence.config import load_settings

    settings = load_settings(
        {
            "FMI_REQUEST_TIMEOUT": "3",
            "FMI_CACHE_TTL": "5",
            "FMI_ENABLE_YFINANCE": "0",
            "FMI_PROVIDER_ORDER": "sina,akshare",
        }
    )
    assert settings.request_timeout == 3.0
    assert settings.cache_ttl == 5.0
    assert settings.enable_yfinance is False
    assert settings.provider_order == ("sina", "akshare")
    assert "yfinance" not in settings.enabled_providers()
