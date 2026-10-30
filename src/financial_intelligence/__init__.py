"""Financial Market Intelligence — a lightweight, free-data market data MCP server.

The public surface is the MCP tool layer in
:mod:`financial_intelligence.server`; everything else (providers, services,
schemas) is import-safe and side-effect free so it can be unit tested without a
network connection.

Guiding principles baked into the code:

* Free public data only — no paid keys, no databases, no front end.
* Never fabricate a number: missing data is ``None`` plus an explicit reason.
* Never present delayed vendor data as real-time.
* Never present a futures contract price as a spot price.
* Not investment advice.
"""

from __future__ import annotations

from .config import DISPLAY_NAME, PACKAGE_NAME, SUBTITLE, VERSION

__all__ = ["DISPLAY_NAME", "PACKAGE_NAME", "SUBTITLE", "VERSION", "__version__"]

__version__ = VERSION
