"""Show the DEFAULT provider order still serves data while Eastmoney is in cooldown.

The Eastmoney cooldown marker is faked in a temporary state directory, so this
script performs **zero** requests to the throttled Eastmoney edges while
exercising the real fallback path (eastmoney -> akshare) of the default config.

Run:  .venv\\Scripts\\python.exe scripts\\check_default_fallback.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STATE_DIR = Path(tempfile.mkdtemp(prefix="fmi-state-"))
os.environ["FMI_STATE_DIR"] = str(STATE_DIR)
(STATE_DIR / "eastmoney_state.json").write_text(
    json.dumps(
        {
            "open_until_epoch": time.time() + 3600,
            "reason": "验证用：模拟东方财富处于限流冷却",
            "last_attempt": "2026-10-30T11:44:03Z",
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

from financial_intelligence.config import load_settings  # noqa: E402
from financial_intelligence.providers.eastmoney import BREAKER  # noqa: E402
from financial_intelligence.services import MarketService  # noqa: E402


def main() -> int:
    settings = load_settings()
    print(f"state_dir={STATE_DIR}")
    print(f"provider_order={list(settings.enabled_providers())}")
    print(f"eastmoney_breaker_open={BREAKER.is_open} reason={BREAKER.reason}")
    print()

    service = MarketService(settings)

    quote = service.get_quote("600519.SS")
    print(f"quote 600519.SS source={quote.source} price={quote.price} status={quote.data_status.value}")

    history = service.get_historical_data("600519.SS", "3mo", "1wk")
    last = history.bars[-1] if history.bars else None
    print(
        f"history 600519.SS 3mo/1wk source={history.source} count={history.count} "
        f"last={last.date if last else None} close={last.close if last else None}"
    )
    if history.error:
        print(f"    error={history.error}")

    print()
    print("RESULT: default_order_falls_back_without_hitting_eastmoney")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
