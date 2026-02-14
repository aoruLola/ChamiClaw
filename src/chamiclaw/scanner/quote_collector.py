from __future__ import annotations

import math
from statistics import pstdev
from typing import Any

from chamiclaw.utils.time import utc_now_iso


def _synthetic_spread_bps(yes_price: float) -> float:
    # Fallback synthetic spread until direct orderbook integration.
    synthetic_spread = max(0.005, min(0.05, 0.015 + abs(0.5 - yes_price) * 0.02))
    return synthetic_spread * 10_000


def build_quote(
    market: dict[str, Any],
    recent_yes_mids: list[float],
    orderbook: dict[str, Any] | None = None,
) -> dict[str, Any]:
    yes_price = float(market["outcome_prices"][0])
    no_price = float(market["outcome_prices"][1])

    if orderbook:
        yes_bid = float(orderbook.get("yes_bid", yes_price))
        yes_ask = float(orderbook.get("yes_ask", yes_price))
        no_bid = float(orderbook.get("no_bid", no_price))
        no_ask = float(orderbook.get("no_ask", no_price))
        depth_usd = float(orderbook.get("depth_usd", 0.0))
        spread_bps = max(0.0, (yes_ask - yes_bid) * 10_000)
        source = "clob_orderbook"
    else:
        spread_bps = _synthetic_spread_bps(yes_price)
        yes_bid = max(0.0, yes_price - spread_bps / 20_000)
        yes_ask = min(1.0, yes_price + spread_bps / 20_000)
        no_bid = max(0.0, no_price - spread_bps / 20_000)
        no_ask = min(1.0, no_price + spread_bps / 20_000)
        depth_usd = float(market.get("liquidity_usd", 0)) * 0.1
        source = "synthetic_from_gamma"

    sigma_5m = 0.0
    if len(recent_yes_mids) >= 2:
        sigma_5m = pstdev(recent_yes_mids[-20:])

    imbalance = 0.0
    if yes_price + no_price > 0:
        imbalance = (yes_price - no_price) / max(1e-9, yes_price + no_price)

    return {
        "market_id": market["market_id"],
        "ts_utc": utc_now_iso(),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_mid": yes_price,
        "no_mid": no_price,
        "spread_bps": spread_bps,
        "depth_usd": depth_usd,
        "depth_imbalance": imbalance,
        "sigma_5m": sigma_5m,
        "raw": {"source": source},
    }
