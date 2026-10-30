"""Provider registry.

Providers are constructed lazily so that server start-up never imports
``akshare``/``yfinance`` (both pull heavy dependency trees) and never touches
the network.
"""

from __future__ import annotations

from ..config import Settings
from .base import MarketDataProvider, ProviderError, assemble_history, call_with_timeout
from .eastmoney import EastmoneyProvider
from .sina import SinaProvider

__all__ = [
    "EastmoneyProvider",
    "MarketDataProvider",
    "ProviderError",
    "SinaProvider",
    "assemble_history",
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
