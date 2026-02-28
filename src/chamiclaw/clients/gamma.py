from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from chamiclaw.core.models import MarketCard


def _parse_end_time(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _parse_outcomes(raw: object) -> list[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        text = raw.strip()
        parsed: object = text
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
        if isinstance(parsed, list):
            values = parsed
        else:
            values = [part.strip() for part in text.split(",") if part.strip()]
    else:
        values = []

    normalized = [str(item).strip().upper() for item in values if str(item).strip()]
    return normalized or ["YES", "NO"]


class GammaClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_markets(self, limit: int = 20) -> list[MarketCard]:
        url = f"{self.base_url}/markets"
        params = {"limit": limit}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json() if resp.content else []
        cards: list[MarketCard] = []
        for item in payload:
            cards.append(
                MarketCard(
                    market_id=str(item.get("id") or item.get("market_id")),
                    question=str(item.get("question") or ""),
                    outcomes=_parse_outcomes(item.get("outcomes", ["YES", "NO"])),
                    end_time=_parse_end_time(item.get("end_date_iso")),
                    status=str(item.get("status", "active")),
                    tags=item.get("tags", []),
                    rule_text=str(item.get("rules") or ""),
                    rule_summary=str(item.get("description") or ""),
                    resolution_sources=item.get("resolution_sources", []),
                    rule_clarity_score=float(item.get("rule_clarity_score", 0.6)),
                    liquidity_score=float(item.get("liquidity_score", 0.0)),
                    spread_stability=float(item.get("spread_stability", 0.0)),
                    volume_density=float(item.get("volume_density", 0.0)),
                    event_risk_adjustment=float(item.get("event_risk_adjustment", 0.0)),
                )
            )
        return cards
