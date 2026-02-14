from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from chamiclaw.scanner.gamma_client import GammaClient
from chamiclaw.scanner.rule_summarizer import summarize_rule
from chamiclaw.utils.time import utc_now_iso


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_end_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def scan_markets(config: dict[str, Any], client: GammaClient) -> list[dict[str, Any]]:
    scan_cfg = config["scan"]
    markets_raw = client.list_active_markets(limit=scan_cfg.get("max_markets_per_scan", 200))

    scanned: list[dict[str, Any]] = []
    for raw in markets_raw:
        liquidity = _to_float(raw.get("liquidity") or raw.get("liquidityNum"))
        volume = _to_float(raw.get("volume") or raw.get("volumeNum") or raw.get("volume24hr"))
        if liquidity < scan_cfg["min_liquidity_usd"]:
            continue

        outcomes, prices = client.parse_outcomes(raw)
        token_ids = client.parse_token_ids(raw)
        if len(prices) < 2:
            continue

        rule = summarize_rule(
            raw,
            min_time_to_expiry_min=scan_cfg["min_time_to_expiry_min"],
            max_time_to_expiry_days=scan_cfg["max_time_to_expiry_days"],
        )

        market_id = str(raw.get("id") or raw.get("conditionId") or "")
        condition_id = str(raw.get("conditionId") or raw.get("condition_id") or market_id)
        if not market_id:
            continue

        token_ids = [str(x) for x in token_ids if str(x)]
        token_id_yes = ""
        token_id_no = ""
        if len(token_ids) >= 1:
            token_id_yes = str(token_ids[0])
        if len(token_ids) >= 2:
            token_id_no = str(token_ids[1])
        raw_yes = str(raw.get("yesTokenId") or raw.get("yes_token_id") or "")
        raw_no = str(raw.get("noTokenId") or raw.get("no_token_id") or "")
        if raw_yes and raw_no and (not token_ids or (raw_yes in token_ids and raw_no in token_ids)):
            token_id_yes = raw_yes
            token_id_no = raw_no

        scanned.append(
            {
                "market_id": market_id,
                "condition_id": condition_id,
                "event_id": str(raw.get("eventId") or ""),
                "slug": raw.get("slug"),
                "question": raw.get("question") or "",
                "description": raw.get("description") or "",
                "end_time_utc": _parse_end_time(raw.get("endDate")),
                "liquidity_usd": liquidity,
                "volume_usd": volume,
                "rule_summary": rule,
                "tradable": bool(rule["tradable"]),
                "tradable_reason": rule["reason"],
                "outcomes": outcomes,
                "outcome_prices": prices,
                "clob_token_ids": token_ids,
                "token_id_yes": token_id_yes,
                "token_id_no": token_id_no,
                "updated_at_utc": utc_now_iso(),
            }
        )

    return scanned
