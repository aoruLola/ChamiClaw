from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from chamiclaw.core.models import MarketCard


DEFAULT_OUTCOMES = ["YES", "NO"]


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
    return normalized or list(DEFAULT_OUTCOMES)


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
    values: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                candidate = item.get("slug") or item.get("label") or item.get("name") or item.get("title")
                if candidate:
                    values.append(str(candidate).strip())
            elif str(item).strip():
                values.append(str(item).strip())
    elif isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


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


def _parse_float(raw: object, default: float = 0.0) -> float:
    try:
        if raw is None or raw == "":
            return default
        return float(raw)
    except (TypeError, ValueError):
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


def _normalize_tag_record(item: object) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    tag_id = str(item.get("id") or item.get("tag_id") or "").strip()
    slug = str(item.get("slug") or item.get("tag_slug") or "").strip()
    label = str(item.get("label") or item.get("name") or item.get("title") or slug).strip()
    if not tag_id and not slug:
        return None
    return {"id": tag_id, "slug": slug, "label": label}


class GammaClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_tags(self) -> list[dict[str, str]]:
        url = f"{self.base_url}/tags"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json() if resp.content else []
        tags: list[dict[str, str]] = []
        for item in _coerce_list(payload):
            tag = _normalize_tag_record(item)
            if tag is not None:
                tags.append(tag)
        return tags

    async def resolve_weather_tags(self, slugs: list[str]) -> list[dict[str, str]]:
        requested = [slug.strip().lower() for slug in slugs if slug.strip()]
        if not requested:
            return []
        available = await self.fetch_tags()
        by_slug = {tag["slug"].lower(): tag for tag in available if tag.get("slug")}
        resolved: list[dict[str, str]] = []
        for slug in requested:
            tag = by_slug.get(slug)
            if tag is not None:
                resolved.append(tag)
        return resolved

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
                    outcomes=_parse_outcomes(item.get("outcomes", DEFAULT_OUTCOMES)),
                    end_time=end_time,
                    status=status or ("active" if active else "closed" if closed else "inactive"),
                    active=active,
                    closed=closed,
                    archived=archived,
                    order_book_enabled=_parse_bool(item.get("enableOrderBook"), default=True),
                    category=str(item.get("category") or ""),
                    subcategory=str(item.get("subcategory") or ""),
                    event_slug=str(item.get("eventSlug") or item.get("event_slug") or ""),
                    market_slug=str(item.get("slug") or item.get("market_slug") or ""),
                    tags=tags,
                    raw_tags=tags,
                    rule_text=str(item.get("rules") or ""),
                    rule_summary=str(item.get("description") or ""),
                    resolution_sources=_parse_tags(item.get("resolution_sources", [])),
                    rule_clarity_score=_parse_float(item.get("rule_clarity_score"), 0.6),
                    liquidity_score=_parse_float(item.get("liquidity_score"), 0.0),
                    spread_stability=_parse_float(item.get("spread_stability"), 0.0),
                    volume_density=_parse_float(item.get("volume_density"), 0.0),
                    event_risk_adjustment=_parse_float(item.get("event_risk_adjustment"), 0.0),
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
        return await self._fetch_event_markets_from_params(
            base_params={},
            page_size=page_size,
            max_pages=max_pages,
            end_after=end_after,
            tagged=False,
        )

    async def fetch_weather_events_by_tags(
        self,
        resolved_tags: list[dict[str, str]],
        *,
        page_size: int = 50,
        max_pages: int = 5,
        end_after: datetime | None = None,
    ) -> tuple[list[MarketCard], dict[str, int | bool]]:
        cutoff = end_after or datetime.now(timezone.utc)
        cards: dict[str, MarketCard] = {}
        events_scanned = 0
        events_tagged = 0
        markets_expanded = 0
        scan_limit_hit = False
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for tag in resolved_tags:
                page_hit_limit = True
                for page in range(max_pages):
                    params: dict[str, object] = {
                        "active": True,
                        "closed": False,
                        "archived": False,
                        "end_date_min": cutoff.isoformat(),
                        "order": "id",
                        "ascending": False,
                        "limit": page_size,
                        "offset": page * page_size,
                    }
                    if tag.get("id"):
                        params["tag_id"] = tag["id"]
                    elif tag.get("slug"):
                        params["tag_slug"] = tag["slug"]
                    resp = await client.get(f"{self.base_url}/events", params=params)
                    resp.raise_for_status()
                    payload = resp.json() if resp.content else []
                    events = self._extract_events(payload)
                    if not events:
                        page_hit_limit = False
                        break
                    events_scanned += len(events)
                    events_tagged += len(events)
                    cards_added, markets_seen = self._cards_from_events(events, cutoff=cutoff)
                    markets_expanded += markets_seen
                    cards.update(cards_added)
                    if len(events) < page_size:
                        page_hit_limit = False
                        break
                if page_hit_limit:
                    scan_limit_hit = True
        return list(cards.values()), {
            "gamma_events_scanned": events_scanned,
            "gamma_events_tagged": events_tagged,
            "gamma_markets_expanded": markets_expanded,
            "gamma_scan_limit_hit": scan_limit_hit,
        }

    async def search_weather_events(
        self,
        terms: list[str],
        *,
        limit_per_term: int = 10,
        resolved_tags: list[dict[str, str]] | None = None,
        end_after: datetime | None = None,
    ) -> tuple[list[MarketCard], dict[str, int | bool]]:
        cutoff = end_after or datetime.now(timezone.utc)
        cards: dict[str, MarketCard] = {}
        events_scanned = 0
        markets_expanded = 0
        tag_slug = ""
        if resolved_tags:
            first = resolved_tags[0]
            tag_slug = str(first.get("slug") or "").strip()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for term in terms:
                if not term.strip():
                    continue
                params: dict[str, object] = {
                    "q": term,
                    "events_status": "active",
                    "keep_closed_markets": 0,
                    "limit": limit_per_term,
                }
                if tag_slug:
                    params["events_tag"] = tag_slug
                resp = await client.get(f"{self.base_url}/public-search", params=params)
                resp.raise_for_status()
                payload = resp.json() if resp.content else []
                events = self._extract_events(payload)
                events_scanned += len(events)
                cards_added, markets_seen = self._cards_from_events(events, cutoff=cutoff)
                markets_expanded += markets_seen
                cards.update(cards_added)
        return list(cards.values()), {
            "gamma_events_scanned": events_scanned,
            "gamma_events_tagged": 0,
            "gamma_markets_expanded": markets_expanded,
            "gamma_scan_limit_hit": False,
            "gamma_search_fallback_used": True,
        }

    async def _fetch_event_markets_from_params(
        self,
        *,
        base_params: dict[str, object],
        page_size: int,
        max_pages: int,
        end_after: datetime | None,
        tagged: bool,
    ) -> tuple[list[MarketCard], dict[str, int | bool]]:
        cutoff = end_after or datetime.now(timezone.utc)
        cards: dict[str, MarketCard] = {}
        events_scanned = 0
        markets_expanded = 0
        scan_limit_hit = False
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for page in range(max_pages):
                params = {
                    "active": True,
                    "closed": False,
                    "archived": False,
                    "end_date_min": cutoff.isoformat(),
                    "order": "id",
                    "ascending": False,
                    "limit": page_size,
                    "offset": page * page_size,
                    **base_params,
                }
                resp = await client.get(f"{self.base_url}/events", params=params)
                resp.raise_for_status()
                payload = resp.json() if resp.content else []
                events = self._extract_events(payload)
                if not events:
                    break
                events_scanned += len(events)
                cards_added, markets_seen = self._cards_from_events(events, cutoff=cutoff)
                markets_expanded += markets_seen
                cards.update(cards_added)
                if len(events) < page_size:
                    break
            else:
                scan_limit_hit = True
        return list(cards.values()), {
            "gamma_events_scanned": events_scanned,
            "gamma_events_tagged": events_scanned if tagged else 0,
            "gamma_markets_expanded": markets_expanded,
            "gamma_scan_limit_hit": scan_limit_hit,
        }

    @staticmethod
    def _extract_events(payload: object) -> list[dict]:
        if isinstance(payload, dict):
            for key in ("events", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [event for event in map(GammaClient._unwrap_event, value) if event is not None]
            maybe_event = GammaClient._unwrap_event(payload)
            return [maybe_event] if maybe_event is not None else []
        if isinstance(payload, list):
            return [event for event in map(GammaClient._unwrap_event, payload) if event is not None]
        return []

    @staticmethod
    def _unwrap_event(item: object) -> dict | None:
        if not isinstance(item, dict):
            return None
        if isinstance(item.get("event"), dict):
            return item["event"]
        if isinstance(item.get("item"), dict):
            return item["item"]
        if isinstance(item.get("result"), dict):
            return item["result"]
        if "markets" in item or "title" in item or "slug" in item:
            return item
        return None

    def _cards_from_events(self, events: list[dict], *, cutoff: datetime) -> tuple[dict[str, MarketCard], int]:
        cards: dict[str, MarketCard] = {}
        markets_expanded = 0
        for event in events:
            event_closed = _parse_bool(event.get("closed"), default=False)
            event_archived = _parse_bool(event.get("archived"), default=False)
            event_active = _parse_bool(event.get("active"), default=not event_closed)
            event_order_book = _parse_bool(event.get("enableOrderBook"), default=True)
            raw_event_end = event.get("endDate") or event.get("end_date_iso") or event.get("endDateIso")
            event_end_time = _parse_end_time(raw_event_end)
            if event_closed or event_archived or not event_active or not event_order_book:
                continue
            if raw_event_end and event_end_time <= cutoff:
                continue
            for item in _coerce_list(event.get("markets")):
                if not isinstance(item, dict):
                    continue
                markets_expanded += 1
                market = self._market_card_from_event(event, item, cutoff=cutoff)
                if market is None:
                    continue
                cards[market.market_id] = market
        return cards, markets_expanded

    def _market_card_from_event(self, event: dict, item: dict, *, cutoff: datetime) -> MarketCard | None:
        status = str(item.get("status", "")).strip().lower()
        active = _parse_bool(item.get("active"), default=status == "active")
        closed = _parse_bool(item.get("closed"), default=status in {"closed", "resolved", "finalized"})
        archived = _parse_bool(item.get("archived"), default=False)
        enable_order_book = _parse_bool(item.get("enableOrderBook"), default=_parse_bool(event.get("enableOrderBook"), True))
        end_time = _parse_end_time(
            item.get("endDate")
            or item.get("end_date_iso")
            or item.get("endDateIso")
            or event.get("endDate")
            or event.get("end_date_iso")
            or event.get("endDateIso")
        )
        if not active or closed or archived or not enable_order_book or end_time <= cutoff:
            return None
        event_slug = str(event.get("slug") or event.get("eventSlug") or "")
        event_title = str(event.get("title") or event.get("name") or "")
        event_description = str(event.get("description") or "")
        event_resolution_source = str(event.get("resolutionSource") or event.get("resolution_source") or "")
        event_tags = _parse_tags(event.get("tags", []))
        market_tags = _parse_tags(item.get("tags", []))
        tags = event_tags + [tag for tag in market_tags if tag.lower() not in {existing.lower() for existing in event_tags}]
        clob_token_ids = _parse_clob_token_ids(
            item.get("clobTokenIds", item.get("clob_token_ids", item.get("clob_token_ids_list", [])))
        )
        market_id = clob_token_ids[0] if clob_token_ids else str(item.get("id") or item.get("market_id"))
        return MarketCard(
            market_id=market_id,
            question=str(item.get("question") or event_title or ""),
            outcomes=_parse_outcomes(item.get("outcomes", DEFAULT_OUTCOMES)),
            end_time=end_time,
            status=status or "active",
            active=active,
            closed=closed,
            archived=archived,
            order_book_enabled=enable_order_book,
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
                source
                for source in [str(item.get("resolutionSource") or "").strip(), event_resolution_source.strip()]
                if source
            ],
            rule_clarity_score=_parse_float(item.get("rule_clarity_score"), 0.6),
            liquidity_score=_parse_float(item.get("liquidityNum", item.get("liquidity_score", 0.0)), 0.0),
            spread_stability=_parse_float(item.get("spread_stability"), 0.0),
            volume_density=_parse_float(item.get("volume_density"), 0.0),
            event_risk_adjustment=_parse_float(item.get("event_risk_adjustment"), 0.0),
        )


