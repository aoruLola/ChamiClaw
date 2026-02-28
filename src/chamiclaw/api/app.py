from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.clients.brave import BraveClient
from chamiclaw.clients.clob import CLOBClient
from chamiclaw.clients.gamma import GammaClient
from chamiclaw.core.models import InfoSignal, MarketCard, OrderRecord, PortfolioState, PriceSignal
from chamiclaw.core.settings import AppSettings
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.phase_gate import PhaseGateService
from chamiclaw.engines.portfolio import PortfolioEngine
from chamiclaw.engines.price import PriceEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.obs.logging import configure_logging, get_logger
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.orchestration.scheduler import TaskScheduler
from chamiclaw.storage.repository import build_repository

settings = AppSettings.load()
configure_logging(settings.log_level)
logger = get_logger("chamiclaw.api")

repo = build_repository(settings)
gamma_client = GammaClient(settings.gamma_base_url)
clob_client = CLOBClient(settings.clob_rest_url, settings.clob_ws_url)
brave_client = BraveClient(settings.brave_api_key)
market_service = MarketService(gamma_client=gamma_client)
price_engine = PriceEngine()
info_engine = InfoEngine(brave_client=brave_client)
mode_engine = ModeEngine()
strategy_engine = StrategyEngine()
risk_engine = RiskEngine()
portfolio_engine = PortfolioEngine()
execution_engine = ExecutionEngine(
    adapter=SimmerAdapter(base_url=settings.simmer_base_url, api_key=settings.simmer_api_key),
    dry_run=settings.execution_dry_run,
    max_retries=settings.execution_max_retries,
    retry_backoff_seconds=settings.execution_retry_backoff_seconds,
    breaker_failures=settings.execution_breaker_failures,
    breaker_cooldown_seconds=settings.execution_breaker_cooldown_seconds,
)
phase_gate_service = PhaseGateService(
    min_trades=settings.phase1_min_trades,
    min_win_rate=settings.phase1_min_win_rate,
    min_rr=settings.phase1_min_rr,
    max_drawdown=settings.phase1_max_drawdown,
)
orchestrator = RuntimeOrchestrator(
    repo=repo,
    market_service=market_service,
    info_engine=info_engine,
    mode_engine=mode_engine,
    strategy_engine=strategy_engine,
    risk_engine=risk_engine,
    execution_engine=execution_engine,
    portfolio_engine=portfolio_engine,
    phase_gate_service=phase_gate_service,
    price_engine=price_engine,
    clob_client=clob_client,
    ws_max_retries=settings.clob_ws_max_retries,
    ws_backoff_base_seconds=settings.clob_ws_backoff_base_seconds,
    ws_backoff_max_seconds=settings.clob_ws_backoff_max_seconds,
    ws_stale_timeout_seconds=settings.ws_stale_timeout_seconds,
    price_flush_seconds=settings.price_flush_seconds,
    anomaly_debounce_seconds=settings.price_flush_seconds,
    execution_rate_limit_per_market_per_minute=settings.execution_rate_limit_per_market_per_minute,
    execution_rate_limit_global_per_minute=settings.execution_rate_limit_global_per_minute,
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler_started = scheduler.start()
    market_ids = await orchestrator.bootstrap_market_pool(top_n=10)
    if not market_ids:
        market_ids = orchestrator.refresh_market_subscriptions()
    restored_compensations = orchestrator.restore_execution_compensations()
    repo.update_price_stream_state(running=True, reconnects=clob_client.reconnect_count)
    stream_task = asyncio.create_task(orchestrator.run_price_stream(market_ids), name="price-stream")
    _app.state.price_stream_task = stream_task
    logger.info(
        "startup",
        scheduler_started=scheduler_started,
        repository_backend=settings.repository_backend,
        execution_dry_run=execution_engine.dry_run,
        subscribed_markets=len(market_ids),
        restored_compensations=restored_compensations,
    )
    try:
        yield
    finally:
        orchestrator.request_price_stream_stop()
        if stream_task is not None:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
        scheduler.stop()
        close_fn = getattr(repo, "close", None)
        if callable(close_fn):
            close_fn()
        logger.info("shutdown")


app = FastAPI(title="ChamiClaw", version="0.4.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.4.0",
        "jobs": list(scheduler.jobs.keys()),
        "repository_backend": settings.repository_backend,
        "execution_dry_run": execution_engine.dry_run,
        "phase": repo.phase_gate_state.phase.value,
        "price_stream_running": repo.price_stream_running,
        "price_stream_last_event_ts": repo.price_stream_last_event_ts.isoformat() if repo.price_stream_last_event_ts else None,
        "price_stream_reconnects": repo.price_stream_reconnects,
    }


@app.get("/ops/state")
async def ops_state() -> dict:
    return {
        "markets": len(repo.markets),
        "price_snapshots": len(repo.price_snapshots),
        "price_signals": len(repo.price_signals),
        "info_signals": len(repo.info_signals),
        "mode_states": len(repo.mode_states),
        "trade_stats": repo.trade_stats.model_dump(),
        "scheduler_enabled": settings.scheduler_enabled,
        "execution_dry_run": execution_engine.dry_run,
        "phase_gate": repo.phase_gate_state.model_dump(mode="json"),
        "price_stream_running": repo.price_stream_running,
        "price_stream_last_event_ts": repo.price_stream_last_event_ts.isoformat() if repo.price_stream_last_event_ts else None,
        "price_stream_reconnects": repo.price_stream_reconnects,
        "risk_controls": {
            "daily_halt": repo.portfolio.daily_halt,
            "pause_until": repo.portfolio.pause_until.isoformat() if repo.portfolio.pause_until else None,
            "consecutive_losses": repo.portfolio.consecutive_losses,
        },
    }


@app.get("/ops/config")
async def ops_config() -> dict:
    return settings.model_dump()


@app.post("/ops/risk/reset")
async def reset_risk_controls(clear_daily_halt: bool = True, clear_pause: bool = True, clear_losses: bool = False) -> dict:
    portfolio = risk_engine.reset_controls(
        repo.portfolio,
        clear_daily_halt=clear_daily_halt,
        clear_pause=clear_pause,
        clear_consecutive_losses=clear_losses,
    )
    save_fn = getattr(repo, "save_portfolio", None)
    if callable(save_fn):
        save_fn()
    payload = {
        "daily_halt": portfolio.daily_halt,
        "pause_until": portfolio.pause_until.isoformat() if portfolio.pause_until else None,
        "consecutive_losses": portfolio.consecutive_losses,
    }
    logger.info("risk_reset", **payload)
    return payload


@app.post("/ops/trade-stats/reset")
async def reset_trade_stats() -> dict:
    stats = repo.reset_trade_stats()
    payload = stats.model_dump()
    logger.info("trade_stats_reset", **payload)
    return payload




@app.post("/ops/portfolio/apply-pnl")
async def apply_portfolio_pnl(realized_pnl: float) -> dict:
    portfolio = portfolio_engine.apply_realized_pnl(repo.portfolio, realized_pnl)
    save_fn = getattr(repo, "save_portfolio", None)
    if callable(save_fn):
        save_fn()
    payload = {
        "equity": portfolio.equity,
        "cash": portfolio.cash,
        "daily_pnl": portfolio.daily_pnl,
        "consecutive_losses": portfolio.consecutive_losses,
    }
    logger.info("portfolio_pnl_applied", realized_pnl=realized_pnl, **payload)
    return payload

@app.post("/ops/state/reset")
async def reset_runtime_state(
    clear_markets: bool = False,
    clear_trade_stats: bool = False,
    clear_portfolio_controls: bool = False,
) -> dict:
    payload = repo.reset_runtime_state(
        clear_markets=clear_markets,
        clear_trade_stats=clear_trade_stats,
        clear_portfolio_controls=clear_portfolio_controls,
    )
    logger.info(
        "runtime_state_reset",
        clear_markets=clear_markets,
        clear_trade_stats=clear_trade_stats,
        clear_portfolio_controls=clear_portfolio_controls,
        **payload,
    )
    return payload


@app.post("/ops/tick")
async def tick_once() -> dict:
    ranked = orchestrator.market_refresh()
    info = orchestrator.info_refresh()
    info += orchestrator.info_refresh(anomaly_only=True)
    mode = orchestrator.mode_refresh()
    orchestrator.evaluate_phase_gate(admin_override=False)
    executed = await orchestrator.strategy_loop()
    reconciled_orders = await orchestrator.reconcile_order_statuses(limit=100)
    payload = {
        "ranked": ranked,
        "info": info,
        "mode": mode,
        "executed": executed,
        "reconciled_orders": reconciled_orders,
        "phase": repo.phase_gate_state.model_dump(mode="json"),
    }
    logger.info("tick", **payload)
    return payload


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
        allow_mode_b=repo.phase_gate_state.allowed_mode_b,
    )
    if intent is None:
        return {"intent": None, "approved": False, "reason": "no_signal_or_mode_limit"}

    approved = risk_engine.validate(intent, portfolio)
    result = await execution_engine.execute(approved)
    if approved.approved and approved.intent is not None and result and result.accepted:
        repo.register_trade(approved.intent.mode.value)
        repo.record_order(
            OrderRecord(
                order_id=result.order_id or "",
                market_id=approved.intent.market_id,
                side=approved.intent.side,
                action=approved.intent.action,
                order_type=approved.intent.order_type,
                limit_price=approved.intent.limit_price,
                size_usd=approved.intent.size_usd,
                status=result.status,
                adapter=execution_engine.adapter.__class__.__name__,
                mode=approved.intent.mode,
                dry_run=result.dry_run,
                raw=result.raw,
            )
        )
    return {
        "intent": intent,
        "approved": approved.approved,
        "reason": approved.reason,
        "execution": result.model_dump(mode="json") if result else None,
    }


