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


_DEFAULT_ENDPOINTS = {
    "submit_order": "/orders",
    "order_status": "/orders/{order_id}",
    "cancel_order": "/orders/{order_id}/cancel",
    "orderbook": "/book?market={market_id}",
    "positions": "/positions",
}

_PROFILE_ENDPOINTS = {
    "default": dict(_DEFAULT_ENDPOINTS),
    "legacy_v1": {
        "submit_order": "/order",
        "order_status": "/order/{order_id}",
        "cancel_order": "/order/{order_id}/cancel",
        "orderbook": "/orderbook/{market_id}",
        "positions": "/account/positions",
    },
}

_DEFAULT_FIELD_MAP = {
    "order": {
        "order_id": ["order_id", "id", "client_order_id"],
        "status": ["status"],
    },
    "positions": {
        "rows": ["positions"],
        "market_id": ["market_id", "marketId"],
        "yes_qty": ["yes_qty", "yesQty", "long_qty"],
        "no_qty": ["no_qty", "noQty", "short_qty"],
    },
    "orderbook": {
        "container": ["book"],
        "yes_bids": ["yes_bids", "bids"],
        "yes_asks": ["yes_asks", "asks"],
        "no_bids": ["no_bids"],
        "no_asks": ["no_asks"],
        "price": ["price", "px"],
        "size": ["size", "quantity", "qty"],
    },
}

_PROFILE_FIELD_MAP = {
    "default": dict(_DEFAULT_FIELD_MAP),
    "legacy_v1": {
        "order": {
            "order_id": ["id", "order_id", "client_order_id", "clientOrderId", "clientOid"],
            "status": ["state", "status"],
        },
        "positions": {
            "rows": ["positions", "items"],
            "market_id": ["market", "market_id", "marketId", "token_id"],
            "yes_qty": ["long", "yes_qty", "yesQty", "long_qty", "qty"],
            "no_qty": ["short", "no_qty", "noQty", "short_qty"],
        },
        "orderbook": {
            "container": ["book", "orderbook"],
            "yes_bids": ["bids", "yes_bids", "buy"],
            "yes_asks": ["asks", "yes_asks", "sell"],
            "no_bids": ["no_bids", "buy_no"],
            "no_asks": ["no_asks", "sell_no"],
            "price": ["price", "px"],
            "size": ["size", "quantity", "qty", "sz"],
        },
    },
}


def _deep_copy_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _deep_copy_mapping(v) for k, v in value.items()}
    if isinstance(value, list):
        return [v for v in value]
    return value


def _merge_mapping(base: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    out = _deep_copy_mapping(base)
    for key, val in custom.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_mapping(out[key], val)
            continue
        if isinstance(val, (list, tuple)):
            out[key] = [str(x).strip() for x in val if str(x).strip()]
            continue
        if isinstance(val, str):
            v = val.strip()
            out[key] = [v] if v else []
            continue
        out[key] = val
    return out


def load_clob_endpoints(config: dict[str, Any]) -> CLOBEndpoints:
    base = str(config.get("apis", {}).get("clob_base", "")).rstrip("/")
    profile_name = str(config.get("apis", {}).get("clob_profile", "default")).strip().lower()
    default = dict(_PROFILE_ENDPOINTS.get(profile_name, _PROFILE_ENDPOINTS["default"]))
    for k, v in _DEFAULT_ENDPOINTS.items():
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


def load_clob_field_map(config: dict[str, Any]) -> dict[str, Any]:
    profile_name = str(config.get("apis", {}).get("clob_profile", "default")).strip().lower()
    base = _deep_copy_mapping(_PROFILE_FIELD_MAP.get(profile_name, _PROFILE_FIELD_MAP["default"]))
    custom = config.get("apis", {}).get("clob_field_map", {}) or {}
    if not isinstance(custom, dict):
        return base
    return _merge_mapping(base, custom)
