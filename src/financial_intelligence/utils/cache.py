"""Small, dependency-free caching helpers.

Two layers are used deliberately:

* :class:`TTLCache` — in-process, used by services so several MCP tools in one
  agent turn never hit the same upstream endpoint twice.
* :class:`DiskCache` — JSON files under ``FMI_CACHE_DIR`` (default ``~/.fmi/cache``)
  so a fresh process (or the next agent turn) reuses filings that change at most
  once a quarter.  Every entry keeps the time it was fetched; cached financial
  data is always reported as historical, never as real-time.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    env = os.environ.get("FMI_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".fmi" / "cache"


class TTLCache:
    """Tiny in-memory TTL cache keyed by string."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float) -> Any | None:
        if ttl <= 0:
            self._store.pop(key, None)
            return None
        hit = self._store.get(key)
        if hit is None:
            return None
        stored_at, value = hit
        if (time.monotonic() - stored_at) > ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class DiskCache:
    """JSON file cache with per-entry fetch timestamps."""

    def __init__(self, root: Path | None = None, *, enabled: bool = True) -> None:
        self.root = Path(root) if root is not None else default_cache_dir()
        self.enabled = enabled

    def _path(self, namespace: str, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
        return self.root / namespace / f"{safe}.json"

    def get(self, namespace: str, key: str, ttl: float) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        try:
            if not path.exists():
                return None
            if ttl > 0 and (time.time() - path.stat().st_mtime) > ttl:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or "value" not in payload:
            return None
        return payload["value"]

    def set(self, namespace: str, key: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self._path(namespace, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"fetched_at": time.time(), "fetched_at_iso": _now_iso(), "value": value},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError):
            # A cache write must never break a request.
            return

    def fetched_at(self, namespace: str, key: str) -> str | None:
        path = self._path(namespace, key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload.get("fetched_at_iso") if isinstance(payload, dict) else None

    def clear(self) -> int:
        removed = 0
        if not self.root.exists():
            return 0
        for path in self.root.rglob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed


def _now_iso() -> str:
    from ..schemas import iso, utc_now

    return iso(utc_now()) or ""
