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


def test_strategy_generates_mode_a_close_on_take_profit():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=0.015,
        vol_ratio_15m=1.5,
        mid=0.51,
        spread_status=SpreadStatus.stable,
    )

    intent = engine.generate_intent(
        10_000,
        mode_state,
        signal,
        position_size=100.0,
        position_avg_price=0.5,
        held_minutes=20,
    )
    assert intent is not None
    assert intent.action.value == "CLOSE"
    assert intent.mode.value == "A"


def test_strategy_blocks_mode_b_when_phase_gate_disallows():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_B_ALLOWED)
    signal = PriceSignal(
        market_id="m1",
        change_15m=0.04,
        vol_ratio_15m=2.1,
        breakout_15m=True,
        mid=0.5,
    )
    intent = engine.generate_intent(10_000, mode_state, signal, allow_mode_b=False)
    assert intent is None


def test_strategy_does_not_open_new_position_when_existing_position_not_exiting():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=0.02,
        vol_ratio_15m=1.5,
        mid=0.501,
        spread_status=SpreadStatus.stable,
    )

    intent = engine.generate_intent(
        10_000,
        mode_state,
        signal,
        position_size=100.0,
        position_avg_price=0.5,
        held_minutes=10,
    )
    assert intent is None
