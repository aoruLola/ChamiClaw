import asyncio

from fastapi.testclient import TestClient

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.api import app as app_module
from chamiclaw.api.app import app
from chamiclaw.core.models import BatchTradeCandidate, LlmReviewDecision, PortfolioState, PriceSnapshot
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.storage.repository import InMemoryRepository


class ApproveAllReviewClient:
    async def review_trade(self, request):
        return LlmReviewDecision(
            decision="approve",
            size_multiplier=1.0,
            confidence=0.9,
            risk_tags=list(request.risk_tags),
            reason_summary="approved",
        )


def test_runtime_run_weather_batch_enforces_order_and_risk_caps():
    repo = InMemoryRepository()
    repo.portfolio = PortfolioState(equity=10_000.0, cash=10_000.0, positions=[])
    for market_id, mid in (("m1", 0.41), ("m2", 0.38), ("m3", 0.35)):
        repo.put_price_snapshot(
            PriceSnapshot(
                market_id=market_id,
                best_bid=max(mid - 0.02, 0.0),
                best_ask=min(mid + 0.02, 1.0),
                mid=mid,
                last=mid,
                spread=0.04,
            )
        )

    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        llm_review_client=ApproveAllReviewClient(),
        weather_batch_max_orders=2,
        weather_max_batch_risk_usd=60.0,
    )

    candidates = [
        BatchTradeCandidate(
            market_id="m1",
            market_question="Will it rain in New York, NY tomorrow?",
            market_probability=0.41,
            consensus_probability=0.72,
            consensus_confidence=0.86,
            edge=0.31,
            suggested_size_usd=40.0,
            data_freshness_minutes=20,
            risk_tags=[],
        ),
        BatchTradeCandidate(
            market_id="m2",
            market_question="Will it rain in Boston, MA tomorrow?",
            market_probability=0.38,
            consensus_probability=0.66,
            consensus_confidence=0.82,
            edge=0.28,
            suggested_size_usd=30.0,
            data_freshness_minutes=25,
            risk_tags=[],
        ),
        BatchTradeCandidate(
            market_id="m3",
            market_question="Will it rain in Seattle, WA tomorrow?",
            market_probability=0.35,
            consensus_probability=0.61,
            consensus_confidence=0.8,
            edge=0.26,
            suggested_size_usd=20.0,
            data_freshness_minutes=30,
            risk_tags=[],
        ),
    ]
    orchestrator.collect_weather_candidates = lambda **kwargs: [candidate.model_copy(deep=True) for candidate in candidates]

    summary = asyncio.run(orchestrator.run_weather_batch(max_candidates=5, per_market_cap_usd=50.0))

    assert summary["candidates"] == 3
    assert summary["executed"] == 2
    assert [order.market_id for order in repo.order_records] == ["m1", "m3"]
    assert sum(order.size_usd for order in repo.order_records) <= 60.0


def test_ops_tick_uses_weather_pipeline_when_enabled(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(app_module.settings, "weather_enabled", True)

    async def fake_bootstrap_market_pool(top_n: int = 10):
        calls.append(("bootstrap_market_pool", top_n))
        return ["m-weather"]

    async def fake_info_refresh_weather(*, top_n=None):
        calls.append(("info_refresh_weather", None))
        return 1

    async def fake_run_weather_batch(*, max_candidates=None, per_market_cap_usd=None):
        calls.append(("run_weather_batch", (max_candidates, per_market_cap_usd)))
        return {"candidates": 2, "reviewed": 1, "executed": 1, "rejected": 1}

    async def fake_reconcile_order_statuses(limit: int = 100):
        calls.append(("reconcile_order_statuses", limit))
        return 0

    async def fake_run_price_stream(_market_ids):
        while True:
            await asyncio.sleep(0.05)

    def fail_legacy(*args, **kwargs):
        raise AssertionError("legacy minute-level path should not run when weather mode is enabled")

    monkeypatch.setattr(app_module.orchestrator, "bootstrap_market_pool", fake_bootstrap_market_pool)
    monkeypatch.setattr(app_module.orchestrator, "info_refresh_weather", fake_info_refresh_weather, raising=False)
    monkeypatch.setattr(app_module.orchestrator, "run_weather_batch", fake_run_weather_batch)
    monkeypatch.setattr(app_module.orchestrator, "reconcile_order_statuses", fake_reconcile_order_statuses)
    monkeypatch.setattr(app_module.orchestrator, "market_refresh", fail_legacy)
    monkeypatch.setattr(app_module.orchestrator, "info_refresh", fail_legacy)
    monkeypatch.setattr(app_module.orchestrator, "mode_refresh", fail_legacy)
    monkeypatch.setattr(app_module.orchestrator, "strategy_loop", fail_legacy)
    monkeypatch.setattr(app_module.orchestrator, "evaluate_phase_gate", lambda admin_override=False: None)
    monkeypatch.setattr(app_module.orchestrator, "run_price_stream", fake_run_price_stream)

    with TestClient(app) as client:
        res = client.post("/ops/tick")

    assert res.status_code == 200
    payload = res.json()
    assert payload["info"] == 1
    assert payload["executed"] == 1
    assert payload["weather_batch"]["reviewed"] == 1
    assert any(name == "info_refresh_weather" for name, _ in calls)
    assert any(name == "run_weather_batch" for name, _ in calls)


def test_ops_emergency_stop_sets_daily_halt_and_pause(monkeypatch):
    app_module.repo.portfolio.daily_halt = False
    app_module.repo.portfolio.pause_until = None

    async def fake_bootstrap_market_pool(top_n: int = 10):
        return ["m1"]

    async def fake_run_price_stream(_market_ids):
        while True:
            await asyncio.sleep(0.05)

    monkeypatch.setattr(app_module.orchestrator, "bootstrap_market_pool", fake_bootstrap_market_pool)
    monkeypatch.setattr(app_module.orchestrator, "run_price_stream", fake_run_price_stream)

    with TestClient(app) as client:
        res = client.post("/ops/emergency/stop", params={"pause_minutes": 120, "reason": "deploy freeze"})

    assert res.status_code == 200
    payload = res.json()
    assert payload["daily_halt"] is True
    assert payload["reason"] == "deploy freeze"
    assert payload["pause_until"] is not None

