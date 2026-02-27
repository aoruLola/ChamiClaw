from __future__ import annotations

from fastapi import FastAPI, HTTPException

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.core.models import InfoSignal, MarketCard, PortfolioState, PriceSignal
from chamiclaw.core.settings import AppSettings
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.price import PriceEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.orchestration.scheduler import TaskScheduler
from chamiclaw.storage.repository import InMemoryRepository

app = FastAPI(title="ChamiClaw", version="0.3.0")
settings = AppSettings()
repo = InMemoryRepository()
market_service = MarketService()
price_engine = PriceEngine()
info_engine = InfoEngine()
mode_engine = ModeEngine()
strategy_engine = StrategyEngine()
risk_engine = RiskEngine()
execution_engine = ExecutionEngine(adapter=SimmerAdapter())
orchestrator = RuntimeOrchestrator(
    repo=repo,
    market_service=market_service,
    info_engine=info_engine,
    mode_engine=mode_engine,
    strategy_engine=strategy_engine,
    risk_engine=risk_engine,
    execution_engine=execution_engine,
)
scheduler = TaskScheduler(settings)


async def _strategy_job() -> int:
    return await orchestrator.strategy_loop()


def _price_job() -> int:
    return len(repo.price_signals)


scheduler.bootstrap_defaults(
    market_refresh_fn=orchestrator.market_refresh,
    price_aggregate_fn=_price_job,
    strategy_loop_fn=_strategy_job,
    info_refresh_fn=orchestrator.info_refresh,
)


@app.on_event("startup")
async def startup() -> None:
    scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.stop()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.3.0", "jobs": list(scheduler.jobs.keys())}


@app.post("/ops/tick")
async def tick_once() -> dict:
    ranked = orchestrator.market_refresh()
    info = orchestrator.info_refresh()
    mode = orchestrator.mode_refresh()
    executed = await orchestrator.strategy_loop()
    return {"ranked": ranked, "info": info, "mode": mode, "executed": executed}


@app.post("/markets/rank")
async def rank_markets(cards: list[MarketCard], top_n: int = 10) -> list[MarketCard]:
    ranked = market_service.rank_markets(cards, top_n=top_n)
    for card in ranked:
        repo.upsert_market(card)
    return ranked


@app.post("/price/ingest")
async def ingest_price(
    market_id: str,
    best_bid: float,
    best_ask: float,
    last: float,
    volume_1m: float,
    trades_1m: int,
):
    snapshot, signal = price_engine.on_quote(market_id, best_bid, best_ask, last, volume_1m, trades_1m)
    repo.put_price_snapshot(snapshot)
    repo.put_price_signal(signal)
    return {"snapshot": snapshot, "signal": signal}


@app.post("/info/analyze")
async def analyze_info(market_id: str, source_tiers: list[int], event_detected: bool, clarification_flag: bool = False):
    signal = info_engine.analyze(
        market_id=market_id,
        source_tiers=source_tiers,
        event_detected=event_detected,
        clarification_flag=clarification_flag,
    )
    repo.put_info_signal(signal)
    return signal


@app.post("/mode/decide")
async def decide_mode(market_id: str, rule_clarity_score: float, info: InfoSignal, price: PriceSignal):
    mode_state = mode_engine.decide(market_id, rule_clarity_score, info, price)
    repo.put_mode_state(mode_state)
    return mode_state


@app.post("/strategy/run")
async def run_strategy(mode_market_id: str, price_signal: PriceSignal, portfolio: PortfolioState):
    mode_state = repo.mode_states.get(mode_market_id)
    if mode_state is None:
        raise HTTPException(status_code=404, detail=f"mode_state not found for market_id={mode_market_id}")

    intent = strategy_engine.generate_intent(
        portfolio.equity,
        mode_state,
        price_signal,
        trade_stats=repo.trade_stats,
    )
    if intent is None:
        return {"intent": None, "approved": False, "reason": "no_signal_or_mode_limit"}

    approved = risk_engine.validate(intent, portfolio)
    order_id = await execution_engine.execute(approved)
    if approved.approved and approved.intent is not None:
        repo.register_trade(approved.intent.mode.value)
    return {"intent": intent, "approved": approved.approved, "reason": approved.reason, "order_id": order_id}
