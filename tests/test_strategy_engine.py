from chamiclaw.core.models import Mode, ModeState, PriceSignal, SpreadStatus, TradeStats
from chamiclaw.engines.strategy import StrategyEngine


def test_strategy_generates_mode_a_intent():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=0.015,
        vol_ratio_15m=1.5,
        mid=0.5,
        spread_status=SpreadStatus.stable,
    )
    intent = engine.generate_intent(10_000, mode_state, signal)
    assert intent is not None
    assert intent.size_usd == 40


def test_strategy_blocks_mode_b_when_b_share_exceeded():
    engine = StrategyEngine(max_b_share=0.2)
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_B_ALLOWED)
    signal = PriceSignal(
        market_id="m1",
        change_15m=0.04,
        vol_ratio_15m=2.1,
        breakout_15m=True,
        mid=0.5,
    )
    stats = TradeStats(total_trades=4, b_trades=1)
    # projected ratio = 2/5 = 40% > 20%
    intent = engine.generate_intent(10_000, mode_state, signal, trade_stats=stats)
    assert intent is None
