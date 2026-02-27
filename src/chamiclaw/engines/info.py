from __future__ import annotations

from chamiclaw.core.models import InfoSignal


class InfoEngine:
    """Brave-backed info scorer placeholder with deterministic scoring path."""

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
            top_sources=[{"domain": "example.com", "tier": t, "title": "signal", "time": "now"} for t in source_tiers],
        )
