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
