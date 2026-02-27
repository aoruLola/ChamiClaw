from datetime import datetime, timedelta, timezone

import asyncio

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.core.models import InfoSignal, MarketCard, Mode, PriceSignal, SpreadStatus
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.storage.repository import InMemoryRepository


def test_runtime_tick_pipeline_updates_mode_and_executes():
    repo = InMemoryRepository()
    repo.upsert_market(
        MarketCard(
            market_id="m1",
            question="Q",
            end_time=datetime.now(timezone.utc) + timedelta(days=1),
            status="active",
            rule_clarity_score=0.9,
            liquidity_score=0.8,
            spread_stability=0.6,
            volume_density=0.7,
        )
    )
    repo.put_price_signal(
        PriceSignal(
            market_id="m1",
            change_5m=0.02,
            vol_ratio_15m=1.3,
            spread=0.01,
            mid=0.5,
            spread_status=SpreadStatus.stable,
        )
    )
    repo.put_info_signal(InfoSignal(market_id="m1", risk_score=0.1, confirmation_level=2))

    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
    )

    assert orchestrator.market_refresh() == 1
    assert orchestrator.mode_refresh() == 1
    assert repo.mode_states["m1"].mode == Mode.MODE_A

    executed = asyncio.run(orchestrator.strategy_loop())
    assert executed == 1
    assert repo.trade_stats.total_trades == 1
