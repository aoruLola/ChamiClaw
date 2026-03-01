from chamiclaw.core.models import Phase, PhaseGateState, TradeStats
from chamiclaw.engines.phase_gate import PhaseGateService


def test_phase_gate_stays_in_phase1_without_enough_trades():
    gate = PhaseGateService(min_trades=200, min_win_rate=0.57, min_rr=1.1, max_drawdown=0.05)
    stats = TradeStats(total_trades=199, wins=120, losses=79, gross_profit=120.0, gross_loss=80.0)

    state = gate.evaluate(stats, max_drawdown_pct=0.01)

    assert state.phase == Phase.PHASE_1
    assert "min_trades_not_met" in state.reasons


def test_phase_gate_promotes_to_phase2_when_all_thresholds_met():
    gate = PhaseGateService(min_trades=200, min_win_rate=0.57, min_rr=1.1, max_drawdown=0.05)
    stats = TradeStats(total_trades=240, wins=150, losses=90, gross_profit=250.0, gross_loss=190.0)

    state = gate.evaluate(stats, max_drawdown_pct=0.03)

    assert state.phase == Phase.PHASE_2
    assert state.allowed_mode_b is True
    assert state.reasons == ["promotion_criteria_met"]


def test_phase_gate_respects_manual_override():
    gate = PhaseGateService(min_trades=200, min_win_rate=0.57, min_rr=1.1, max_drawdown=0.05)
    initial = PhaseGateState(phase=Phase.PHASE_1, allowed_mode_b=False, reasons=["init"])

    state = gate.evaluate(
        TradeStats(total_trades=10, wins=2, losses=8, gross_profit=10.0, gross_loss=30.0),
        max_drawdown_pct=0.04,
        current=initial,
        admin_override=True,
    )

    assert state.phase == Phase.PHASE_2
    assert state.allowed_mode_b is True
    assert "admin_override" in state.reasons


def test_phase_gate_uses_realized_trade_count_for_min_trades():
    gate = PhaseGateService(min_trades=200, min_win_rate=0.57, min_rr=1.1, max_drawdown=0.05)
    stats = TradeStats(
        total_trades=500,
        wins=114,
        losses=86,
        gross_profit=250.0,
        gross_loss=190.0,
    )

    state = gate.evaluate(stats, max_drawdown_pct=0.03)

    assert state.phase == Phase.PHASE_2
    assert state.allowed_mode_b is True
