from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from chamiclaw.clients.gamma import GammaClient
from chamiclaw.core.models import MarketCard, WeatherMarketMeta

_US_STATE_CODES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA',
    'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK',
    'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
}
_LOCATION_RE = re.compile(r"([A-Za-z .'-]+,\s*[A-Z]{2})")
_DEFAULT_WEATHER_TAG_SLUGS = ["weather", "rain", "precipitation", "forecast"]
_DEFAULT_WEATHER_SEARCH_TERMS = ["rain", "precipitation", "rainfall", "showers"]


class MarketService:
    def __init__(
        self,
        gamma_client: GammaClient | None = None,
        *,
        weather_event_page_size: int = 50,
        weather_event_max_pages: int = 5,
        weather_event_tag_slugs: list[str] | None = None,
        weather_search_fallback_enabled: bool = True,
        weather_search_terms: list[str] | None = None,
        weather_search_limit_per_term: int = 10,
    ):
        self.gamma_client = gamma_client
        self.weather_event_page_size = max(weather_event_page_size, 1)
        self.weather_event_max_pages = max(weather_event_max_pages, 1)
        self.weather_event_tag_slugs = self._normalize_terms(weather_event_tag_slugs or _DEFAULT_WEATHER_TAG_SLUGS)
        self.weather_search_fallback_enabled = weather_search_fallback_enabled
        self.weather_search_terms = self._normalize_terms(weather_search_terms or _DEFAULT_WEATHER_SEARCH_TERMS)
        self.weather_search_limit_per_term = max(weather_search_limit_per_term, 1)
        self.last_pool_stats: dict[str, object] = {
            "gamma_fetched_total": 0,
            "gamma_events_scanned": 0,
            "gamma_events_tagged": 0,
            "gamma_markets_expanded": 0,
            "gamma_scan_limit_hit": False,
            "gamma_search_fallback_used": False,
            "weather_discovery_mode": "tag_first",
            "weather_tags_requested": list(self.weather_event_tag_slugs),
            "weather_tags_resolved": [],
            "active_markets_total": 0,
            "weather_markets_total": 0,
            "weather_markets_rejected_by_reason": {},
        }

    async def refresh_pool(self, top_n: int = 10, *, weather_only: bool = False) -> list[MarketCard]:
        if self.gamma_client is None:
            return []
        discovery_rejections: dict[str, int] = {}
        discovery_stats: dict[str, object] = {
            "gamma_fetched_total": 0,
            "gamma_events_scanned": 0,
            "gamma_events_tagged": 0,
            "gamma_markets_expanded": 0,
            "gamma_scan_limit_hit": False,
            "gamma_search_fallback_used": False,
            "weather_discovery_mode": "tag_first",
            "weather_tags_requested": list(self.weather_event_tag_slugs),
            "weather_tags_resolved": [],
        }
        if weather_only:
            cards = await self._discover_weather_cards(discovery_stats, discovery_rejections)
        else:
            cards = await self.gamma_client.fetch_markets(limit=max(20, top_n))
            discovery_stats["gamma_fetched_total"] = len(cards)
        current_cards = [card for card in cards if self._is_current_market(card)]
        self.last_pool_stats = {
            **discovery_stats,
            "active_markets_total": len(current_cards),
            "weather_markets_total": 0,
            "weather_markets_rejected_by_reason": dict(discovery_rejections),
        }
        if weather_only:
            filtered = self._filter_weather_cards(current_cards, top_n=top_n, seed_rejections=discovery_rejections)
            return self.rank_markets(filtered, top_n=top_n)
        return self.rank_markets(current_cards, top_n=top_n)

    async def _discover_weather_cards(
        self,
        discovery_stats: dict[str, object],
        discovery_rejections: dict[str, int],
    ) -> list[MarketCard]:
        if self.gamma_client is None:
            return []
        requested = list(self.weather_event_tag_slugs)
        resolved_tags: list[dict[str, str]] = []
        if hasattr(self.gamma_client, "resolve_weather_tags"):
            resolved_tags = await self.gamma_client.resolve_weather_tags(requested)
        discovery_stats["weather_tags_resolved"] = [str(tag.get("slug") or "") for tag in resolved_tags if tag.get("slug")]
        cards: list[MarketCard] = []
        if resolved_tags and hasattr(self.gamma_client, "fetch_weather_events_by_tags"):
            discovery_stats["weather_discovery_mode"] = "tag_first"
            cards, stats = await self.gamma_client.fetch_weather_events_by_tags(
                resolved_tags,
                page_size=self.weather_event_page_size,
                max_pages=self.weather_event_max_pages,
            )
            discovery_stats.update(stats)
        elif self.weather_search_fallback_enabled and hasattr(self.gamma_client, "search_weather_events"):
            discovery_stats["weather_discovery_mode"] = "search_fallback"
            discovery_stats["gamma_search_fallback_used"] = True
            discovery_rejections["missing_weather_tag"] = 1
            cards, stats = await self.gamma_client.search_weather_events(
                self.weather_search_terms,
                limit_per_term=self.weather_search_limit_per_term,
                resolved_tags=resolved_tags,
            )
            discovery_stats.update(stats)
            if not cards:
                discovery_rejections["search_fallback_no_match"] = 1
        elif hasattr(self.gamma_client, "fetch_event_markets"):
            cards, stats = await self.gamma_client.fetch_event_markets(
                page_size=self.weather_event_page_size,
                max_pages=self.weather_event_max_pages,
            )
            discovery_stats.update(stats)
            if not resolved_tags:
                discovery_rejections["missing_weather_tag"] = 1
        else:
            cards = await self.gamma_client.fetch_markets(limit=20)
            discovery_stats["gamma_fetched_total"] = len(cards)
        discovery_stats["gamma_fetched_total"] = len(cards)
        return cards

    def compute_market_score(self, card: MarketCard) -> float:
        return (
            card.liquidity_score
            + card.spread_stability
            + card.volume_density
            + card.rule_clarity_score
            - card.event_risk_adjustment
        )

    def rank_markets(self, cards: list[MarketCard], top_n: int = 10) -> list[MarketCard]:
        for card in cards:
            card.market_score = self.compute_market_score(card)
        return sorted(cards, key=lambda c: c.market_score, reverse=True)[:top_n]

    def extract_weather_markets(self, cards: list[MarketCard], top_n: int = 10) -> list[WeatherMarketMeta]:
        ranked = self.rank_markets([card for card in cards if self._is_current_market(card)], top_n=max(top_n, len(cards)))
        weather_markets: list[WeatherMarketMeta] = []
        rejected: dict[str, int] = {}
        for card in ranked:
            accepted, reason, location = self._classify_weather_card(card)
            if not accepted:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            weather_markets.append(
                WeatherMarketMeta(
                    market_id=card.market_id,
                    question=card.question,
                    location=location,
                    country_code='US',
                    weather_type='daily_precipitation',
                    resolution_source=(card.resolution_sources[0] if card.resolution_sources else ''),
                    rule_text=card.rule_text,
                    settlement_date=self._normalize_market_end_time(card).date(),
                    active=True,
                )
            )
            if len(weather_markets) >= top_n:
                break
        self.last_pool_stats["weather_markets_total"] = len(weather_markets)
        self.last_pool_stats["weather_markets_rejected_by_reason"] = rejected
        return weather_markets

    def _filter_weather_cards(
        self,
        cards: list[MarketCard],
        *,
        top_n: int,
        seed_rejections: dict[str, int] | None = None,
    ) -> list[MarketCard]:
        ranked = self.rank_markets(cards, top_n=max(top_n, len(cards)))
        accepted_cards: list[MarketCard] = []
        rejected: dict[str, int] = dict(seed_rejections or {})
        for card in ranked:
            accepted, reason, _location = self._classify_weather_card(card)
            if not accepted:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            accepted_cards.append(card)
            if len(accepted_cards) >= top_n:
                break
        self.last_pool_stats["weather_markets_total"] = len(accepted_cards)
        self.last_pool_stats["weather_markets_rejected_by_reason"] = rejected
        return accepted_cards

    def _classify_weather_card(self, card: MarketCard) -> tuple[bool, str, str]:
        if not self._is_tradeable(card):
            return False, "non_tradeable", ""
        if not self._is_current_market(card):
            return False, "inactive_or_expired", ""
        if not self._is_weather_family(card):
            return False, "not_weather_family", ""
        if not self._is_daily_weather_window(card):
            return False, "not_daily_window", ""
        if not self._is_precipitation_market(card):
            return False, "not_precipitation", ""
        location = self._extract_location(card)
        if not location:
            return False, "missing_location", ""
        if not self._is_us_location(location):
            return False, "non_us_location", ""
        return True, "accepted", location

    @staticmethod
    def _extract_location(card: MarketCard) -> str:
        for text in (card.rule_summary, card.question, card.event_title, card.event_description):
            if not text:
                continue
            match = _LOCATION_RE.search(text)
            if match:
                return match.group(1).strip()
        return ''

    @staticmethod
    def _is_daily_weather_window(card: MarketCard) -> bool:
        now = datetime.now(timezone.utc)
        end_time = MarketService._normalize_market_end_time(card)
        return end_time <= now + timedelta(days=3)

    @staticmethod
    def _is_precipitation_market(card: MarketCard) -> bool:
        text = " ".join(
            filter(
                None,
                [
                    card.question,
                    card.rule_text,
                    card.rule_summary,
                    card.category,
                    card.subcategory,
                    card.event_slug,
                    card.event_title,
                    card.event_description,
                    card.event_resolution_source,
                    card.market_slug,
                    " ".join(card.raw_tags),
                ],
            )
        ).lower()
        precip_keywords = ('rain', 'precipitation', 'precip', 'shower', 'showers', 'rainfall')
        excluded_keywords = ('snow', 'temperature', 'high temperature', 'win ', 'score', 'touchdown')
        return any(word in text for word in precip_keywords) and not any(word in text for word in excluded_keywords)

    @staticmethod
    def _is_us_location(location: str) -> bool:
        parts = [part.strip() for part in location.split(',')]
        state = parts[-1] if parts else ''
        return state in _US_STATE_CODES

    @staticmethod
    def _is_weather_family(card: MarketCard) -> bool:
        text = " ".join(
            filter(
                None,
                [
                    card.category,
                    card.subcategory,
                    card.event_slug,
                    card.event_title,
                    card.event_description,
                    card.event_resolution_source,
                    card.market_slug,
                    " ".join(card.raw_tags),
                    card.question,
                    card.rule_summary,
                ],
            )
        ).lower()
        include_tokens = ("weather", "rain", "rainfall", "precip", "precipitation", "forecast", "showers")
        exclude_tokens = (
            "election",
            "president",
            "mlb",
            "nba",
            "btc",
            "ethereum",
            "stock",
            "finance",
            "touchdown",
            "goal",
            "temperature",
            "snow",
        )
        return any(token in text for token in include_tokens) and not any(token in text for token in exclude_tokens)

    @staticmethod
    def _is_tradeable(card: MarketCard) -> bool:
        return not card.archived and not card.closed and card.order_book_enabled

    @staticmethod
    def _is_current_market(card: MarketCard) -> bool:
        if card.archived or card.closed:
            return False
        if not card.active and card.status != "active":
            return False
        end_time = MarketService._normalize_market_end_time(card)
        return end_time > datetime.now(timezone.utc)

    @staticmethod
    def _normalize_market_end_time(card: MarketCard) -> datetime:
        end_time = card.end_time
        if end_time.tzinfo is None or end_time.tzinfo.utcoffset(end_time) is None:
            return end_time.replace(tzinfo=timezone.utc)
        return end_time.astimezone(timezone.utc)

    @staticmethod
    def _normalize_terms(values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip().lower()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

