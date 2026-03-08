from __future__ import annotations

import re

from chamiclaw.clients.gamma import GammaClient
from chamiclaw.core.models import MarketCard, WeatherMarketMeta

_US_STATE_CODES = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI',
    'MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT',
    'VT','VA','WA','WV','WI','WY','DC'
}
_LOCATION_RE = re.compile(r"([A-Za-z .'-]+,\s*[A-Z]{2})")


class MarketService:
    def __init__(self, gamma_client: GammaClient | None = None):
        self.gamma_client = gamma_client

    async def refresh_pool(self, top_n: int = 10) -> list[MarketCard]:
        if self.gamma_client is None:
            return []
        cards = await self.gamma_client.fetch_markets(limit=max(20, top_n))
        return self.rank_markets(cards, top_n=top_n)

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
        ranked = self.rank_markets(cards, top_n=max(top_n, len(cards)))
        weather_markets: list[WeatherMarketMeta] = []
        for card in ranked:
            if card.status != 'active':
                continue
            if not self._is_precipitation_market(card):
                continue
            location = self._extract_location(card)
            if not location or not self._is_us_location(location, card):
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
                    active=True,
                )
            )
            if len(weather_markets) >= top_n:
                break
        return weather_markets

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
        text = f"{card.question} {card.rule_text}".lower()
        precip_keywords = ('rain', 'precipitation', 'precip', 'shower', 'rainfall')
        excluded_keywords = ('snow', 'temperature', 'high temperature', 'win ', 'score', 'touchdown')
        return any(word in text for word in precip_keywords) and not any(word in text for word in excluded_keywords)

    @staticmethod
    def _is_us_location(location: str, card: MarketCard) -> bool:
        parts = [part.strip() for part in location.split(',')]
        state = parts[-1] if parts else ''
        if state not in _US_STATE_CODES:
            return False
        source_text = ' '.join(card.resolution_sources).upper()
        rule_text = card.rule_text.upper()
        return any(token in source_text or token in rule_text for token in ('NOAA', 'NWS'))
