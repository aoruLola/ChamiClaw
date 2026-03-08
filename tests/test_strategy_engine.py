from datetime import date

from chamiclaw.core.models import (
    ForecastConsensus,
    Mode,
    ModeState,
    PriceSignal,
    PriceSnapshot,
    SpreadStatus,
    TradeStats,
    WeatherMarketMeta,
)
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


def test_strategy_mode_a_uses_no_side_for_negative_signal():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=-0.02,
        vol_ratio_15m=1.5,
        mid=0.4,
        spread_status=SpreadStatus.stable,
    )

    intent = engine.generate_intent(10_000, mode_state, signal)
    assert intent is not None
    assert intent.side.value == "NO"


def test_strategy_mode_b_uses_no_side_for_negative_signal():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_B_ALLOWED)
    signal = PriceSignal(
        market_id="m1",
        change_15m=-0.04,
        vol_ratio_15m=2.3,
        breakout_15m=True,
        mid=0.45,
    )

    intent = engine.generate_intent(10_000, mode_state, signal)
    assert intent is not None
    assert intent.side.value == "NO"


def test_strategy_close_preserves_existing_position_side():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=-0.02,
        vol_ratio_15m=1.5,
        mid=0.49,
        spread_status=SpreadStatus.stable,
    )

    intent = engine.generate_intent(
        10_000,
        mode_state,
        signal,
        position_size=100.0,
        position_avg_price=0.5,
        held_minutes=50,
        position_side="NO",
    )
    assert intent is not None
    assert intent.action.value == "CLOSE"
    assert intent.side.value == "NO"


def test_strategy_does_not_close_no_position_on_small_favorable_move():
    engine = StrategyEngine()
    mode_state = ModeState(market_id="m1", mode=Mode.MODE_A)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=-0.01,
        vol_ratio_15m=1.5,
        mid=0.4955,
        spread_status=SpreadStatus.stable,
    )

    intent = engine.generate_intent(
        10_000,
        mode_state,
        signal,
        position_size=100.0,
        position_avg_price=0.5,
        held_minutes=10,
        position_side="NO",
    )

    assert intent is None


def test_strategy_ranks_weather_candidates_by_edge_and_limits_batch():
    engine = StrategyEngine()
    markets = [
        WeatherMarketMeta(market_id="m1", question="NYC rain?", location="New York, NY"),
        WeatherMarketMeta(market_id="m2", question="Boston rain?", location="Boston, MA"),
    ]
    price_snapshots = {
        "m1": PriceSnapshot(market_id="m1", best_bid=0.39, best_ask=0.41, mid=0.40, spread=0.02, last=0.40),
        "m2": PriceSnapshot(market_id="m2", best_bid=0.49, best_ask=0.51, mid=0.50, spread=0.02, last=0.50),
    }
    consensuses = {
        "m1": ForecastConsensus(
            market_id="m1",
            location="New York, NY",
            forecast_date=date(2026, 1, 5),
            consensus_probability=0.70,
            confidence=0.80,
            dispersion=0.1,
            freshness_minutes=30,
        ),
        "m2": ForecastConsensus(
            market_id="m2",
            location="Boston, MA",
            forecast_date=date(2026, 1, 5),
            consensus_probability=0.62,
            confidence=0.70,
            dispersion=0.08,
            freshness_minutes=25,
        ),
    }

    ranked = engine.rank_weather_candidates(
        markets,
        price_snapshots=price_snapshots,
        consensuses=consensuses,
        portfolio_equity=10_000,
        max_candidates=1,
        per_market_cap_usd=50.0,
    )

    assert len(ranked) == 1
    assert ranked[0].market_id == "m1"
    assert ranked[0].edge == 0.30
    assert ranked[0].suggested_size_usd == 50.0


def test_strategy_skips_stale_or_low_confidence_weather_candidates():
    engine = StrategyEngine()
    markets = [WeatherMarketMeta(market_id="m1", question="NYC rain?", location="New York, NY")]
    price_snapshots = {
        "m1": PriceSnapshot(market_id="m1", best_bid=0.44, best_ask=0.46, mid=0.45, spread=0.02, last=0.45)
    }
    consensuses = {
        "m1": ForecastConsensus(
            market_id="m1",
            location="New York, NY",
            forecast_date=date(2026, 1, 5),
            consensus_probability=0.70,
            confidence=0.40,
            dispersion=0.25,
            freshness_minutes=240,
            stale=True,
        )
    }

    ranked = engine.rank_weather_candidates(
        markets,
        price_snapshots=price_snapshots,
        consensuses=consensuses,
        portfolio_equity=10_000,
        max_candidates=5,
        per_market_cap_usd=50.0,
    )

    assert ranked == []
