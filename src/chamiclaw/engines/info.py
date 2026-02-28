from __future__ import annotations

from datetime import datetime, timezone

from chamiclaw.clients.brave import BraveClient
from chamiclaw.core.models import InfoSignal


class InfoEngine:
    """Brave-backed info scorer with deterministic fallback path."""

    def __init__(self, brave_client: BraveClient | None = None):
        self.brave_client = brave_client

    @staticmethod
    def _tier_for_domain(domain: str) -> int:
        d = domain.lower()
        if d.endswith(".gov") or d.endswith(".edu") or "official" in d:
            return 1
        if any(x in d for x in ("reuters", "apnews", "bloomberg", "ft.com")):
            return 2
        return 3

    async def analyze_with_brave(
        self,
        market_id: str,
        query: str,
        *,
        event_detected: bool = False,
        clarification_flag: bool = False,
    ) -> InfoSignal:
        if self.brave_client is None:
            return self.analyze(
                market_id=market_id,
                source_tiers=[3],
                event_detected=event_detected,
                clarification_flag=clarification_flag,
            )
        results = await self.brave_client.search(query, count=5)
        tiers: list[int] = []
        top_sources: list[dict[str, str | int]] = []
        for item in results:
            url = str(item.get("url") or "")
            domain = str(item.get("profile", {}).get("name") or item.get("domain") or "")
            tier = self._tier_for_domain(domain or url)
            tiers.append(tier)
            top_sources.append(
                {
                    "domain": domain or url,
                    "tier": tier,
                    "title": str(item.get("title") or ""),
                    "time": datetime.now(timezone.utc).isoformat(),
                }
            )
        signal = self.analyze(
            market_id=market_id,
            source_tiers=tiers or [3],
            event_detected=event_detected,
            clarification_flag=clarification_flag,
        )
        signal.top_sources = top_sources
        return signal

    def analyze(
        self,
        market_id: str,
        source_tiers: list[int],
        event_detected: bool,
        clarification_flag: bool = False,
    ) -> InfoSignal:
        if clarification_flag:
            return InfoSignal(
                market_id=market_id,
                event_detected=event_detected,
                clarification_flag=True,
                risk_score=0.8,
                confirmation_level=0,
            )

        confirmation_level = min(3, len(set(t for t in source_tiers if t in {1, 2})))
        risk_score = 0.2
        if event_detected and 1 not in source_tiers:
            risk_score = 0.5
        if event_detected and 3 in source_tiers and confirmation_level == 0:
            risk_score = 0.75

        return InfoSignal(
            market_id=market_id,
            event_detected=event_detected,
            risk_score=risk_score,
            confirmation_level=confirmation_level,
            clarification_flag=False,
            top_sources=[
                {
                    "domain": "example.com",
                    "tier": t,
                    "title": "signal",
                    "time": datetime.now(timezone.utc).isoformat(),
                }
                for t in source_tiers
            ],
        )
