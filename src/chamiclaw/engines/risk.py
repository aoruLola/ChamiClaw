from __future__ import annotations

from chamiclaw.core.models import ApprovedOrder, OrderIntent, OrderMode, PortfolioState


class RiskEngine:
    def __init__(self, max_open_positions: int = 5):
        self.max_open_positions = max_open_positions

    def validate(self, intent: OrderIntent, portfolio: PortfolioState) -> ApprovedOrder:
        if portfolio.max_drawdown_pct >= 0.03:
            return ApprovedOrder(approved=False, reason="daily_drawdown_halt")

        if portfolio.consecutive_losses >= 5:
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
