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


class MarketService:
    def __init__(
        self,
        gamma_client: GammaClient | None = None,
        *,
        weather_event_page_size: int = 50,
        weather_event_max_pages: int = 5,
    ):
        self.gamma_client = gamma_client
        self.weather_event_page_size = max(weather_event_page_size, 1)
        self.weather_event_max_pages = max(weather_event_max_pages, 1)
        self.last_pool_stats: dict[str, object] = {
            "gamma_fetched_total": 0,
            "gamma_events_scanned": 0,
            "gamma_markets_expanded": 0,
            "gamma_scan_limit_hit": False,
            "active_markets_total": 0,
            "weather_markets_total": 0,
            "weather_markets_rejected_by_reason": {},
        }

    async def refresh_pool(self, top_n: int = 10, *, weather_only: bool = False) -> list[MarketCard]:
        if self.gamma_client is None:
            return []
        discovery_stats: dict[str, object] = {
            "gamma_fetched_total": 0,
            "gamma_events_scanned": 0,
            "gamma_markets_expanded": 0,
            "gamma_scan_limit_hit": False,
        }
        if weather_only and hasattr(self.gamma_client, "fetch_event_markets"):
            cards, event_stats = await self.gamma_client.fetch_event_markets(
                page_size=self.weather_event_page_size,
                max_pages=self.weather_event_max_pages,
            )
            discovery_stats["gamma_fetched_total"] = len(cards)
            discovery_stats.update(event_stats)
        else:
            cards = await self.gamma_client.fetch_markets(limit=max(20, top_n))
            discovery_stats["gamma_fetched_total"] = len(cards)
        current_cards = [card for card in cards if self._is_current_market(card)]
        self.last_pool_stats = {
            **discovery_stats,
            "active_markets_total": len(current_cards),
            "weather_markets_total": 0,
            "weather_markets_rejected_by_reason": {},
        }
        if weather_only:
            filtered = self._filter_weather_cards(current_cards, top_n=top_n)
            return self.rank_markets(filtered, top_n=top_n)
        return self.rank_markets(current_cards, top_n=top_n)

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

    def _filter_weather_cards(self, cards: list[MarketCard], *, top_n: int) -> list[MarketCard]:
        ranked = self.rank_markets(cards, top_n=max(top_n, len(cards)))
        accepted_cards: list[MarketCard] = []
        rejected: dict[str, int] = {}
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

    def _classify_weather_card(self, card: MarketCard) -> tuple[bool, str, WeatherMarketMeta | None]:
        if not self._is_weather_family(card):
            return False, "not_weather", None
        now = datetime.now(timezone.utc)
        end_time = self._normalize_market_end_time(card)
        if end_time <= now:
            return False, "inactive_or_expired", None
        if not self._is_daily_weather_window(card):
            return False, "not_daily_window", None
        if not self._is_temperature_market(card):
            return False, "not_temperature", None

        location = self._extract_location(card)
        if not location:
            return False, "missing_location", None
        if not self._is_us_location(location):
            return False, "non_us_location", None

        threshold, is_or_higher = self._extract_temperature_threshold(card)
        if threshold is None:
            return False, "missing_threshold", None

        meta = WeatherMarketMeta(
            market_id=card.market_id,
            question=card.question,
            location=location,
            country_code="US",
            latitude=0.0,
            longitude=0.0,
            weather_type="high_temperature",
            temperature_threshold=threshold,
            is_or_higher=is_or_higher,
            resolution_source=card.event_resolution_source,
            rule_text=card.rule_text,
            settlement_date=end_time.date() if end_time else None,
            active=True,
        )
        return True, "accepted", meta

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
    def _is_temperature_market(card: MarketCard) -> bool:
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
        temperature_keywords = ('temperature', 'highest temperature')
        excluded_keywords = ('win ', 'score', 'touchdown')
        return any(word in text for word in temperature_keywords) and not any(word in text for word in excluded_keywords)

    @staticmethod
    def _extract_temperature_threshold(card: MarketCard) -> tuple[float | None, bool]:
        # Examples: "Will the highest temperature in Paris be 16°C or higher on March 15?"
        # "Will the highest temperature in Singapore be 26°C or below on March 15?"
        # "Will the highest temperature in Singapore be 27°C on March 15?"
        # Also need to handle Farenheit if it exists e.g "be 50°F"
        text = card.question.lower()
        # Find something like 26°c or 26 c or 26
        match = re.search(r'(\d+(?:\.\d+)?)\s*°?\s*(c|f)?', text)
        if not match:
            return None, False
        
        val = float(match.group(1))
        unit = match.group(2)
        if unit == 'f':
            val = round((val - 32) * 5/9, 2)
            
        is_or_higher = 'or higher' in text or 'or above' in text or 'greater than' in text
        is_or_below = 'or below' in text or 'or lower' in text or 'less than' in text
        
        # If it's a specific exact number, we'll default is_or_higher to False, meaning it's an exact match check (or handled in info.py)
        # However, looking at polymarket's current questions, they actually are mutually exclusive buckets. 
        # i.e 26 or below, 27, 28, 29, 30, 31, 32, 33, 34 or higher
        # so we will pass is_or_higher=True for "34 or higher" and is_or_higher=False for "26 or below".
        # For exactly 27, we'll need info.py to know it's an exact match. We'll encode this by setting both flags in the logic where it's used, but here we just return the boolean. 
        # ACTUALLY, let's just use `is_or_higher` to indicate the direction if it's open-ended.
        
        if is_or_higher:
            return val, True
        elif is_or_below:
            return val, False
        else:
            return val, False # For exact match, info.py will handle it by matching rounded values.

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
        include_tokens = ("weather", "rain", "rainfall", "precip", "precipitation", "forecast")
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
        )
        return any(token in text for token in include_tokens) and not any(token in text for token in exclude_tokens)

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
