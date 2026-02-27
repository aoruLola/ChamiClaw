from __future__ import annotations

from chamiclaw.core.models import InfoSignal, Mode, ModeState, PriceSignal


class ModeEngine:
    def __init__(self, rule_clarity_threshold: float = 0.6, high_risk_threshold: float = 0.7):
        self.rule_clarity_threshold = rule_clarity_threshold
        self.high_risk_threshold = high_risk_threshold

    def decide(self, market_id: str, rule_clarity_score: float, info: InfoSignal, price: PriceSignal) -> ModeState:
        reasons: list[str] = []

        if info.clarification_flag:
            reasons.append("clarification_required")
            return ModeState(market_id=market_id, mode=Mode.NO_TRADE, reason_codes=reasons)

        if info.risk_score > self.high_risk_threshold:
            reasons.append("high_event_risk")
            return ModeState(market_id=market_id, mode=Mode.NO_TRADE, reason_codes=reasons)

        if rule_clarity_score < self.rule_clarity_threshold:
            reasons.append("low_rule_clarity")
            return ModeState(market_id=market_id, mode=Mode.NO_TRADE, reason_codes=reasons)

        b_allowed = (
            price.change_15m >= 0.03
            and price.vol_ratio_15m >= 2.0
            and info.confirmation_level >= 2
            and price.spread_status.value == "stable"
            and price.breakout_15m
        )

        if b_allowed:
            reasons.append("b_conditions_met")
            return ModeState(market_id=market_id, mode=Mode.MODE_B_ALLOWED, reason_codes=reasons)

        reasons.append("default_mode_a")
        return ModeState(market_id=market_id, mode=Mode.MODE_A, reason_codes=reasons)
