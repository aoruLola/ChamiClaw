from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CLOBEndpoints:
    submit_order: str
    order_status: str
    cancel_order: str
    orderbook: str
    positions: str


def load_clob_endpoints(config: dict[str, Any]) -> CLOBEndpoints:
    base = str(config.get("apis", {}).get("clob_base", "")).rstrip("/")
    profiles = {
        "default": {
            "submit_order": "/orders",
            "order_status": "/orders/{order_id}",
            "cancel_order": "/orders/{order_id}/cancel",
            "orderbook": "/book?market={market_id}",
            "positions": "/positions",
        },
        "legacy_v1": {
            "submit_order": "/order",
            "order_status": "/order/{order_id}",
            "cancel_order": "/order/{order_id}/cancel",
            "orderbook": "/orderbook/{market_id}",
            "positions": "/account/positions",
        },
    }
    profile_name = str(config.get("apis", {}).get("clob_profile", "default")).strip().lower()
    default = dict(profiles.get(profile_name, profiles["default"]))
    fallback = {
        "submit_order": "/orders",
        "order_status": "/orders/{order_id}",
        "cancel_order": "/orders/{order_id}/cancel",
        "orderbook": "/book?market={market_id}",
        "positions": "/positions",
    }
    for k, v in fallback.items():
        default.setdefault(k, v)
    custom = dict(config.get("apis", {}).get("clob_endpoints", {}) or {})
    merged = {**default, **custom}
    # keep path only; callers append to base_url
    for k, v in list(merged.items()):
        val = str(v).strip()
        if val.startswith(base):
            val = val[len(base) :]
        if not val.startswith("/"):
            val = "/" + val
        merged[k] = val
    return CLOBEndpoints(
        submit_order=merged["submit_order"],
        order_status=merged["order_status"],
        cancel_order=merged["cancel_order"],
        orderbook=merged["orderbook"],
        positions=merged["positions"],
    )
