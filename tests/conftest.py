"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run anyio-based tests on asyncio only (no trio dependency)."""
    return "asyncio"
