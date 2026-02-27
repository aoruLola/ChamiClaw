from __future__ import annotations

from chamiclaw.core.models import Mode, PortfolioState
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.storage.repository import InMemoryRepository


class RuntimeOrchestrator:
    """Coordinates periodic loops for T1 runtime wiring."""

    def __init__(
        self,
        repo: InMemoryRepository,
        market_service: MarketService,
        info_engine: InfoEngine,
        mode_engine: ModeEngine,
        strategy_engine: StrategyEngine,
        risk_engine: RiskEngine,
        execution_engine: ExecutionEngine,
    ):
        self.repo = repo
        self.market_service = market_service
        self.info_engine = info_engine
        self.mode_engine = mode_engine
        self.strategy_engine = strategy_engine
        self.risk_engine = risk_engine
        self.execution_engine = execution_engine

    def market_refresh(self) -> int:
        cards = list(self.repo.markets.values())
        ranked = self.market_service.rank_markets(cards, top_n=10) if cards else []
        for card in ranked:
            self.repo.upsert_market(card)
        return len(ranked)

    def info_refresh(self) -> int:
        count = 0
        for market_id in self.repo.markets:
            signal = self.info_engine.analyze(market_id=market_id, source_tiers=[1], event_detected=False)
            self.repo.put_info_signal(signal)
            count += 1
        return count

    def mode_refresh(self) -> int:
        count = 0
        for market_id, card in self.repo.markets.items():
            info = self.repo.info_signals.get(market_id)
            price = self.repo.price_signals.get(market_id)
            if info is None or price is None:
                continue
            mode_state = self.mode_engine.decide(market_id, card.rule_clarity_score, info, price)
            self.repo.put_mode_state(mode_state)
            count += 1
        return count

    async def strategy_loop(self, portfolio: PortfolioState | None = None) -> int:
        pf = portfolio or self.repo.portfolio
        executed = 0
        for market_id, mode_state in self.repo.mode_states.items():
            if mode_state.mode == Mode.NO_TRADE:
                continue
            price_signal = self.repo.price_signals.get(market_id)
            if price_signal is None:
                continue
            intent = self.strategy_engine.generate_intent(
                pf.equity,
                mode_state,
                price_signal,
                trade_stats=self.repo.trade_stats,
            )
            if intent is None:
                continue
            approved = self.risk_engine.validate(intent, pf)
            order_id = await self.execution_engine.execute(approved)
            if approved.approved and order_id:
                self.repo.register_trade(approved.intent.mode.value)
                executed += 1
        return executed
