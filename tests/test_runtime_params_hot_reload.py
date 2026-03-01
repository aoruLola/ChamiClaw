import asyncio
from datetime import datetime, timedelta, timezone

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.clients.clob import CLOBClient
from chamiclaw.core.models import MarketCard, Mode, ModeState, PriceSignal, SpreadStatus, StrategyParams
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.price import PriceEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.storage.repository import InMemoryRepository


def test_runtime_strategy_loop_reads_latest_params_from_repo():
    repo = InMemoryRepository()
    repo.upsert_market(
        MarketCard(
            market_id="m1",
            question="Q",
            end_time=datetime.now(timezone.utc) + timedelta(days=1),
            status="active",
            rule_clarity_score=0.9,
            liquidity_score=0.9,
            spread_stability=0.9,
            volume_density=0.9,
        )
    )
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    repo.put_price_signal(
        PriceSignal(
            market_id="m1",
            spread=0.01,
            change_5m=0.02,
            vol_ratio_15m=1.5,
            spread_status=SpreadStatus.stable,
            mid=0.5,
        )
    )
    repo.save_params_version(StrategyParams(a_change_5m_min_abs=0.03), source="test", make_current=True)
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    first = asyncio.run(orchestrator.strategy_loop())
    repo.save_params_version(StrategyParams(a_change_5m_min_abs=0.01), source="test", make_current=True)
    second = asyncio.run(orchestrator.strategy_loop())

    assert first == 0
    assert second == 1

