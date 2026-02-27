from chamiclaw.core.models import PortfolioState
from chamiclaw.engines.portfolio import PortfolioEngine


def test_portfolio_engine_applies_loss_and_increments_consecutive_losses():
    engine = PortfolioEngine()
    portfolio = PortfolioState(equity=10_000, cash=10_000, consecutive_losses=1)
    updated = engine.apply_realized_pnl(portfolio, -25)
    assert updated.equity == 9_975
    assert updated.cash == 9_975
    assert updated.daily_pnl == -25
    assert updated.consecutive_losses == 2


def test_portfolio_engine_profit_resets_consecutive_losses():
    engine = PortfolioEngine()
    portfolio = PortfolioState(equity=10_000, cash=10_000, consecutive_losses=3)
    updated = engine.apply_realized_pnl(portfolio, 40)
    assert updated.equity == 10_040
    assert updated.cash == 10_040
    assert updated.daily_pnl == 40
    assert updated.consecutive_losses == 0
