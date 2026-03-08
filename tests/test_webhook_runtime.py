import asyncio
from datetime import datetime, timezone

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.core.models import BatchTradeCandidate, LlmReviewDecision, PortfolioState, PriceSnapshot
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.storage.repository import InMemoryRepository


class FailingReviewClient:
    async def review_trade(self, _request):
        raise RuntimeError("llm unavailable")


class ApproveReviewClient:
    async def review_trade(self, _request):
        return LlmReviewDecision(
            decision="approve",
            size_multiplier=1.0,
            confidence=0.9,
            risk_tags=[],
            reason_summary="ok",
        )


class FailingExecutionEngine(ExecutionEngine):
    async def execute(self, _approved):
        from chamiclaw.core.models import ExecutionResult

        return ExecutionResult(accepted=False, status="execution_error", dry_run=True)


def test_runtime_emits_llm_review_failed_notification():
    repo = InMemoryRepository()
    emitted: list[tuple[str, str, dict]] = []

    async def notify(event_type: str, summary: str, details: dict):
        emitted.append((event_type, summary, details))
        return True

    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        llm_review_client=FailingReviewClient(),
        notify_event=notify,
    )

    candidates = [
        BatchTradeCandidate(
            market_id="m1",
            market_question="Will it rain in Austin, TX tomorrow?",
            market_probability=0.41,
            consensus_probability=0.7,
            consensus_confidence=0.8,
            edge=0.29,
            suggested_size_usd=20.0,
            data_freshness_minutes=10,
            risk_tags=[],
        )
    ]

    reviewed = asyncio.run(orchestrator.review_weather_candidates(candidates))

    assert reviewed == []
    assert emitted
    assert emitted[0][0] == "llm_review_failed"
    assert emitted[0][2]["market_id"] == "m1"


def test_runtime_emits_execution_error_notification_on_failed_weather_batch():
    repo = InMemoryRepository()
    repo.portfolio = PortfolioState(equity=10_000.0, cash=10_000.0, positions=[])
    repo.put_price_snapshot(
        PriceSnapshot(
            market_id="m1",
            best_bid=0.39,
            best_ask=0.43,
            mid=0.41,
            last=0.41,
            spread=0.04,
            ts=datetime.now(timezone.utc),
        )
    )
    emitted: list[tuple[str, str, dict]] = []

    async def notify(event_type: str, summary: str, details: dict):
        emitted.append((event_type, summary, details))
        return True

    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=FailingExecutionEngine(adapter=SimmerAdapter()),
        llm_review_client=ApproveReviewClient(),
        notify_event=notify,
    )
    orchestrator.collect_weather_candidates = lambda **kwargs: [
        BatchTradeCandidate(
            market_id="m1",
            market_question="Will it rain in Austin, TX tomorrow?",
            market_probability=0.41,
            consensus_probability=0.7,
            consensus_confidence=0.8,
            edge=0.29,
            suggested_size_usd=20.0,
            data_freshness_minutes=10,
            risk_tags=[],
        )
    ]

    summary = asyncio.run(orchestrator.run_weather_batch(max_candidates=5, per_market_cap_usd=20.0))

    assert summary["executed"] == 0
    assert emitted
    assert emitted[0][0] == "execution_error"
    assert emitted[0][2]["market_id"] == "m1"
