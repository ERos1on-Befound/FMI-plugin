"""Offline tests for the Eastmoney provider.

Every payload here was captured from the live endpoint on 2026-10-30 (see
``docs/eastmoney-diagnosis.md``), so the field-layout assertions are grounded in
real responses while the tests themselves never touch the network.
"""

from __future__ import annotations

import time

import pytest

from financial_intelligence.config import load_settings
from financial_intelligence.providers.base import ProviderError
from financial_intelligence.providers import eastmoney as em
from financial_intelligence.providers.eastmoney import (
    EastmoneyProvider,
    decode_quote,
    parse_klines,
    secid,
)
from financial_intelligence.utils.symbol_mapper import map_symbol

#: Real response body of
#: ``GET https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=...``
REAL_QUOTE_PAYLOAD = {
    "rc": 0,
    "rt": 4,
    "svr": 183640490,
    "lt": 1,
    "full": 1,
    "dlmkts": "",
    "dsc": "0",
    "data": {
        "f43": 139205,
        "f57": "600519",
        "f58": "贵州茅台",
        "f60": 135400,
        "f86": 1793347921,
        "f169": 3805,
        "f170": 281,
    },
}

#: Real ``data.klines`` rows from
#: ``GET https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519&klt=101``
REAL_KLINES = [
    "2025-10-09,1436.00,1436.78,1439.38,1420.00,54918,7845448947.00",
    "2025-10-10,1437.60,1430.00,1439.94,1427.50,36001,5157459262.00",
    "2025-10-13,1415.70,1419.20,1422.85,1415.12,46069,6533147458.00",
]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Keep the cross-process cooldown marker out of the real home directory.
    monkeypatch.setenv("FMI_STATE_DIR", str(tmp_path))
    em.reset_state()
    yield
    em.reset_state()


def _provider(**overrides) -> EastmoneyProvider:
    settings = load_settings(
        {
            "FMI_EASTMONEY_MIN_INTERVAL": str(overrides.pop("min_interval", 0.0)),
            "FMI_EASTMONEY_COOLDOWN": str(overrides.pop("cooldown", 300.0)),
            "FMI_REQUEST_TIMEOUT": "5",
        }
    )
    return EastmoneyProvider(settings)


# ---------------------------------------------------------------------------
# secid mapping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519.SS", "1.600519"),
        ("600519", "1.600519"),
        ("000001.SZ", "0.000001"),
        ("300750.SZ", "0.300750"),
        ("002594.SZ", "0.002594"),
        ("000001.SS", "1.000001"),
        ("399001.SZ", "0.399001"),
        ("000300.SS", "1.000300"),
        ("399006.SZ", "0.399006"),
        ("000688.SS", "1.000688"),
    ],
)
def test_secid_covers_a_share_codes_and_indices(raw: str, expected: str) -> None:
    assert secid(map_symbol(raw)) == expected


@pytest.mark.parametrize("raw", ["0700.HK", "AAPL", "^GSPC", "GC=F"])
def test_secid_rejects_non_china_symbols(raw: str) -> None:
    with pytest.raises(ProviderError) as excinfo:
        secid(map_symbol(raw))
    assert excinfo.value.kind == "unsupported"


def test_supports_only_china_quote_and_history() -> None:
    provider = _provider()
    assert provider.supports_quote(map_symbol("600519.SS")) is True
    assert provider.supports_history(map_symbol("000001.SS")) is True
    assert provider.supports_quote(map_symbol("0700.HK")) is False
    assert provider.supports_history(map_symbol("AAPL")) is False
    assert provider.supports_quote(map_symbol("GC=F")) is False


# ---------------------------------------------------------------------------
# payload decoding
# ---------------------------------------------------------------------------
def test_decode_quote_from_real_payload() -> None:
    decoded = decode_quote(REAL_QUOTE_PAYLOAD["data"])
    assert decoded["code"] == "600519"
    assert decoded["name"] == "贵州茅台"
    assert decoded["price"] == 1392.05
    assert decoded["previous_close"] == 1354.0
    assert decoded["change"] == 38.05
    assert decoded["change_percent"] == 2.81
    assert decoded["timestamp"].isoformat() == "2026-10-30T08:12:01+00:00"


def test_decode_quote_honours_decimal_field() -> None:
    # A 3-decimal instrument would send f59=3; the scale must follow it.
    decoded = decode_quote({"f43": 1234, "f59": 3, "f60": 1000})
    assert decoded["price"] == 1.234
    assert decoded["previous_close"] == 1.0


@pytest.mark.parametrize("value", ["-", None, "", "--"])
def test_decode_quote_missing_price_is_none_not_zero(value: object) -> None:
    decoded = decode_quote({"f43": value, "f57": "600519", "f59": 2})
    assert decoded["price"] is None


def test_parse_klines_uses_eastmoney_column_order() -> None:
    bars = parse_klines(REAL_KLINES)
    assert len(bars) == 3
    first = bars[0]
    assert first["date"] == "2025-10-09"
    # Eastmoney order is date,open,close,high,low,volume — not the usual OHLC
    assert first["open"] == 1436.0
    assert first["close"] == 1436.78
    assert first["high"] == 1439.38
    assert first["low"] == 1420.0
    assert first["volume"] == 54918.0


def test_parse_klines_skips_broken_rows() -> None:
    assert parse_klines(["garbage", "2025-10-09,1.0"]) == []


