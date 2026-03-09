from __future__ import annotations

import re
from datetime import datetime, timezone

from chamiclaw.clients.gamma import GammaClient
from chamiclaw.core.models import MarketCard, WeatherMarketMeta

_US_STATE_CODES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA',
    'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK',
    'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
}
_LOCATION_RE = re.compile(r"([A-Za-z .'-]+,\s*[A-Z]{2})")


class MarketService:
    def __init__(self, gamma_client: GammaClient | None = None):
        self.gamma_client = gamma_client
        self.last_pool_stats: dict[str, object] = {
            "gamma_fetched_total": 0,
            "active_markets_total": 0,
            "weather_markets_total": 0,
            "weather_markets_rejected_by_reason": {},
        }

    async def refresh_pool(self, top_n: int = 10, *, weather_only: bool = False) -> list[MarketCard]:
        if self.gamma_client is None:
            return []
        cards = await self.gamma_client.fetch_markets(limit=max(20, top_n))
        current_cards = [card for card in cards if self._is_current_market(card)]
        self.last_pool_stats = {
            "gamma_fetched_total": len(cards),
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
                    settlement_date=card.end_time.astimezone(timezone.utc).date(),
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

    def _classify_weather_card(self, card: MarketCard) -> tuple[bool, str, str]:
        if not self._is_current_market(card):
            return False, "inactive_or_expired", ""
        if not self._is_weather_family(card):
            return False, "not_weather_family", ""
        if not self._is_precipitation_market(card):
            return False, "not_daily_precipitation", ""
        location = self._extract_location(card)
        if not location:
            return False, "missing_location", ""
        if not self._is_us_location(location):
            return False, "non_us_location", ""
        return True, "accepted", location

    @staticmethod
    def _extract_location(card: MarketCard) -> str:
        for text in (card.rule_summary, card.question):
            if not text:
                continue
            match = _LOCATION_RE.search(text)
            if match:
                return match.group(1).strip()
        return ''

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
                    card.market_slug,
                    " ".join(card.raw_tags),
                ],
            )
        ).lower()
        precip_keywords = ('rain', 'precipitation', 'precip', 'shower', 'rainfall')
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
                    card.market_slug,
                    " ".join(card.raw_tags),
                    card.question,
                    card.rule_summary,
                ],
            )
        ).lower()
        include_tokens = ("weather", "rain", "rainfall", "precip", "precipitation", "forecast")
        exclude_tokens = ("election", "president", "mlb", "nba", "btc", "ethereum", "stock", "touchdown", "goal")
        return any(token in text for token in include_tokens) and not any(token in text for token in exclude_tokens)

    @staticmethod
    def _is_current_market(card: MarketCard) -> bool:
        if card.archived or card.closed:
            return False
        if not card.active and card.status != "active":
            return False
        return card.end_time > datetime.now(timezone.utc)
