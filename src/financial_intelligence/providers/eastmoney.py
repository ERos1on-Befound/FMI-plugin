"""Eastmoney (东方财富) provider for China A-shares and A-share indices.

Background (measured on 2026-10-30, see ``docs/eastmoney-diagnosis.md``):
Eastmoney's ``push2*`` edge servers do **not** answer with ``429 Too Many
Requests``.  When it decides a client has asked too often it *closes the
connection without a response* (``RemoteDisconnected`` / ``RemoteProtocolError``),
and every further attempt during the cooldown extends the cooldown.  There is no
``Retry-After`` header to obey and no status code to log.

Consequences that shaped this module:

* **Single-symbol endpoints only.**  The paginated bulk endpoints
  (``*push2*.eastmoney.com/api/qt/clist/get`` — what ``stock_zh_a_spot_em`` uses)
  page through thousands of rows and are what trips the limit.  This provider
  never calls them: one quote = one request.
* **No blind retries.**  Retrying a dropped connection is exactly the behaviour
  that lengthens the block, so a dropped connection immediately opens a circuit
  breaker instead.
* **Pacing.**  Requests from this provider are serialised with a minimum gap
  (``FMI_EASTMONEY_MIN_INTERVAL``, default 1.0 s).
* **Honest status.**  A ``push2`` snapshot proves neither exchange directness nor
  realtime, so quotes are always ``delayed`` (never ``realtime``), and a tripped
  breaker reports ``unavailable`` with the concrete reason.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..schemas import DataStatus, HistoricalData, Quote, iso, utc_now
from ..utils.bars import PERIOD_DAYS
from ..utils.symbol_mapper import SymbolSpec
from ..utils.validators import to_float
from .base import MarketDataProvider, ProviderError, assemble_history, call_with_timeout

logger = logging.getLogger(__name__)

QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

#: A股交易所 -> 东财 secid 市场号（沪市=1，深市/北交所=0）。
_SECID_MARKET = {"sh": "1", "sz": "0", "bj": "0"}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FMI/0.1.0"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

QUOTE_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86,f169,f170"
KLINE_FIELDS1 = "f1,f2,f3,f4,f5,f6"
KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"

_SNAPSHOT_NOTE = (
    "东方财富 push2 公开行情快照；该接口不提供实时授权证明，统一标记为 delayed。"
)
_THROTTLE_HINT = (
    "东财 push2 在短时间内请求过多时不会返回 429，而是直接断开连接（无 HTTP 状态码）。"
    "已按官方提示进入冷却：冷却期内不要重复请求，反复重试只会延长限流。"
)

_STATE_FILENAME = "eastmoney_state.json"


def state_path() -> Path:
    """Location of the cross-process cooldown marker.

    ``FMI_STATE_DIR`` overrides the directory (the test suite points it at a
    temporary folder); the default is ``~/.fmi``.
    """
    override = os.environ.get("FMI_STATE_DIR")
    base = Path(override) if override else Path.home() / ".fmi"
    return base / _STATE_FILENAME


# ---------------------------------------------------------------------------
# Process-wide pacing + circuit breaker
# ---------------------------------------------------------------------------
class _Pacer:
    """Serialise requests and enforce a minimum gap between them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0.0
        self.min_interval = 1.0

    def wait(self) -> None:
        with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._last = 0.0


