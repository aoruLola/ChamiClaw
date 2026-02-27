from __future__ import annotations

from chamiclaw.core.models import (
    Action,
    Mode,
    ModeState,
    OrderIntent,
    OrderMode,
    OrderType,
    PriceSignal,
    Side,
    TradeStats,
)


class StrategyEngine:
    def __init__(self, a_risk_pct: float = 0.004, b_risk_pct: float = 0.007, max_b_share: float = 0.2):
        self.a_risk_pct = a_risk_pct
        self.b_risk_pct = b_risk_pct
        self.max_b_share = max_b_share

    def generate_intent(
        self,
        equity: float,
        mode_state: ModeState,
        signal: PriceSignal,
        trade_stats: TradeStats | None = None,
    ) -> OrderIntent | None:
        if mode_state.mode == Mode.NO_TRADE:
            return None

        if mode_state.mode == Mode.MODE_A:
            if not (
                signal.spread <= 0.015
                and abs(signal.change_5m) >= 0.01
                and signal.vol_ratio_15m >= 1.2
                and 0.25 <= signal.mid <= 0.75
            ):
                return None
            size_usd = equity * self.a_risk_pct
            return OrderIntent(
                market_id=signal.market_id,
                side=Side.YES,
                action=Action.OPEN,
                order_type=OrderType.LIMIT,
                limit_price=signal.mid,
                size_usd=size_usd,
                mode=OrderMode.A,
                thesis="MODE_A mean-reversion/flow entry",
                ttl_seconds=60,
            )

        stats = trade_stats or TradeStats()
        projected_total = stats.total_trades + 1
        projected_b = stats.b_trades + 1
        if projected_total > 0 and (projected_b / projected_total) > self.max_b_share:
            return None

        if not (
            abs(signal.change_15m) >= 0.03
            and signal.vol_ratio_15m >= 2.0
            and signal.breakout_15m
            and 0.35 <= signal.mid <= 0.65
        ):
            return None

        size_usd = equity * self.b_risk_pct
        return OrderIntent(
            market_id=signal.market_id,
            side=Side.YES,
            action=Action.OPEN,
            order_type=OrderType.LIMIT,
            limit_price=signal.mid,
            size_usd=size_usd,
            mode=OrderMode.B,
            thesis="MODE_B momentum confirmation entry",
            ttl_seconds=60,
        )
