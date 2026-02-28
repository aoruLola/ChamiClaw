from __future__ import annotations

from chamiclaw.core.models import Phase, PhaseGateState, TradeStats


class PhaseGateService:
    def __init__(
        self,
        min_trades: int = 200,
        min_win_rate: float = 0.57,
        min_rr: float = 1.1,
        max_drawdown: float = 0.05,
    ):
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.min_rr = min_rr
        self.max_drawdown = max_drawdown

    @staticmethod
    def win_rate(stats: TradeStats) -> float:
        if stats.total_trades <= 0:
            return 0.0
        return stats.wins / stats.total_trades

    @staticmethod
    def rr(stats: TradeStats) -> float:
        if stats.gross_loss <= 0:
            return float("inf") if stats.gross_profit > 0 else 0.0
        return stats.gross_profit / stats.gross_loss

    def evaluate(
        self,
        stats: TradeStats,
        *,
        max_drawdown_pct: float,
        current: PhaseGateState | None = None,
        admin_override: bool = False,
    ) -> PhaseGateState:
        if admin_override:
            return PhaseGateState(
                phase=Phase.PHASE_2,
                allowed_mode_b=True,
                reasons=["admin_override"],
            )

        reasons: list[str] = []
        if stats.total_trades < self.min_trades:
            reasons.append("min_trades_not_met")
        if self.win_rate(stats) < self.min_win_rate:
            reasons.append("min_win_rate_not_met")
        if self.rr(stats) < self.min_rr:
            reasons.append("min_rr_not_met")
        if max_drawdown_pct > self.max_drawdown:
            reasons.append("max_drawdown_exceeded")

        if reasons:
            if current and current.phase == Phase.PHASE_2:
                return PhaseGateState(phase=Phase.PHASE_2, allowed_mode_b=True, reasons=["phase2_locked"])
            return PhaseGateState(phase=Phase.PHASE_1, allowed_mode_b=False, reasons=reasons)

        return PhaseGateState(phase=Phase.PHASE_2, allowed_mode_b=True, reasons=["promotion_criteria_met"])
