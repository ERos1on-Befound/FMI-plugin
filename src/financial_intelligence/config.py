"""Runtime configuration for Financial Market Intelligence.

All settings can be overridden with ``FMI_``-prefixed environment variables.
The MVP requires no API keys or paid credentials: every data source used here
is a free, public interface.

Important: this module must never write to ``stdout``.  When the MCP server is
served over stdio, ``stdout`` is the protocol wire and anything else printed
there corrupts the stream.  Logging therefore always goes to ``stderr``.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Mapping

PACKAGE_NAME = "financial-market-intelligence"
DISPLAY_NAME = "Financial Market Intelligence"
SUBTITLE = "A Lightweight Financial Market Data Retrieval & Research Skill"
VERSION = "0.1.0"

#: Supported history windows, in the order they are advertised.
SUPPORTED_PERIODS: tuple[str, ...] = ("1mo", "3mo", "6mo", "1y", "2y", "5y")
#: Supported history intervals.
SUPPORTED_INTERVALS: tuple[str, ...] = ("1d", "1wk", "1mo")

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_FALSY = {"0", "false", "no", "off", "n", "f"}


def _env_str(env: Mapping[str, str], key: str, default: str) -> str:
    raw = env.get(key)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


def _env_order(env: Mapping[str, str], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    items = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return items or default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the FMI server."""

    server_name: str = PACKAGE_NAME
    display_name: str = DISPLAY_NAME
    subtitle: str = SUBTITLE
    version: str = VERSION

    # --- network ---
    request_timeout: float = 10.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FMI/0.1.0"
    )

    # --- caches (seconds) ---
    cache_ttl: float = 60.0
    history_cache_ttl: float = 300.0

    # --- history limits ---
    max_history_rows: int = 1500

    # --- providers ---
    #: Providers tried, in order, for every request.  A provider that cannot
    #: serve a symbol is skipped, and a provider that fails falls through to the
    #: next one (the failure reasons are surfaced in ``Quote.error``).
    enable_sina: bool = True
    enable_eastmoney: bool = True
    enable_akshare: bool = True
    enable_yfinance: bool = True
    provider_order: tuple[str, ...] = ("sina", "eastmoney", "akshare", "yahoo")

    # --- eastmoney pacing / breaker ---
    #: Minimum gap between two Eastmoney requests (seconds).  Eastmoney drops
    #: connections instead of returning 429, so pacing is the main defence.
    eastmoney_min_interval: float = 1.0
    #: How long to stop calling Eastmoney after a dropped connection (seconds).
    eastmoney_cooldown: float = 300.0

    # --- data freshness thresholds (seconds) ---
    #: A quote whose market timestamp is older than this is reported as ``stale``.
    stale_after: float = 10 * 24 * 3600.0

    # --- logging ---
    log_level: str = "INFO"

    notes: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    def enabled_providers(self) -> tuple[str, ...]:
        """Return the configured provider order, filtered by the enable flags."""
        flags = {
            "sina": self.enable_sina,
            "eastmoney": self.enable_eastmoney,
            "akshare": self.enable_akshare,
            "yahoo": self.enable_yfinance,
            "yfinance": self.enable_yfinance,
        }
        return tuple(name for name in self.provider_order if flags.get(name, True))


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``os.environ`` (or the supplied mapping)."""
    source: Mapping[str, str] = os.environ if env is None else env
    return Settings(
        request_timeout=_env_float(source, "FMI_REQUEST_TIMEOUT", 10.0),
        cache_ttl=_env_float(source, "FMI_CACHE_TTL", 60.0),
        history_cache_ttl=_env_float(source, "FMI_HISTORY_CACHE_TTL", 300.0),
        max_history_rows=_env_int(source, "FMI_MAX_HISTORY_ROWS", 1500),
        enable_sina=_env_bool(source, "FMI_ENABLE_SINA", True),
        enable_eastmoney=_env_bool(source, "FMI_ENABLE_EASTMONEY", True),
        enable_akshare=_env_bool(source, "FMI_ENABLE_AKSHARE", True),
        enable_yfinance=_env_bool(source, "FMI_ENABLE_YFINANCE", True),
        provider_order=_env_order(
            source, "FMI_PROVIDER_ORDER", ("sina", "eastmoney", "akshare", "yahoo")
        ),
        eastmoney_min_interval=_env_float(source, "FMI_EASTMONEY_MIN_INTERVAL", 1.0),
        eastmoney_cooldown=_env_float(source, "FMI_EASTMONEY_COOLDOWN", 300.0),
        stale_after=_env_float(source, "FMI_STALE_AFTER_DAYS", 10.0) * 86400.0,
        log_level=_env_str(source, "FMI_LOG_LEVEL", "INFO").upper(),
    )


_LOG_CONFIGURED = False


def configure_logging(settings: Settings | None = None) -> logging.Logger:
    """Configure logging so that every record goes to ``stderr`` only.

    ``stdout`` is reserved for the MCP stdio transport, so we never attach a
    handler to it.  Safe to call more than once.
    """
    global _LOG_CONFIGURED
    settings = settings or load_settings()
    logger = logging.getLogger("financial_intelligence")

    if not _LOG_CONFIGURED:
        root = logging.getLogger()
        level = getattr(logging, settings.log_level, logging.INFO)
        root.setLevel(level)
        # Drop handlers that could write to stdout (e.g. added by a host app).
        for handler in list(root.handlers):
            stream = getattr(handler, "stream", None)
            if stream is sys.stdout:
                root.removeHandler(handler)
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        if not root.handlers:
            root.addHandler(handler)
        _LOG_CONFIGURED = True

    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    logger.propagate = True
    return logger
