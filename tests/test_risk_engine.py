from datetime import datetime, timezone

from chamiclaw.core.models import (
    Action,
    OrderIntent,
    OrderMode,
    OrderType,
    PortfolioState,
    Side,
)
from chamiclaw.engines.risk import RiskEngine


def make_intent(size_usd: float, mode: OrderMode = OrderMode.A) -> OrderIntent:
    return OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=size_usd,
        mode=mode,
        thesis="test",
    )


def test_risk_reject_single_order_limit_for_mode_a():
    risk = RiskEngine()
    portfolio = PortfolioState(equity=10_000)
    intent = make_intent(size_usd=60, mode=OrderMode.A)
    approved = risk.validate(intent, portfolio)
    assert not approved.approved
    assert approved.reason == "single_order_limit"


def test_risk_approve_valid_mode_b_order():
    risk = RiskEngine()
    portfolio = PortfolioState(equity=10_000)
    intent = make_intent(size_usd=65, mode=OrderMode.B)
    approved = risk.validate(intent, portfolio)
    assert approved.approved


def test_risk_sets_pause_on_consecutive_losses():
    risk = RiskEngine(pause_minutes=30)
    portfolio = PortfolioState(equity=10_000, consecutive_losses=5)
    approved = risk.validate(make_intent(10), portfolio, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert not approved.approved
    assert approved.reason == "consecutive_losses_pause"
    assert portfolio.pause_until is not None


def test_risk_blocks_during_cooldown_window():
    risk = RiskEngine()
    portfolio = PortfolioState(
        equity=10_000,
        pause_until=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
    )
    approved = risk.validate(
        make_intent(10),
        portfolio,
        now=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
    )
    assert not approved.approved
    assert approved.reason == "cooldown_active"


def test_risk_reset_controls_clears_flags():
    risk = RiskEngine()
    portfolio = PortfolioState(
        equity=10_000,
        daily_halt=True,
        pause_until=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        consecutive_losses=6,
    )
    risk.reset_controls(portfolio, clear_daily_halt=True, clear_pause=True, clear_consecutive_losses=True)
    assert portfolio.daily_halt is False
    assert portfolio.pause_until is None
    assert portfolio.consecutive_losses == 0


def test_risk_rollover_day_resets_day_scoped_state():
    risk = RiskEngine()
    portfolio = PortfolioState(
        equity=10_000,
        daily_pnl=-123.4,
        daily_halt=True,
        consecutive_losses=3,
        max_drawdown_pct=0.031,
        per_market_drawdown_pct={"m1": 0.02},
        pause_until=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
    )

    updated = risk.rollover_day(portfolio)

    assert updated is portfolio
    assert portfolio.daily_halt is False
    assert portfolio.daily_pnl == 0.0
    assert portfolio.consecutive_losses == 0
    assert portfolio.max_drawdown_pct == 0.0
    assert portfolio.per_market_drawdown_pct == {}
    assert portfolio.pause_until is None


def test_risk_allows_close_orders_during_halt():
    risk = RiskEngine()
    portfolio = PortfolioState(equity=10_000, daily_halt=True, consecutive_losses=9)
    close_intent = OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=500.0,
        mode=OrderMode.A,
        thesis="force-exit",
    )
    approved = risk.validate(close_intent, portfolio)
    assert approved.approved is True
    assert approved.reason == "approved_close"
