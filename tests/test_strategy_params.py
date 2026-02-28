from chamiclaw.core.models import Mode, ModeState, PriceSignal, SpreadStatus, StrategyParams
from chamiclaw.engines.strategy import StrategyEngine


def test_strategy_uses_runtime_params_for_mode_a_entry_thresholds():
    strict = StrategyParams(a_change_5m_min_abs=0.03)
    relaxed = StrategyParams(a_change_5m_min_abs=0.01)
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=0.02,
        vol_ratio_15m=1.5,
        mid=0.5,
        spread_status=SpreadStatus.stable,
    )

    engine = StrategyEngine(params=strict)
    assert engine.generate_intent(10_000, mode_state, signal) is None

    engine.configure(relaxed)
    assert engine.generate_intent(10_000, mode_state, signal) is not None

