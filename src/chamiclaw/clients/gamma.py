from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from chamiclaw.core.models import MarketCard


def _parse_end_time(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
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


def _parse_clob_token_ids(raw: object) -> list[str]:
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
    return [str(item).strip() for item in values if str(item).strip()]


def _parse_tags(raw: object) -> list[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        values = []
    return [str(item).strip() for item in values if str(item).strip()]


def _parse_bool(raw: object, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_list(raw: object) -> list[object]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                return parsed
        if text:
            return [text]
    return []


class GammaClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_markets(
        self,
        limit: int = 20,
        *,
        active_only: bool = True,
        exclude_closed: bool = True,
        exclude_archived: bool = True,
        end_after: datetime | None = None,
    ) -> list[MarketCard]:
        url = f"{self.base_url}/markets"
        cutoff = end_after or datetime.now(timezone.utc)
        params = {
            "limit": limit,
            "active": active_only,
            "closed": not exclude_closed,
            "archived": not exclude_archived,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json() if resp.content else []
        cards: list[MarketCard] = []
        for item in payload:
            status = str(item.get("status", "")).strip().lower()
            active = _parse_bool(item.get("active"), default=status == "active")
            closed = _parse_bool(item.get("closed"), default=status in {"closed", "resolved", "finalized"})
            archived = _parse_bool(item.get("archived"), default=False)
            end_time = _parse_end_time(item.get("end_date_iso") or item.get("endDateIso") or item.get("endDate"))
            if active_only and not active:
                continue
            if exclude_closed and closed:
                continue
            if exclude_archived and archived:
                continue
            if end_time <= cutoff:
                continue
            clob_token_ids = _parse_clob_token_ids(
                item.get("clobTokenIds", item.get("clob_token_ids", item.get("clob_token_ids_list", [])))
            )
            market_id = clob_token_ids[0] if clob_token_ids else str(item.get("id") or item.get("market_id"))
            tags = _parse_tags(item.get("tags", []))
            cards.append(
                MarketCard(
                    market_id=market_id,
                    question=str(item.get("question") or ""),
                    outcomes=_parse_outcomes(item.get("outcomes", ["YES", "NO"])),
                    end_time=end_time,
                    status=status or ("active" if active else "closed" if closed else "inactive"),
                    active=active,
                    closed=closed,
                    archived=archived,
                    category=str(item.get("category") or ""),
                    subcategory=str(item.get("subcategory") or ""),
                    event_slug=str(item.get("eventSlug") or item.get("event_slug") or ""),
                    market_slug=str(item.get("slug") or item.get("market_slug") or ""),
                    tags=tags,
                    raw_tags=tags,
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

    async def fetch_event_markets(
        self,
        *,
        page_size: int = 50,
        max_pages: int = 5,
        end_after: datetime | None = None,
    ) -> tuple[list[MarketCard], dict[str, int | bool]]:
        url = f"{self.base_url}/events"
        cutoff = end_after or datetime.now(timezone.utc)
        cards: dict[str, MarketCard] = {}
        events_scanned = 0
        markets_expanded = 0
        scan_limit_hit = False
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for page in range(max_pages):
                params = {
                    "closed": False,
                    "order": "id",
                    "ascending": False,
                    "limit": page_size,
                    "offset": page * page_size,
                }
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json() if resp.content else []
                if not payload:
                    break
                events_scanned += len(payload)
                for event in payload:
                    if _parse_bool(event.get("closed"), default=False):
                        continue
                    if _parse_bool(event.get("archived"), default=False):
                        continue
                    event_slug = str(event.get("slug") or event.get("eventSlug") or "")
                    event_title = str(event.get("title") or event.get("name") or "")
                    event_description = str(event.get("description") or "")
                    event_resolution_source = str(event.get("resolutionSource") or "")
                    event_tags = _parse_tags(event.get("tags", []))
                    for item in _coerce_list(event.get("markets")):
                        if not isinstance(item, dict):
                            continue
                        markets_expanded += 1
                        status = str(item.get("status", "")).strip().lower()
                        active = _parse_bool(item.get("active"), default=status == "active")
                        closed = _parse_bool(item.get("closed"), default=status in {"closed", "resolved", "finalized"})
                        archived = _parse_bool(item.get("archived"), default=False)
                        enable_order_book = _parse_bool(item.get("enableOrderBook"), default=False)
                        end_time = _parse_end_time(
                            item.get("endDate")
                            or item.get("end_date_iso")
                            or item.get("endDateIso")
                            or event.get("endDate")
                        )
                        if not active or closed or archived or not enable_order_book or end_time <= cutoff:
                            continue
                        tags = event_tags + _parse_tags(item.get("tags", []))
                        clob_token_ids = _parse_clob_token_ids(
                            item.get("clobTokenIds", item.get("clob_token_ids", item.get("clob_token_ids_list", [])))
                        )
                        market_id = clob_token_ids[0] if clob_token_ids else str(item.get("id") or item.get("market_id"))
                        cards[market_id] = MarketCard(
                            market_id=market_id,
                            question=str(item.get("question") or event_title or ""),
                            outcomes=_parse_outcomes(item.get("outcomes", ["YES", "NO"])),
                            end_time=end_time,
                            status=status or "active",
                            active=active,
                            closed=closed,
                            archived=archived,
                            category=str(item.get("category") or event.get("category") or ""),
                            subcategory=str(item.get("subcategory") or event.get("subcategory") or ""),
                            event_slug=event_slug,
                            event_title=event_title,
                            event_description=event_description,
                            event_resolution_source=event_resolution_source,
                            market_slug=str(item.get("slug") or item.get("market_slug") or ""),
                            tags=tags,
                            raw_tags=tags,
                            rule_text=str(item.get("rules") or item.get("description") or event_description or ""),
                            rule_summary=str(item.get("description") or event_description or ""),
                            resolution_sources=[
                                value
                                for value in [str(item.get("resolutionSource") or ""), event_resolution_source]
                                if value
                            ],
                            rule_clarity_score=float(item.get("rule_clarity_score", 0.6)),
                            liquidity_score=float(item.get("liquidityNum", item.get("liquidity_score", 0.0)) or 0.0),
                            spread_stability=float(item.get("spread_stability", 0.0)),
                            volume_density=float(item.get("volume_density", 0.0)),
                            event_risk_adjustment=float(item.get("event_risk_adjustment", 0.0)),
                        )
                if len(payload) < page_size:
                    break
            else:
                scan_limit_hit = True
        return list(cards.values()), {
            "gamma_events_scanned": events_scanned,
            "gamma_markets_expanded": markets_expanded,
            "gamma_scan_limit_hit": scan_limit_hit,
        }