class _Breaker:
    """Open the breaker when Eastmoney drops the connection, then fail fast.

    The cooldown is persisted to a small JSON file so that a *new* process (a
    CLI script, a fresh MCP host, ``python -m financial_intelligence.server``)
    does not immediately re-hit an endpoint that was just throttled — that is
    exactly what the official notice says will lengthen the block.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self.open_until = 0.0
        self.reason: str | None = None
        self.last_attempt: str | None = None

    # -- persistence ------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = state_path()
        try:
            if not path.exists():
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            remaining = float(payload.get("open_until_epoch", 0.0)) - time.time()
        except (TypeError, ValueError):
            return
        if remaining > 0:
            self.open_until = time.monotonic() + remaining
            self.reason = str(payload.get("reason") or "上一次进程记录的限流")
            self.last_attempt = str(payload.get("last_attempt") or "") or None
            logger.warning(
                "eastmoney breaker restored from %s: %ss of cooldown remaining",
                path,
                int(remaining),
            )

    def _persist(self, cooldown: float) -> None:
        path = state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "open_until_epoch": time.time() + cooldown,
                        "reason": self.reason,
                        "last_attempt": self.last_attempt,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("could not persist eastmoney breaker state to %s", path, exc_info=True)

    # -- public API -------------------------------------------------------
    def check(self) -> None:
        self._ensure_loaded()
        with self._lock:
            remaining = self.open_until - time.monotonic()
            reason = self.reason
            last = self.last_attempt
        if remaining > 0:
            raise ProviderError(
                "eastmoney",
                f"冷却中（剩余约 {remaining:.0f} 秒），不发起请求。触发原因：{reason}。"
                f"最后一次尝试时间：{last}。{_THROTTLE_HINT}",
                kind="rate_limit",
            )

    def trip(self, reason: str, cooldown: float) -> None:
        with self._lock:
            self.open_until = time.monotonic() + cooldown
            self.reason = reason
            self.last_attempt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            snapshot = (self.reason, self.last_attempt)
        self._persist(cooldown)
        logger.warning(
            "eastmoney circuit breaker tripped for %.0fs: %s (%s)",
            cooldown,
            snapshot[0],
            snapshot[1],
        )

    def reset(self) -> None:
        with self._lock:
            self._loaded = True
            self.open_until = 0.0
            self.reason = None
            self.last_attempt = None
        try:
            state_path().unlink()
        except OSError:
            pass

    @property
    def is_open(self) -> bool:
        self._ensure_loaded()
        return self.open_until > time.monotonic()


PACER = _Pacer()
BREAKER = _Breaker()


def reset_state() -> None:
    """Reset pacing/breaker state (used by tests)."""
    PACER.reset()
    BREAKER.reset()


# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------
def secid(spec: SymbolSpec) -> str:
    """Return the Eastmoney ``secid`` (``"1.600519"``) for a China symbol."""
    if spec.market.value != "CN":
        raise ProviderError(
            "eastmoney",
            f"本数据源只覆盖中国内地市场（A股/指数）；{spec.symbol} 属于 {spec.market.value}。",
            kind="unsupported",
            symbol=spec.symbol,
        )
    code = spec.akshare_code
    exchange = (spec.akshare_exchange or "").lower()
    market = _SECID_MARKET.get(exchange)
    if not code or market is None:
        raise ProviderError(
            "eastmoney", f"无法解析 A股代码 {spec.symbol} 的交易所。", kind="unsupported", symbol=spec.symbol
        )
    return f"{market}.{code}"


# ---------------------------------------------------------------------------
# Payload decoding
# ---------------------------------------------------------------------------
def _scaled(value: Any, digits: int) -> float | None:
    """Eastmoney sends prices as integers scaled by ``10**digits``."""
    raw = to_float(value)
    if raw is None:
        return None
    return round(raw / (10**digits), digits)


def decode_quote(data: dict[str, Any]) -> dict[str, Any]:
    """Decode the ``data`` object of ``/api/qt/stock/get`` into plain values."""
    digits = int(to_float(data.get("f59")) or 2)
    timestamp = None
    epoch = to_float(data.get("f86"))
    if epoch:
        timestamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return {
        "code": str(data.get("f57") or "").strip() or None,
        "name": (str(data.get("f58") or "").strip() or None),
        "price": _scaled(data.get("f43"), digits),
        "open": _scaled(data.get("f46"), digits),
        "high": _scaled(data.get("f44"), digits),
        "low": _scaled(data.get("f45"), digits),
        "previous_close": _scaled(data.get("f60"), digits),
        "change": _scaled(data.get("f169"), digits),
        "change_percent": _scaled(data.get("f170"), 2),
        "volume": to_float(data.get("f47")),
        "amount": to_float(data.get("f48")),
        "timestamp": timestamp,
    }


def parse_klines(klines: list[str]) -> list[dict[str, Any]]:
    """Parse ``data.klines`` (``"date,open,close,high,low,volume,amount"``)."""
    bars: list[dict[str, Any]] = []
    for row in klines:
        parts = str(row).split(",")
        if len(parts) < 6:
            continue
        bar = {
            "date": parts[0].strip(),
            # NOTE: Eastmoney puts close before high/low.
            "open": to_float(parts[1]),
            "close": to_float(parts[2]),
            "high": to_float(parts[3]),
            "low": to_float(parts[4]),
            "volume": to_float(parts[5]),
        }
        if bar["close"] is None and bar["open"] is None:
            continue
        bars.append(bar)
    return bars


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class EastmoneyProvider(MarketDataProvider):
    """A-share quote + daily history straight from Eastmoney's public JSON API."""

    name = "eastmoney"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        PACER.min_interval = float(getattr(settings, "eastmoney_min_interval", 1.0))

    # -- capability ------------------------------------------------------
    def supports_quote(self, spec: SymbolSpec) -> bool:
        return spec.market.value == "CN" and spec.asset_type.value in {"stock", "index"}

    def supports_history(self, spec: SymbolSpec) -> bool:
        return self.supports_quote(spec)

    # -- HTTP ------------------------------------------------------------
    def _client(self) -> httpx.Client:
        # ``local_address="0.0.0.0"`` pins the connection to IPv4: the AAAA path
        # to Eastmoney dropped connections even when the A record answered.
        return httpx.Client(
            timeout=self.settings.request_timeout,
            headers=_HEADERS,
            follow_redirects=True,
            transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=0),
        )

    def _request(self, url: str, params: dict[str, str], symbol: str | None = None) -> dict[str, Any]:
        """One single request: paced, breaker-guarded, never retried."""
        BREAKER.check()
        PACER.wait()
        try:
            with self._client() as client:
                response = client.get(url, params=params)
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as exc:
            cooldown = float(getattr(self.settings, "eastmoney_cooldown", 300.0))
            BREAKER.trip(f"{exc.__class__.__name__}: 服务器未响应即断开连接", cooldown)
            raise ProviderError(
                "eastmoney",
                f"请求 {url} 时连接被服务端断开（{exc.__class__.__name__}），"
                f"没有 HTTP 状态码可记录；判定为疑似 IP 限流，已进入 {cooldown:.0f} 秒冷却。{_THROTTLE_HINT}",
                kind="rate_limit",
                symbol=symbol,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "eastmoney",
                f"请求 {url} 超时（{self.settings.request_timeout:.0f} 秒），数据源未在限定时间内返回。",
                kind="timeout",
                symbol=symbol,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "eastmoney",
                f"请求 {url} 失败（{exc.__class__.__name__}）：{exc}",
                kind="network",
                symbol=symbol,
            ) from exc

        if response.status_code != 200:
            raise ProviderError(
                "eastmoney",
                f"{url} 返回 HTTP {response.status_code}；响应片段：{response.text[:200]}",
                kind="http",
                symbol=symbol,
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(
                "eastmoney",
                f"{url} 返回的不是 JSON（前 200 字符：{response.text[:200]}）。",
                kind="parse",
                symbol=symbol,
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError("eastmoney", f"{url} 返回结构异常：{type(payload).__name__}", kind="parse", symbol=symbol)
        return payload

    # -- quote -----------------------------------------------------------
    def get_quote(self, spec: SymbolSpec) -> Quote:
        code = secid(spec)
        params = {"secid": code, "fields": QUOTE_FIELDS, "invt": "2", "fltt": "1"}
        payload = call_with_timeout(
            lambda: self._request(QUOTE_URL, params, spec.symbol),
            max(self.settings.request_timeout, 5.0),
            self.name,
            symbol=spec.symbol,
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise ProviderError(
                "eastmoney",
                f"{QUOTE_URL} 未返回 {code} 的行情（data 为空）；代码可能不存在、已退市或停牌。",
                kind="empty",
                symbol=spec.symbol,
            )

        decoded = decode_quote(data)
        retrieved = utc_now()
        price = decoded["price"]
        timestamp = decoded["timestamp"]

        status = DataStatus.DELAYED
        error = None
        if price is None or price == 0:
            status = DataStatus.UNAVAILABLE
            price = None
            error = "东财返回的最新价为空或为 0（可能停牌、代码错误或该时段无成交）。"
        elif timestamp is None:
            status = DataStatus.UNKNOWN
        elif (retrieved - timestamp).total_seconds() > self.settings.stale_after:
            status = DataStatus.STALE

        quote = Quote(
            symbol=spec.symbol,
            name=spec.name_hint or decoded["name"],
            market=spec.market,
            asset_type=spec.asset_type,
            currency=spec.currency,
            price=price,
            previous_close=decoded["previous_close"],
            timestamp=iso(timestamp),
            retrieved_at=iso(retrieved) or "",
            source=self.name,
            data_status=status,
            error=error,
            status_note=_SNAPSHOT_NOTE,
        )
        return quote.with_derived()

    # -- history ---------------------------------------------------------
    def get_history(self, spec: SymbolSpec, period: str, interval: str) -> HistoricalData:
        code = secid(spec)
        days = PERIOD_DAYS.get(period, 366)
        beg = (date.today() - timedelta(days=days + 15)).strftime("%Y%m%d")
        params = {
            "secid": code,
            "fields1": KLINE_FIELDS1,
            "fields2": KLINE_FIELDS2,
            "klt": "101",
            "fqt": "0",
            "beg": beg,
            "end": date.today().strftime("%Y%m%d"),
            "lmt": "10000",
        }
        payload = call_with_timeout(
            lambda: self._request(KLINE_URL, params, spec.symbol),
            max(self.settings.request_timeout, 5.0),
            self.name,
            symbol=spec.symbol,
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("klines"):
            raise ProviderError(
                "eastmoney",
                f"{KLINE_URL} 未返回 {code} 的K线（data.klines 为空）。",
                kind="empty",
                symbol=spec.symbol,
            )

        bars = parse_klines(list(data["klines"]))
        if not bars:
            raise ProviderError(
                "eastmoney",
                f"{code} 的K线全部解析失败（原始 {len(data['klines'])} 行）。",
                kind="parse",
                symbol=spec.symbol,
            )
        name = spec.name_hint or (str(data.get("name") or "").strip() or None)
        return assemble_history(
            spec,
            bars,
            period=period,
            interval=interval,
            source=self.name,
            settings=self.settings,
            name=name,
            status_note=_SNAPSHOT_NOTE,
        )
