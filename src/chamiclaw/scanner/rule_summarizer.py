from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_rule(market: dict[str, Any], min_time_to_expiry_min: int, max_time_to_expiry_days: int) -> dict[str, Any]:
    end_time = _parse_iso(market.get("endDate") or market.get("end_date_iso"))
    now = datetime.now(timezone.utc)

    ambiguities: list[str] = []
    description = (market.get("description") or "").strip()
    question = (market.get("question") or "").strip()

    if not question:
        ambiguities.append("missing_question")
    if len(description) < 20:
        ambiguities.append("description_too_short")

    expiry_minutes = None
    if end_time:
        expiry_minutes = int((end_time - now).total_seconds() / 60)

    time_ok = True
    if expiry_minutes is None:
        time_ok = False
        ambiguities.append("missing_end_time")
    else:
        if expiry_minutes < min_time_to_expiry_min:
            time_ok = False
            ambiguities.append("too_close_to_expiry")
        if expiry_minutes > max_time_to_expiry_days * 24 * 60:
            time_ok = False
            ambiguities.append("too_far_from_expiry")

    tradable = len(ambiguities) == 0 and time_ok

    return {
        "resolution_source": "Polymarket rules page",
        "expiry_minutes": expiry_minutes,
        "ambiguities": ambiguities,
        "score": max(0.0, 1.0 - 0.25 * len(ambiguities)),
        "tradable": tradable,
        "reason": "clear" if tradable else ",".join(ambiguities),
    }
