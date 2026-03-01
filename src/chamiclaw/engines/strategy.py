from __future__ import annotations

from chamiclaw.core.models import (
    Action,
    Mode,
    ModeState,
    OrderIntent,
    OrderMode,
    OrderType,
    PriceSignal,
    Side,
    StrategyParams,
    TradeStats,
)


class StrategyEngine:
    def __init__(
        self,
        a_risk_pct: float = 0.004,
        b_risk_pct: float = 0.007,
        max_b_share: float = 0.2,
        *,
        params: StrategyParams | None = None,
    ):
        base = params.model_copy(deep=True) if params is not None else StrategyParams()
        base.a_risk_pct = a_risk_pct
        base.b_risk_pct = b_risk_pct
        base.max_b_share = max_b_share
        self.params = base

    def configure(self, params: StrategyParams) -> None:
        self.params = params.model_copy(deep=True)

    def generate_intent(
        self,
        equity: float,
        mode_state: ModeState,
        signal: PriceSignal,
        trade_stats: TradeStats | None = None,
        *,
        position_size: float = 0.0,
        position_avg_price: float | None = None,
        position_side: Side | str | None = None,
        held_minutes: int = 0,
        allow_mode_b: bool = True,
    ) -> OrderIntent | None:
        if mode_state.mode == Mode.NO_TRADE:
            return None

        # Exit rules are evaluated first when a position is already open.
        if position_size > 0 and position_avg_price and position_avg_price > 0:
            close_side = Side.YES
            if position_side is not None:
                if isinstance(position_side, Side):
                    close_side = position_side
                else:
                    try:
                        close_side = Side(position_side)
                    except ValueError:
                        close_side = Side.YES
            direction = 1.0 if close_side == Side.YES else -1.0
            ret = direction * (signal.mid - position_avg_price) / position_avg_price
            if mode_state.mode == Mode.MODE_A:
                should_close = (
                    ret >= self.params.a_take_profit
                    or ret <= -self.params.a_stop_loss
                    or held_minutes >= self.params.a_max_hold_minutes
                )
                thesis = "MODE_A exit tp/sl/timestop"
                exit_mode = OrderMode.A
            else:
                should_close = (
                    ret >= self.params.b_take_profit
                    or ret <= -self.params.b_stop_loss
                    or held_minutes >= self.params.b_max_hold_minutes
                )
                thesis = "MODE_B exit tp/sl/timestop"
                exit_mode = OrderMode.B
            if should_close:
                return OrderIntent(
                    market_id=signal.market_id,
                    side=close_side,
                    action=Action.CLOSE,
                    order_type=OrderType.LIMIT,
                    limit_price=signal.mid,
                    size_usd=position_size * signal.mid,
                    mode=exit_mode,
                    thesis=thesis,
                    ttl_seconds=60,
                )
            return None

        if mode_state.mode == Mode.MODE_A:
            if not (
                signal.spread <= self.params.a_spread_max
                and abs(signal.change_5m) >= self.params.a_change_5m_min_abs
                and signal.vol_ratio_15m >= self.params.a_vol_ratio_15m_min
                and self.params.a_mid_min <= signal.mid <= self.params.a_mid_max
            ):
                return None
            size_usd = equity * self.params.a_risk_pct
            entry_side = Side.YES if signal.change_5m > 0 else Side.NO
            return OrderIntent(
                market_id=signal.market_id,
                side=entry_side,
                action=Action.OPEN,
                order_type=OrderType.LIMIT,
                limit_price=signal.mid,
                size_usd=size_usd,
                mode=OrderMode.A,
                thesis="MODE_A mean-reversion/flow entry",
                ttl_seconds=60,
            )

        if not allow_mode_b:
            return None

        stats = trade_stats or TradeStats()
        projected_total = stats.total_trades + 1
        projected_b = stats.b_trades + 1
        if (
            stats.total_trades > 0
            and projected_total > 0
            and (projected_b / projected_total) > self.params.max_b_share
        ):
            return None

        if not (
            abs(signal.change_15m) >= self.params.b_change_15m_min_abs
            and signal.vol_ratio_15m >= self.params.b_vol_ratio_15m_min
            and (signal.breakout_15m if self.params.b_breakout_required else True)
            and self.params.b_mid_min <= signal.mid <= self.params.b_mid_max
        ):
            return None

        size_usd = equity * self.params.b_risk_pct
        entry_side = Side.YES if signal.change_15m > 0 else Side.NO
        return OrderIntent(
            market_id=signal.market_id,
            side=entry_side,
            action=Action.OPEN,
            order_type=OrderType.LIMIT,
            limit_price=signal.mid,
            size_usd=size_usd,
            mode=OrderMode.B,
            thesis="MODE_B momentum confirmation entry",
            ttl_seconds=60,
        )
