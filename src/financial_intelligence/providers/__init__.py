"""Provider registry.

Providers are constructed lazily so that server start-up never imports
``akshare``/``yfinance`` (both pull heavy dependency trees) and never touches
the network.
"""

from __future__ import annotations

from ..config import Settings
from .base import (
    FinancialDataProvider,
    MarketDataProvider,
    ProviderError,
    assemble_history,
    call_with_timeout,
)
from .eastmoney import EastmoneyProvider
from .sina import SinaProvider

__all__ = [
    "EastmoneyProvider",
    "FinancialDataProvider",
    "MarketDataProvider",
    "ProviderError",
    "SinaProvider",
    "assemble_history",
    "build_financial_providers",
    "build_providers",
    "call_with_timeout",
]


def build_providers(settings: Settings) -> list[MarketDataProvider]:
    """Instantiate the enabled providers in the configured order."""
    providers: list[MarketDataProvider] = []
    for name in settings.enabled_providers():
        if name == "sina":
            providers.append(SinaProvider(settings))
        elif name == "eastmoney":
            from .eastmoney import EastmoneyProvider

            providers.append(EastmoneyProvider(settings))
        elif name == "akshare":
            from .akshare import AkshareProvider

            providers.append(AkshareProvider(settings))
        elif name in {"yahoo", "yfinance"}:
            from .yahoo import YahooProvider

            providers.append(YahooProvider(settings))
        else:
            # Unknown names in FMI_PROVIDER_ORDER are ignored rather than fatal.
            continue
    return providers


def build_financial_providers(settings: Settings) -> list[FinancialDataProvider]:
    """Instantiate the enabled *statement* providers.

    ``FMI_FINANCIAL_PROVIDER_ORDER`` controls the order (default: SEC first for
    US companies, then the AKShare mirrors used for A-share and Hong Kong
    filings).  Disabled data sources are skipped, exactly like quotes.
    """
    providers: list[FinancialDataProvider] = []
    for name in settings.financial_provider_order:
        if name == "sec":
            if not settings.enable_sec:
                continue
            from .sec import SecFinancialProvider

            providers.append(SecFinancialProvider(settings))
        elif name in {"akshare", "cninfo", "hkex"}:
            # A-share / HK statements come from AKShare's verified mirrors of the
            # exchange filings.  ``cninfo``/``hkex`` are accepted as aliases so a
            # configuration written against the task document still works, but
            # they all resolve to the same AKShare-backed provider.
            if not settings.enable_akshare:
                continue
            from .akshare import AkshareFinancialProvider

            providers.append(AkshareFinancialProvider(settings))
        else:
            continue
    return providers
