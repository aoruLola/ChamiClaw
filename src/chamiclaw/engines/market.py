from __future__ import annotations

from chamiclaw.clients.gamma import GammaClient
from chamiclaw.core.models import MarketCard


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
