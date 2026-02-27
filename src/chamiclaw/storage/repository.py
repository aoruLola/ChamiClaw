from __future__ import annotations

from chamiclaw.core.models import (
    InfoSignal,
    MarketCard,
    ModeState,
    PortfolioState,
    PriceSignal,
    PriceSnapshot,
    TradeStats,
)


class InMemoryRepository:
    def __init__(self) -> None:
        self.markets: dict[str, MarketCard] = {}
        self.price_snapshots: dict[str, PriceSnapshot] = {}
        self.price_signals: dict[str, PriceSignal] = {}
        self.info_signals: dict[str, InfoSignal] = {}
        self.mode_states: dict[str, ModeState] = {}
        self.trade_stats = TradeStats()
        self.portfolio = PortfolioState()

    def upsert_market(self, market: MarketCard) -> None:
        self.markets[market.market_id] = market

    def put_price_snapshot(self, snapshot: PriceSnapshot) -> None:
        self.price_snapshots[snapshot.market_id] = snapshot

    def put_price_signal(self, signal: PriceSignal) -> None:
        self.price_signals[signal.market_id] = signal

    def put_info_signal(self, signal: InfoSignal) -> None:
        self.info_signals[signal.market_id] = signal

    def put_mode_state(self, mode_state: ModeState) -> None:
        self.mode_states[mode_state.market_id] = mode_state

    def register_trade(self, mode: str) -> None:
        self.trade_stats.total_trades += 1
        if mode == "B":
            self.trade_stats.b_trades += 1
