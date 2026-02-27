from __future__ import annotations

from chamiclaw.core.models import PortfolioState


class PortfolioEngine:
    """Portfolio state transitions driven by realized PnL events."""

    def apply_realized_pnl(self, portfolio: PortfolioState, realized_pnl: float) -> PortfolioState:
        portfolio.daily_pnl += realized_pnl
        portfolio.equity += realized_pnl
        portfolio.cash += realized_pnl
        if realized_pnl < 0:
            portfolio.consecutive_losses += 1
        else:
            portfolio.consecutive_losses = 0
        return portfolio