# ---------------------------------------------------------------------------
# quote / history behaviour with a stubbed HTTP layer
# ---------------------------------------------------------------------------
def test_quote_from_stubbed_payload_is_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_request", lambda url, params, symbol=None: REAL_QUOTE_PAYLOAD)

    quote = provider.get_quote(map_symbol("600519.SS"))

    assert quote.price == 1392.05
    assert quote.previous_close == 1354.0
    assert quote.change == 38.05
    # change_percent is derived from change / previous_close so that every
    # provider reports the same number for the same snapshot (Eastmoney's f170
    # rounds to 2.81).
    assert quote.change_percent == pytest.approx(38.05 / 1354.0 * 100.0)
    assert quote.source == "eastmoney"
    assert quote.currency == "CNY"
    assert quote.data_status.value == "delayed"
    assert "delayed" in (quote.status_note or "")


def test_quote_missing_price_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    payload = {"data": {"f43": "-", "f57": "600519", "f58": "贵州茅台", "f59": 2}}
    monkeypatch.setattr(provider, "_request", lambda url, params, symbol=None: payload)

    quote = provider.get_quote(map_symbol("600519.SS"))

    assert quote.price is None
    assert quote.data_status.value == "unavailable"
    assert quote.error


def test_quote_empty_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_request", lambda url, params, symbol=None: {"rc": 0, "data": None})
    with pytest.raises(ProviderError) as excinfo:
        provider.get_quote(map_symbol("600519.SS"))
    assert excinfo.value.kind == "empty"


def test_history_assembles_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    payload = {"rc": 0, "data": {"code": "600519", "name": "贵州茅台", "klines": REAL_KLINES}}
    monkeypatch.setattr(provider, "_request", lambda url, params, symbol=None: payload)

    history = provider.get_history(map_symbol("600519.SS"), "5y", "1d")

    assert history.source == "eastmoney"
    assert history.name == "贵州茅台"
    assert history.count == 3
    assert history.bars[0].close == 1436.78
    assert history.bars[0].currency == "CNY"


def test_history_without_klines_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_request", lambda url, params, symbol=None: {"data": {}})
    with pytest.raises(ProviderError) as excinfo:
        provider.get_history(map_symbol("600519.SS"), "1mo", "1d")
    assert excinfo.value.kind == "empty"


# ---------------------------------------------------------------------------
# pacing and circuit breaker
# ---------------------------------------------------------------------------
def test_dropped_connection_trips_breaker_and_fails_fast() -> None:
    import httpx

    provider = _provider(cooldown=300.0)

    def boom(*args, **kwargs):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            boom()

    # Only the first call reaches the (fake) network; the second must be blocked.
    provider._client = lambda: FakeClient()  # type: ignore[assignment]

    with pytest.raises(ProviderError) as first:
        provider.get_quote(map_symbol("600519.SS"))
    assert first.value.kind == "rate_limit"
    assert "断开" in str(first.value)
    assert em.BREAKER.is_open is True

    with pytest.raises(ProviderError) as second:
        provider.get_quote(map_symbol("600519.SS"))
    assert second.value.kind == "rate_limit"
    assert "冷却" in str(second.value)
    assert em.BREAKER.last_attempt is not None


def test_breaker_reset_allows_calls_again() -> None:
    em.BREAKER.trip("测试", 300.0)
    assert em.BREAKER.is_open is True
    em.reset_state()
    assert em.BREAKER.is_open is False


def test_breaker_cooldown_survives_a_new_process() -> None:
    # A freshly started CLI/MCP process must not re-hit a throttled endpoint.
    em.BREAKER.trip("RemoteProtocolError: 服务器未响应即断开连接。", 300.0)
    assert em.state_path().exists()

    revived = em._Breaker()
    with pytest.raises(ProviderError) as excinfo:
        revived.check()

    assert excinfo.value.kind == "rate_limit"
    assert "冷却" in str(excinfo.value)
    assert revived.is_open is True


def test_expired_cooldown_file_does_not_block(tmp_path) -> None:
    path = em.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"open_until_epoch": 1, "reason": "old"}', encoding="utf-8")

    revived = em._Breaker()
    revived.check()  # must not raise
    assert revived.is_open is False


def test_pacer_enforces_min_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}
    slept: list[float] = []

    monkeypatch.setattr(em.time, "monotonic", lambda: clock["now"])

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(em.time, "sleep", fake_sleep)

    em.PACER.min_interval = 1.0
    em.PACER.reset()
    em.PACER.wait()  # first request goes out immediately
    assert slept == []

    em.PACER.wait()  # second request is spaced by min_interval
    assert slept == [pytest.approx(1.0)]


def test_timeout_is_reported_not_retried() -> None:
    import httpx

    provider = _provider()

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    provider._client = lambda: FakeClient()  # type: ignore[assignment]
    with pytest.raises(ProviderError) as excinfo:
        provider.get_quote(map_symbol("600519.SS"))
    assert excinfo.value.kind == "timeout"
    assert em.BREAKER.is_open is False


def test_non_200_is_reported_as_http_error() -> None:
    provider = _provider()

    class FakeResponse:
        status_code = 503
        text = "service unavailable"

        def json(self):
            raise AssertionError("must not parse a non-200 body")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            return FakeResponse()

    provider._client = lambda: FakeClient()  # type: ignore[assignment]
    with pytest.raises(ProviderError) as excinfo:
        provider.get_quote(map_symbol("600519.SS"))
    assert excinfo.value.kind == "http"
    assert "503" in str(excinfo.value)
