from __future__ import annotations

from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def unwrap_payload(payload: Any) -> Any:
    cur = payload
    for key in ("data", "result", "payload", "response"):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
    return cur


def normalize_order_status(status: str) -> str:
    s = str(status or "").strip().lower()
    if s in {"new", "created"}:
        return "new"
    if s in {"submitted", "open", "working"}:
        return "submitted"
    if s in {"partial", "partially_filled"}:
        return "partial"
    if s in {"filled", "done"}:
        return "filled"
    if s in {"canceled", "cancelled"}:
        return "canceled"
    if s in {"rejected", "failed"}:
        return "rejected"
    return "submitted"


def parse_order_response(payload: dict[str, Any]) -> dict[str, Any]:
    payload = unwrap_payload(payload)
    if not isinstance(payload, dict):
        payload = {}
    return {
        "order_id": str(payload.get("order_id") or payload.get("id") or payload.get("client_order_id") or ""),
        "status": normalize_order_status(str(payload.get("status", "submitted"))),
    }


def parse_positions_response(payload: Any) -> dict[str, dict[str, float]]:
    rows = unwrap_payload(payload)
    if isinstance(rows, dict) and "positions" in rows:
        rows = rows["positions"]
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        market_id = str(row.get("market_id") or row.get("marketId") or "")
        if not market_id:
            continue
        out[market_id] = {
            "yes_qty": _to_float(row.get("yes_qty", row.get("yesQty", row.get("long_qty", 0.0))), 0.0),
            "no_qty": _to_float(row.get("no_qty", row.get("noQty", row.get("short_qty", 0.0))), 0.0),
        }
    return out


def parse_orderbook_response(payload: Any) -> dict[str, float] | None:
    book = unwrap_payload(payload)
    if isinstance(book, dict) and "book" in book:
        book = book["book"]
    if not isinstance(book, dict):
        return None
    yes_bids = list(book.get("yes_bids", book.get("bids", [])) or [])
    yes_asks = list(book.get("yes_asks", book.get("asks", [])) or [])
    no_bids = list(book.get("no_bids", [])) or []
    no_asks = list(book.get("no_asks", [])) or []

    def _best_bid(levels: list[dict[str, Any]]) -> float:
        if not levels:
            return 0.0
        return max(_to_float(x.get("price", 0.0), 0.0) for x in levels)

    def _best_ask(levels: list[dict[str, Any]]) -> float:
        vals = [_to_float(x.get("price", 0.0), 0.0) for x in levels]
        vals = [v for v in vals if v > 0]
        return min(vals) if vals else 0.0

    def _depth(levels: list[dict[str, Any]], cap_levels: int = 5) -> float:
        total = 0.0
        for lv in levels[:cap_levels]:
            total += _to_float(lv.get("price", 0.0), 0.0) * _to_float(lv.get("size", lv.get("quantity", 0.0)), 0.0)
        return total

    yes_bid = _best_bid(yes_bids)
    yes_ask = _best_ask(yes_asks)
    no_bid = _best_bid(no_bids) if no_bids else max(0.0, 1.0 - yes_ask)
    no_ask = _best_ask(no_asks) if no_asks else max(0.0, 1.0 - yes_bid)
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "depth_usd": _depth(yes_bids) + _depth(yes_asks),
    }
