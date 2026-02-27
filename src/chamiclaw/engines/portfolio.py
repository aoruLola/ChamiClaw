from __future__ import annotations

from chamiclaw.core.models import PortfolioState


class PortfolioEngine:
    def __init__(self) -> None:
        self.state = PortfolioState()

    def on_fill(self, realized_pnl: float) -> PortfolioState:
        self.state.daily_pnl += realized_pnl
        self.state.equity += realized_pnl
        if realized_pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        return self.state
