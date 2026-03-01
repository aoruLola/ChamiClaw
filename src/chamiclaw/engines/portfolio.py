from __future__ import annotations

from chamiclaw.core.models import (
    Action,
    FillRecord,
    OrderRecord,
    PnLAttribution,
    PortfolioState,
    Position,
    PriceSnapshot,
    Side,
)


class PortfolioEngine:
    """Portfolio state transitions driven by realized PnL events."""

    def apply_realized_pnl(self, portfolio: PortfolioState, realized_pnl: float) -> PortfolioState:
        portfolio.daily_pnl += realized_pnl
        portfolio.realized_pnl += realized_pnl
        portfolio.equity += realized_pnl
        portfolio.cash += realized_pnl
        if portfolio.equity > 0:
            current_dd = max(0.0, -portfolio.daily_pnl / portfolio.equity)
            portfolio.max_drawdown_pct = max(portfolio.max_drawdown_pct, current_dd)
        if realized_pnl < 0:
            portfolio.consecutive_losses += 1
        else:
            portfolio.consecutive_losses = 0
        return portfolio

    def apply_fill(
        self,
        portfolio: PortfolioState,
        order: OrderRecord,
        fill: FillRecord,
        *,
        snapshot: PriceSnapshot | None = None,
    ) -> tuple[PortfolioState, PnLAttribution]:
        position = self._find_position(portfolio, order.market_id, order.side)
        ref_price = snapshot.mid if snapshot is not None else fill.fill_price
        spread = snapshot.spread if snapshot is not None else 0.0
        notional = max(fill.fill_price * fill.fill_size, 1e-9)
        attribution = PnLAttribution(
            spread_at_entry=spread if order.action == Action.OPEN else 0.0,
            spread_at_exit=spread if order.action == Action.CLOSE else 0.0,
            slippage=abs(fill.fill_price - ref_price) * fill.fill_size,
            fee_ratio=fill.fee / notional,
        )

        if order.action == Action.OPEN:
            portfolio.cash -= fill.fill_price * fill.fill_size + fill.fee
            if position is None:
                position = Position(
                    market_id=order.market_id,
                    side=order.side,
                    size=fill.fill_size,
                    avg_price=fill.fill_price,
                    u_pnl=0.0,
                )
                portfolio.positions.append(position)
            else:
                total_size = position.size + fill.fill_size
                if total_size > 0:
                    position.avg_price = (
                        position.avg_price * position.size + fill.fill_price * fill.fill_size
                    ) / total_size
                position.size = total_size
            attribution.actual_pnl = -fill.fee
            attribution.theoretical_pnl = -fill.fee
        else:
            if position is None or position.size <= 0:
                return portfolio, attribution
            close_size = min(fill.fill_size, position.size)
            direction = 1.0 if position.side == Side.YES else -1.0
            actual_pnl = direction * (fill.fill_price - position.avg_price) * close_size - fill.fee
            theoretical_pnl = direction * (ref_price - position.avg_price) * close_size - fill.fee

            portfolio.cash += fill.fill_price * close_size - fill.fee
            portfolio.realized_pnl += actual_pnl
            portfolio.daily_pnl += actual_pnl
            if actual_pnl < 0:
                portfolio.consecutive_losses += 1
            else:
                portfolio.consecutive_losses = 0
            market_realized = portfolio.per_market_realized_pnl.get(order.market_id, 0.0) + actual_pnl
            portfolio.per_market_realized_pnl[order.market_id] = market_realized
            current_market_dd = max(0.0, -market_realized / max(portfolio.equity, 1e-9))
            previous_market_dd = portfolio.per_market_drawdown_pct.get(order.market_id, 0.0)
            portfolio.per_market_drawdown_pct[order.market_id] = max(previous_market_dd, current_market_dd)
            position.size -= close_size
            if position.size <= 1e-9:
                portfolio.positions = [
                    p for p in portfolio.positions if not (p.market_id == position.market_id and p.side == position.side)
                ]
            attribution.actual_pnl = actual_pnl
            attribution.theoretical_pnl = theoretical_pnl

        self._mark_unrealized(portfolio, order.market_id, ref_price)
        portfolio.unrealized_pnl = sum(p.u_pnl for p in portfolio.positions)
        position_value = sum((p.avg_price * p.size) + p.u_pnl for p in portfolio.positions)
        portfolio.equity = portfolio.cash + position_value
        if portfolio.equity > 0:
            current_dd = max(0.0, -portfolio.daily_pnl / portfolio.equity)
            portfolio.max_drawdown_pct = max(portfolio.max_drawdown_pct, current_dd)
        return portfolio, attribution

    @staticmethod
    def _find_position(portfolio: PortfolioState, market_id: str, side: Side) -> Position | None:
        for pos in portfolio.positions:
            if pos.market_id == market_id and pos.side == side:
                return pos
        return None

    @staticmethod
    def _mark_unrealized(portfolio: PortfolioState, market_id: str, mark_price: float) -> None:
        for pos in portfolio.positions:
            if pos.market_id != market_id:
                continue
            direction = 1.0 if pos.side == Side.YES else -1.0
            pos.u_pnl = direction * (mark_price - pos.avg_price) * pos.size
