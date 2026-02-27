from chamiclaw.core.models import InfoSignal, Mode, PriceSignal, SpreadStatus
from chamiclaw.engines.mode import ModeEngine


def test_mode_no_trade_on_clarification():
    engine = ModeEngine()
    info = InfoSignal(market_id="m1", clarification_flag=True)
    price = PriceSignal(market_id="m1")
    state = engine.decide("m1", 0.9, info, price)
    assert state.mode == Mode.NO_TRADE


def test_mode_b_allowed_when_all_conditions_met():
    engine = ModeEngine()
    info = InfoSignal(market_id="m1", confirmation_level=2, risk_score=0.2)
    price = PriceSignal(
        market_id="m1",
        change_15m=0.031,
        vol_ratio_15m=2.2,
        spread_status=SpreadStatus.stable,
        breakout_15m=True,
    )
    state = engine.decide("m1", 0.9, info, price)
    assert state.mode == Mode.MODE_B_ALLOWED
