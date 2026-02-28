from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chamiclaw.core.models import Action, ApprovedOrder, OrderIntent, OrderMode, PortfolioState


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RiskEngine:
    def __init__(self, max_open_positions: int = 5, pause_minutes: int = 30):
        self.max_open_positions = max_open_positions
        self.pause_minutes = pause_minutes

    def validate(self, intent: OrderIntent, portfolio: PortfolioState, now: datetime | None = None) -> ApprovedOrder:
        ts = now or utc_now()

        if intent.action == Action.CLOSE:
            return ApprovedOrder(approved=True, reason="approved_close", intent=intent)

        if portfolio.daily_halt or portfolio.max_drawdown_pct >= 0.03:
            portfolio.daily_halt = True
            return ApprovedOrder(approved=False, reason="daily_drawdown_halt")

        if portfolio.pause_until and ts < portfolio.pause_until:
            return ApprovedOrder(approved=False, reason="cooldown_active")

        if portfolio.consecutive_losses >= 5:
            portfolio.pause_until = ts + timedelta(minutes=self.pause_minutes)
            return ApprovedOrder(approved=False, reason="consecutive_losses_pause")

        if len(portfolio.positions) >= self.max_open_positions:
            return ApprovedOrder(approved=False, reason="max_open_positions")

        limit_pct = 0.005 if intent.mode == OrderMode.A else 0.007
        if intent.size_usd > portfolio.equity * limit_pct:
            return ApprovedOrder(approved=False, reason="single_order_limit")

        current_market_exposure = sum(
            p.size * p.avg_price for p in portfolio.positions if p.market_id == intent.market_id
        )
        if current_market_exposure + intent.size_usd > portfolio.equity * 0.02:
            return ApprovedOrder(approved=False, reason="per_market_exposure_limit")

        market_dd = portfolio.per_market_drawdown_pct.get(intent.market_id, 0.0)
        if market_dd >= 0.02:
            return ApprovedOrder(approved=False, reason="market_day_ban")

        return ApprovedOrder(approved=True, reason="approved", intent=intent)

    def reset_controls(
        self,
        portfolio: PortfolioState,
        *,
        clear_daily_halt: bool = True,
        clear_pause: bool = True,
        clear_consecutive_losses: bool = False,
    ) -> PortfolioState:
        if clear_daily_halt:
            portfolio.daily_halt = False
        if clear_pause:
            portfolio.pause_until = None
        if clear_consecutive_losses:
            portfolio.consecutive_losses = 0
        return portfolio


    def rollover_day(self, portfolio: PortfolioState) -> PortfolioState:
        portfolio.daily_halt = False
        portfolio.daily_pnl = 0.0
        portfolio.consecutive_losses = 0
        portfolio.max_drawdown_pct = 0.0
        portfolio.per_market_drawdown_pct.clear()
        portfolio.per_market_realized_pnl.clear()
        portfolio.pause_until = None
        return portfolio
