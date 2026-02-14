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


def _aliases(field_map: dict[str, Any] | None, key: str, default: list[str]) -> list[str]:
    if not field_map:
        return list(default)
    raw = field_map.get(key)
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        vals = [str(x).strip() for x in raw if str(x).strip()]
        return vals if vals else list(default)
    return list(default)


def _first_value(payload: dict[str, Any], aliases: list[str], default: Any = None) -> Any:
    for key in aliases:
        if key in payload:
            return payload.get(key)
    return default


def parse_order_response(payload: dict[str, Any], field_map: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = unwrap_payload(payload)
    if not isinstance(payload, dict):
        payload = {}
    id_aliases = _aliases(field_map, "order_id", ["order_id", "id", "client_order_id"])
    status_aliases = _aliases(field_map, "status", ["status"])
    order_id = str(_first_value(payload, id_aliases, "") or "")
    status_raw = str(_first_value(payload, status_aliases, "submitted") or "submitted")
    return {
        "order_id": order_id,
        "status": normalize_order_status(status_raw),
    }


def parse_positions_response(payload: Any, field_map: dict[str, Any] | None = None) -> dict[str, dict[str, float]]:
    rows = unwrap_payload(payload)
    if isinstance(rows, dict):
        row_aliases = _aliases(field_map, "rows", ["positions"])
        nested = _first_value(rows, row_aliases, None)
        if nested is not None:
            rows = nested
    if not isinstance(rows, list):
        return {}
    market_aliases = _aliases(field_map, "market_id", ["market_id", "marketId"])
    yes_aliases = _aliases(field_map, "yes_qty", ["yes_qty", "yesQty", "long_qty"])
    no_aliases = _aliases(field_map, "no_qty", ["no_qty", "noQty", "short_qty"])
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        market_id = str(_first_value(row, market_aliases, "") or "")
        if not market_id:
            continue
        yes_qty = _to_float(_first_value(row, yes_aliases, 0.0), 0.0)
        no_qty = _to_float(_first_value(row, no_aliases, 0.0), 0.0)
        out[market_id] = {
            "yes_qty": yes_qty,
            "no_qty": no_qty,
        }
    return out


def _extract_levels(book: dict[str, Any], aliases: list[str]) -> list[Any]:
    for key in aliases:
        levels = book.get(key)
        if isinstance(levels, list):
            return levels
    return []


def _level_price_size(level: Any, price_aliases: list[str], size_aliases: list[str]) -> tuple[float, float]:
    if isinstance(level, dict):
        price = _to_float(_first_value(level, price_aliases, 0.0), 0.0)
        size = _to_float(_first_value(level, size_aliases, 0.0), 0.0)
        return price, size
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        return _to_float(level[0], 0.0), _to_float(level[1], 0.0)
    return 0.0, 0.0


def parse_orderbook_response(payload: Any, field_map: dict[str, Any] | None = None) -> dict[str, float] | None:
    book = unwrap_payload(payload)
    if not isinstance(book, dict):
        return None

    def _first_num(obj: dict[str, Any], keys: list[str]) -> float | None:
        for k in keys:
            if k in obj:
                try:
                    return float(obj.get(k))
                except (TypeError, ValueError):
                    pass
        return None

    # direct BBO aliases
    yes_bid = _first_num(book, ["yes_best_bid", "yesBid", "yes_bid", "best_yes_bid"])
    yes_ask = _first_num(book, ["yes_best_ask", "yesAsk", "yes_ask", "best_yes_ask"])
    no_bid = _first_num(book, ["no_best_bid", "noBid", "no_bid", "best_no_bid"])
    no_ask = _first_num(book, ["no_best_ask", "noAsk", "no_ask", "best_no_ask"])

    price_aliases = _aliases(field_map, "price", ["price", "px"])
    size_aliases = _aliases(field_map, "size", ["size", "quantity", "qty"])
    container_aliases = _aliases(field_map, "container", ["orderbook", "book"])
    yes_bids_aliases = _aliases(field_map, "yes_bids", ["yes_bids", "bids"])
    yes_asks_aliases = _aliases(field_map, "yes_asks", ["yes_asks", "asks"])
    no_bids_aliases = _aliases(field_map, "no_bids", ["no_bids"])
    no_asks_aliases = _aliases(field_map, "no_asks", ["no_asks"])

    def _best_bid(levels: list[Any]) -> float | None:
        vals = [_level_price_size(x, price_aliases, size_aliases)[0] for x in levels]
        vals = [v for v in vals if v > 0]
        return max(vals) if vals else None

    def _best_ask(levels: list[Any]) -> float | None:
        vals = [_level_price_size(x, price_aliases, size_aliases)[0] for x in levels]
        vals = [v for v in vals if v > 0]
        return min(vals) if vals else None

    def _depth(levels: list[Any], cap_levels: int = 5) -> float:
        total = 0.0
        for lv in levels[:cap_levels]:
            p, q = _level_price_size(lv, price_aliases, size_aliases)
            total += p * q
        return total

    # container compatibility
    yes_book = book.get("yesBook") or book.get("yes_book") or {}
    no_book = book.get("noBook") or book.get("no_book") or {}
    orderbook = _first_value(book, container_aliases, None)
    if not isinstance(orderbook, dict):
        orderbook = book.get("orderbook") or book.get("book") or book

    yes_bids = _extract_levels(yes_book if isinstance(yes_book, dict) else {}, ["bids"]) or _extract_levels(orderbook if isinstance(orderbook, dict) else {}, yes_bids_aliases)
    yes_asks = _extract_levels(yes_book if isinstance(yes_book, dict) else {}, ["asks"]) or _extract_levels(orderbook if isinstance(orderbook, dict) else {}, yes_asks_aliases)
    no_bids = _extract_levels(no_book if isinstance(no_book, dict) else {}, ["bids"]) or _extract_levels(orderbook if isinstance(orderbook, dict) else {}, no_bids_aliases)
    no_asks = _extract_levels(no_book if isinstance(no_book, dict) else {}, ["asks"]) or _extract_levels(orderbook if isinstance(orderbook, dict) else {}, no_asks_aliases)

    if yes_bid is None:
        yes_bid = _best_bid(yes_bids)
    if yes_ask is None:
        yes_ask = _best_ask(yes_asks)
    if no_bid is None:
        no_bid = _best_bid(no_bids)
    if no_ask is None:
        no_ask = _best_ask(no_asks)

    # token-level orderbook compatibility: if only bids/asks are present, treat them as yes side
    if yes_bid is None and yes_bids:
        yes_bid = _best_bid(yes_bids)
    if yes_ask is None and yes_asks:
        yes_ask = _best_ask(yes_asks)

    if yes_bid is None or yes_ask is None:
        last_px = _first_num(book, ["last_trade_price", "lastPrice", "last_price"])
        if last_px is not None:
            if yes_bid is None:
                yes_bid = last_px
            if yes_ask is None:
                yes_ask = last_px

    if yes_bid is None or yes_ask is None:
        return None

    if no_bid is None:
        no_bid = yes_bid
    if no_ask is None:
        no_ask = yes_ask

    return {
        "yes_bid": float(yes_bid),
        "yes_ask": float(yes_ask),
        "no_bid": float(no_bid),
        "no_ask": float(no_ask),
        "depth_usd": _depth(yes_bids) + _depth(yes_asks) + _depth(no_bids) + _depth(no_asks),
    }
