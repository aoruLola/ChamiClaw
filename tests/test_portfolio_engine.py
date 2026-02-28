from chamiclaw.core.models import (
    Action,
    FillRecord,
    OrderMode,
    OrderRecord,
    OrderType,
    Position,
    PortfolioState,
    PriceSnapshot,
    Side,
)
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


def test_portfolio_engine_apply_open_fill_creates_position_and_updates_cash():
    engine = PortfolioEngine()
    portfolio = PortfolioState(equity=10_000, cash=10_000)
    order = OrderRecord(
        order_id="o1",
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.50,
        size_usd=100.0,
        status="filled",
        adapter="SimmerAdapter",
        mode=OrderMode.A,
        dry_run=True,
    )
    fill = FillRecord(order_id="o1", market_id="m1", fill_price=0.50, fill_size=100.0, fee=1.0)
    snapshot = PriceSnapshot(
        market_id="m1",
        best_bid=0.49,
        best_ask=0.51,
        mid=0.50,
        spread=0.02,
        last=0.50,
        volume_1m=0.0,
        trades_1m=0,
    )

    updated, attr = engine.apply_fill(portfolio, order, fill, snapshot=snapshot)

    assert len(updated.positions) == 1
    assert updated.positions[0].market_id == "m1"
    assert updated.positions[0].size == 100.0
    assert updated.positions[0].avg_price == 0.50
    assert updated.cash == 9_949.0
    assert attr.spread_at_entry == 0.02
    assert attr.fee_ratio > 0


def test_portfolio_engine_apply_close_fill_realizes_pnl_and_clears_position():
    engine = PortfolioEngine()
    portfolio = PortfolioState(equity=10_000, cash=9_949.0)
    portfolio.positions = [Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.50, u_pnl=0.0)]
    order = OrderRecord(
        order_id="o2",
        market_id="m1",
        side=Side.YES,
        action=Action.CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=0.56,
        size_usd=56.0,
        status="filled",
        adapter="SimmerAdapter",
        mode=OrderMode.A,
        dry_run=True,
    )
    fill = FillRecord(order_id="o2", market_id="m1", fill_price=0.56, fill_size=100.0, fee=1.0)
    snapshot = PriceSnapshot(
        market_id="m1",
        best_bid=0.55,
        best_ask=0.57,
        mid=0.56,
        spread=0.02,
        last=0.56,
        volume_1m=0.0,
        trades_1m=0,
    )

    updated, attr = engine.apply_fill(portfolio, order, fill, snapshot=snapshot)

    assert updated.positions == []
    assert updated.realized_pnl > 0
    assert updated.daily_pnl > 0
    assert attr.actual_pnl > 0
    assert attr.spread_at_exit == 0.02


def test_portfolio_engine_updates_per_market_drawdown_on_losing_close():
    engine = PortfolioEngine()
    portfolio = PortfolioState(equity=10_000, cash=10_000)
    portfolio.positions = [Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.60, u_pnl=0.0)]
    order = OrderRecord(
        order_id="o-loss",
        market_id="m1",
        side=Side.YES,
        action=Action.CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=0.50,
        size_usd=50.0,
        status="filled",
        adapter="SimmerAdapter",
        mode=OrderMode.A,
        dry_run=True,
    )
    fill = FillRecord(order_id="o-loss", market_id="m1", fill_price=0.50, fill_size=100.0, fee=1.0)
    snapshot = PriceSnapshot(
        market_id="m1",
        best_bid=0.49,
        best_ask=0.51,
        mid=0.50,
        spread=0.02,
        last=0.50,
        volume_1m=0.0,
        trades_1m=0,
    )

    updated, _ = engine.apply_fill(portfolio, order, fill, snapshot=snapshot)

    assert updated.realized_pnl < 0
    assert updated.per_market_drawdown_pct.get("m1", 0.0) > 0