@app.get("/ops/phase")
async def ops_phase() -> dict:
    return repo.phase_gate_state.model_dump(mode="json")


@app.post("/ops/phase/evaluate")
async def evaluate_phase(admin_override: bool = False) -> dict:
    orchestrator.evaluate_phase_gate(admin_override=admin_override)
    payload = repo.phase_gate_state.model_dump(mode="json")
    logger.info("phase_evaluated", **payload)
    return payload


@app.post("/ops/dry-run/set")
async def set_dry_run(enabled: bool = True) -> dict:
    execution_engine.set_dry_run(enabled)
    payload = {"execution_dry_run": execution_engine.dry_run}
    logger.info("dry_run_set", **payload)
    return payload


@app.post("/ops/replay/run")
async def replay_run(minutes: int = 60) -> dict:
    payload = repo.replay_window(minutes)
    logger.info("replay_run", **payload)
    return payload


@app.post("/ops/execution/reconcile")
async def reconcile_execution() -> dict:
    portfolio = await orchestrator.reconcile_execution_state()
    payload = {
        "equity": portfolio.equity,
        "cash": portfolio.cash,
        "positions": [p.model_dump(mode="json") for p in portfolio.positions],
        "unrealized_pnl": portfolio.unrealized_pnl,
    }
    logger.info("execution_reconcile", equity=portfolio.equity, cash=portfolio.cash, positions=len(portfolio.positions))
    return payload


@app.post("/ops/execution/reconcile-orders")
async def reconcile_orders(limit: int = 50) -> dict:
    updated = await orchestrator.reconcile_order_statuses(limit=limit)
    payload = {"updated_orders": updated}
    logger.info("order_reconcile", **payload)
    return payload


@app.post("/ops/execution/compensations/drain")
async def drain_compensations(max_items: int = 10) -> dict:
    drained = await execution_engine.drain_compensations(max_items=max_items)
    sync_payload = orchestrator.sync_execution_compensations()
    health = execution_engine.health_snapshot()
    payload = {
        "drained": drained,
        "pending_compensations": health["pending_compensations"],
        "stored_compensations": sync_payload["stored"],
    }
    logger.info("execution_compensation_drain", **payload)
    return payload


@app.get("/ops/execution/health")
async def execution_health() -> dict:
    payload = execution_engine.health_snapshot()
    logger.info("execution_health", **payload)
    return payload


@app.get("/ops/metrics/summary")
async def metrics_summary() -> dict:
    payload = repo.metrics_summary()
    logger.info("metrics_summary", **payload)
    return payload
