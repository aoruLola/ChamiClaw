from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.clients.brave import BraveClient
from chamiclaw.clients.clob import CLOBClient
from chamiclaw.clients.gamma import GammaClient
from chamiclaw.clients.nws import NwsClient
from chamiclaw.clients.open_meteo import OpenMeteoClient
from chamiclaw.clients.openai_compatible import OpenAICompatibleClient
from chamiclaw.clients.webhook import WebhookNotifier
from chamiclaw.core.models import (
    BacktestRequest,
    InfoSignal,
    MarketCard,
    PortfolioState,
    PreflightCheck,
    PreflightReport,
    PriceSignal,
    StrategyParamsSetRequest,
)
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
from chamiclaw.optimization.backtest import BacktestEngine
from chamiclaw.optimization.online_tuner import OnlineTuner
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
openmeteo_client = OpenMeteoClient(settings.openmeteo_base_url)
nws_client = NwsClient(settings.nws_base_url)
llm_review_client = (
    OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        temperature=settings.llm_decision_temperature,
    )
    if settings.llm_enabled
    else None
)
webhook_notifier = WebhookNotifier(
    url=settings.webhook_url,
    enabled=settings.webhook_enabled,
    timeout_seconds=settings.webhook_timeout_seconds,
    max_retries=settings.webhook_max_retries,
    service_name=settings.webhook_service_name,
    environment=settings.webhook_environment,
)
market_service = MarketService(
    gamma_client=gamma_client,
    weather_event_page_size=settings.weather_event_page_size,
    weather_event_max_pages=settings.weather_event_max_pages,
    weather_event_tag_slugs=settings.weather_event_tag_slugs,
    weather_search_fallback_enabled=settings.weather_search_fallback_enabled,
    weather_search_terms=settings.weather_search_terms,
    weather_search_limit_per_term=settings.weather_search_limit_per_term,
)
price_engine = PriceEngine()
info_engine = InfoEngine(
    brave_client=brave_client,
    openmeteo_client=openmeteo_client,
    nws_client=nws_client,
)
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
    llm_review_client=llm_review_client,
    llm_failsafe_mode=settings.llm_failsafe_mode,
    weather_batch_max_orders=settings.weather_batch_max_orders,
    weather_max_batch_risk_usd=settings.weather_max_batch_risk_usd,
)
orchestrator.notify_event = lambda event_type, summary, details: _send_notification(event_type, summary, details)
scheduler = TaskScheduler(settings)
backtest_engine = BacktestEngine()
online_tuner = OnlineTuner(backtest_engine=backtest_engine)


def _weather_market_pool_size() -> int:
    return max(settings.weather_batch_max_candidates * 5, settings.weather_batch_max_orders * 5, 25)


def _notification_health_payload() -> dict[str, object]:
    enabled = bool(getattr(webhook_notifier, 'enabled', False))
    last_success_ts = getattr(webhook_notifier, 'last_success_ts', None)
    last_failure_ts = getattr(webhook_notifier, 'last_failure_ts', None)
    return {
        'webhook_enabled': enabled,
        'webhook_last_success_ts': last_success_ts.isoformat() if last_success_ts else None,
        'webhook_last_failure_ts': last_failure_ts.isoformat() if last_failure_ts else None,
        'webhook_failures_total': int(getattr(webhook_notifier, 'failures_total', 0)),
        'webhook_last_event_type': getattr(webhook_notifier, 'last_event_type', None),
    }


def _market_pool_payload() -> dict[str, object]:
    return {
        'market_pool': dict(getattr(orchestrator, 'last_market_pool_stats', {})),
        'weather_info_refresh': dict(getattr(orchestrator, 'last_weather_info_refresh_summary', {})),
    }


async def _send_notification(event_type: str, summary: str, details: dict) -> bool:
    send = getattr(webhook_notifier, 'send', None)
    if send is None:
        return False
    return await send(event_type=event_type, summary=summary, details=details)


def _persist_current_params_to_path() -> bool:
    params_path = Path(settings.params_path)
    try:
        params_path.parent.mkdir(parents=True, exist_ok=True)
        current = repo.get_current_params()
        payload = {
            "version_id": current.version_id,
            "created_at": current.created_at.isoformat(),
            "source": current.source,
            "score": current.score,
            "params": current.params.model_dump(mode="json"),
        }
        params_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning("params_file_persist_failed", path=str(params_path), error=str(exc))
        return False


def _load_params_from_path_if_exists() -> None:
    params_path = Path(settings.params_path)
    if not params_path.exists() or not params_path.is_file():
        return
    try:
        payload = json.loads(params_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("params_file_invalid_json", path=str(params_path))
        return
    raw_params = payload.get("params", payload)
    try:
        params = StrategyParamsSetRequest(params=raw_params, source="params_file").params
    except Exception:
        logger.warning("params_file_invalid_schema", path=str(params_path))
        return
    repo.save_params_version(params, source="params_file", make_current=True)


_load_params_from_path_if_exists()
orchestrator.refresh_strategy_params()
_persist_current_params_to_path()


async def _strategy_job() -> int:
    if settings.weather_enabled:
        summary = await orchestrator.run_weather_batch(
            max_candidates=settings.weather_batch_max_candidates,
            per_market_cap_usd=settings.weather_max_position_per_market_usd,
        )
        await _send_notification(
            'weather_batch_completed',
            'scheduled weather batch completed',
            dict(summary),
        )
        return int(summary['executed'])
    return await orchestrator.strategy_loop()


async def _market_refresh_job() -> int:
    if settings.weather_enabled:
        return len(await orchestrator.bootstrap_market_pool(top_n=_weather_market_pool_size(), weather_only=True))
    return orchestrator.market_refresh()


async def _price_job() -> int:
    return len(repo.price_signals)


async def _info_refresh_job() -> int:
    if settings.weather_enabled:
        summary = await orchestrator.info_refresh_weather(top_n=_weather_market_pool_size())
        return int(summary["info_signals"])
    return orchestrator.info_refresh()


scheduler.bootstrap_defaults(
    market_refresh_fn=_market_refresh_job,
    price_aggregate_fn=_price_job,
    strategy_loop_fn=_strategy_job,
    info_refresh_fn=_info_refresh_job,
)


def _probe_http(url: str, timeout_seconds: float = 3.0) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(url)
        if response.status_code >= 500:
            return False, f"http_{response.status_code}"
        return True, f"http_{response.status_code}"
    except Exception as exc:  # pragma: no cover - network failures are environment dependent
        return False, str(exc)


def build_preflight_report() -> dict:
    checks: list[PreflightCheck] = []
    if settings.run_profile == "live" and execution_engine.dry_run:
        checks.append(PreflightCheck(name="execution_mode", ok=True, message="live profile with dry-run safety"))
    elif not execution_engine.dry_run and (not settings.simmer_base_url or not settings.simmer_api_key):
        checks.append(PreflightCheck(name="execution_mode", ok=False, message="missing simmer live credentials"))
    else:
        checks.append(
            PreflightCheck(
                name="execution_mode",
                ok=True,
                message=f"profile={settings.run_profile}, dry_run={execution_engine.dry_run}",
            )
        )

    if settings.repository_backend == "sqlite":
        db_path = Path(settings.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(db_path.parent, os.W_OK)
        free_bytes = shutil.disk_usage(db_path.parent).free
        enough_space = free_bytes >= 500 * 1024 * 1024
        checks.append(
            PreflightCheck(
                name="sqlite_writable",
                ok=bool(writable and enough_space),
                message=f"writable={writable}, free_mb={free_bytes // (1024 * 1024)}",
            )
        )
    else:
        checks.append(PreflightCheck(name="sqlite_writable", ok=True, message="repository_backend is not sqlite"))

    gamma_ok, gamma_msg = _probe_http(f"{settings.gamma_base_url}/markets")
    checks.append(PreflightCheck(name="gamma_connectivity", ok=gamma_ok, message=gamma_msg))
    clob_ok, clob_msg = _probe_http(f"{settings.clob_rest_url}/book?market=healthcheck")
    checks.append(PreflightCheck(name="clob_connectivity", ok=clob_ok, message=clob_msg))
    if settings.weather_enabled:
        openmeteo_ok, openmeteo_msg = _probe_http(f"{settings.openmeteo_base_url}/forecast")
        checks.append(PreflightCheck(name="openmeteo_connectivity", ok=openmeteo_ok, message=openmeteo_msg))
        nws_ok, nws_msg = _probe_http(f"{settings.nws_base_url}/points/39.7456,-97.0892")
        checks.append(PreflightCheck(name="nws_connectivity", ok=nws_ok, message=nws_msg))
    if settings.llm_enabled:
        llm_ok, llm_msg = _probe_http(f"{settings.llm_base_url}/models")
        checks.append(PreflightCheck(name="llm_connectivity", ok=llm_ok, message=llm_msg))
    if execution_engine.dry_run:
        checks.append(PreflightCheck(name="simmer_connectivity", ok=True, message="skipped_in_dry_run"))
    else:
        simmer_ok, simmer_msg = _probe_http(f"{settings.simmer_base_url}/health")
        checks.append(PreflightCheck(name="simmer_connectivity", ok=simmer_ok, message=simmer_msg))

    risk_ok = not repo.portfolio.daily_halt and repo.portfolio.pause_until is None
    checks.append(
        PreflightCheck(
            name="risk_controls",
            ok=risk_ok,
            message=f"daily_halt={repo.portfolio.daily_halt}, pause_until={repo.portfolio.pause_until}",
        )
    )
    account_ok = repo.portfolio.equity > 0 and repo.portfolio.cash >= 0
    checks.append(
        PreflightCheck(
            name="account_state",
            ok=account_ok,
            message=(
                f"equity={repo.portfolio.equity:.4f},cash={repo.portfolio.cash:.4f},"
                f"positions={len(repo.portfolio.positions)}"
            ),
        )
    )
    checks.append(
        PreflightCheck(
            name="phase_gate",
            ok=repo.phase_gate_state.allowed_mode_b or repo.phase_gate_state.phase.value == "PHASE_1",
            message=f"phase={repo.phase_gate_state.phase.value}, allowed_mode_b={repo.phase_gate_state.allowed_mode_b}",
        )
    )
    ok = all(item.ok for item in checks)
    payload = PreflightReport(ok=ok, checks=checks).model_dump(mode="json")
    payload.update(_market_pool_payload())
    return payload


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler_started = scheduler.start()
    market_ids: list[str] = []
    bootstrap_error: str | None = None
    refresh_error: str | None = None
    try:
        market_ids = await orchestrator.bootstrap_market_pool(
            top_n=_weather_market_pool_size() if settings.weather_enabled else 10,
            weather_only=settings.weather_enabled,
        )
    except Exception as exc:
        bootstrap_error = str(exc)
        logger.warning("startup_market_bootstrap_failed", error=bootstrap_error)
    if not market_ids:
        try:
            market_ids = orchestrator.refresh_market_subscriptions()
        except Exception as exc:
            refresh_error = str(exc)
            logger.warning("startup_market_subscription_refresh_failed", error=refresh_error)
            market_ids = []
    if bootstrap_error or refresh_error or not market_ids:
        await _send_notification(
            'startup_degraded',
            'service started with degraded market bootstrap',
            {
                'bootstrap_error': bootstrap_error,
                'refresh_error': refresh_error,
                'subscribed_markets': len(market_ids),
            },
        )
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
webui_dir = Path(__file__).resolve().parents[1] / "webui"
app.mount("/ui", StaticFiles(directory=str(webui_dir), html=True), name="webui")


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
        "weather_enabled": settings.weather_enabled,
        "llm_enabled": settings.llm_enabled,
        "last_weather_batch": orchestrator.last_weather_batch_summary,
        **_market_pool_payload(),
        **_notification_health_payload(),
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
        "weather_enabled": settings.weather_enabled,
        "llm_enabled": settings.llm_enabled,
        "last_weather_batch": orchestrator.last_weather_batch_summary,
        **_market_pool_payload(),
        **_notification_health_payload(),
    }


@app.get("/ops/config")
async def ops_config() -> dict:
    return settings.model_dump()


@app.get("/ops/preflight")
async def ops_preflight() -> dict:
    payload = build_preflight_report()
    if not payload["ok"]:
        failed_checks = [item for item in payload["checks"] if not item.get("ok")]
        await _send_notification(
            'preflight_failed',
            'preflight report contains failing checks',
            {'failed_checks': failed_checks},
        )
    logger.info("ops_preflight", ok=payload["ok"])
    return payload


@app.get("/ops/strategy/params")
async def get_strategy_params() -> dict:
    return repo.get_current_params().model_dump(mode="json")


@app.post("/ops/strategy/params/set")
async def set_strategy_params(payload: StrategyParamsSetRequest, dry_run: bool = False) -> dict:
    if dry_run:
        return {"saved": False, "params": payload.params.model_dump(mode="json"), "source": payload.source}
    saved = repo.save_params_version(payload.params, source=payload.source, make_current=True)
    orchestrator.refresh_strategy_params()
    _persist_current_params_to_path()
    logger.info("strategy_params_set", version_id=saved.version_id, source=payload.source)
    return saved.model_dump(mode="json")


@app.post("/ops/emergency/stop")
async def emergency_stop(pause_minutes: int = 24 * 60, reason: str = "manual_stop") -> dict:
    repo.portfolio.daily_halt = True
    if pause_minutes > 0:
        repo.portfolio.pause_until = datetime.now(timezone.utc) + timedelta(minutes=pause_minutes)
    save_fn = getattr(repo, "save_portfolio", None)
    if callable(save_fn):
        save_fn()
    payload = {
        "daily_halt": repo.portfolio.daily_halt,
        "pause_until": repo.portfolio.pause_until.isoformat() if repo.portfolio.pause_until else None,
        "reason": reason,
    }
    await _send_notification('emergency_stop_triggered', 'emergency stop activated', dict(payload))
    logger.warning("emergency_stop", **payload)
    return payload


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
    if settings.weather_enabled:
        ranked = len(await orchestrator.bootstrap_market_pool(top_n=_weather_market_pool_size(), weather_only=True))
        info = await orchestrator.info_refresh_weather(top_n=_weather_market_pool_size())
        weather_batch = await orchestrator.run_weather_batch(
            max_candidates=settings.weather_batch_max_candidates,
            per_market_cap_usd=settings.weather_max_position_per_market_usd,
        )
        reconciled_orders = await orchestrator.reconcile_order_statuses(limit=100)
        payload = {
            "ranked": ranked,
            "info": info["info_signals"],
            "mode": 0,
            "executed": weather_batch["executed"],
            "weather_batch": weather_batch,
            "market_pool": dict(orchestrator.last_market_pool_stats),
            "weather_info_refresh": dict(info),
            "reconciled_orders": reconciled_orders,
            "phase": repo.phase_gate_state.model_dump(mode="json"),
        }
        await _send_notification('weather_batch_completed', 'weather tick batch completed', dict(weather_batch))
        logger.info("tick", **payload)
        return payload

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


@app.post("/ops/weather/batch/run")
async def weather_batch_run(max_candidates: int | None = None, per_market_cap_usd: float | None = None) -> dict:
    payload = await orchestrator.run_weather_batch(
        max_candidates=max_candidates,
        per_market_cap_usd=per_market_cap_usd,
    )
    await _send_notification('weather_batch_completed', 'manual weather batch completed', dict(payload))
    logger.info("weather_batch_run", **payload)
    return payload


@app.get("/ops/weather/batch/last")
async def weather_batch_last() -> dict:
    return dict(orchestrator.last_weather_batch_summary)


@app.get("/ops/notifications/health")
async def notifications_health() -> dict:
    return _notification_health_payload()


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
    repo.put_price_signal(price_signal)

    # Repository portfolio is the runtime source of truth for risk controls and positions.
    working_portfolio = repo.portfolio.model_copy(deep=True)
    position_size = 0.0
    position_avg_price = None
    position_side = None
    for pos in working_portfolio.positions:
        if pos.market_id != mode_market_id:
            continue
        position_size = pos.size
        position_avg_price = pos.avg_price
        position_side = pos.side.value
        break

    orchestrator.refresh_strategy_params()
    intent = strategy_engine.generate_intent(
        working_portfolio.equity,
        mode_state,
        price_signal,
        trade_stats=repo.trade_stats,
        position_size=position_size,
        position_avg_price=position_avg_price,
        position_side=position_side,
        allow_mode_b=repo.phase_gate_state.allowed_mode_b,
    )
    if intent is None:
        return {"intent": None, "approved": False, "reason": "no_signal_or_mode_limit"}

    approved = risk_engine.validate(intent, working_portfolio)
    result = await execution_engine.execute(approved)
    if approved.approved and approved.intent is not None and result and result.accepted:
        updated_portfolio = orchestrator.apply_execution_result(
            intent=approved.intent,
            result=result,
            signal=price_signal,
            portfolio=working_portfolio,
            risk_reason=approved.reason,
        )
        repo.portfolio = updated_portfolio
    orchestrator.sync_execution_compensations()
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


@app.post("/ops/backtest/run")
async def backtest_run(request: BacktestRequest) -> dict:
    payload = backtest_engine.run(repo, request).model_dump(mode="json")
    logger.info(
        "backtest_run",
        params_version_id=payload["params_version_id"],
        total_trades=payload["total_trades"],
        score=payload["score"],
    )
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


@app.get("/ops/optimization/leaderboard")
async def optimization_leaderboard(limit: int = 20) -> list[dict]:
    payload = [item.model_dump(mode="json") for item in repo.optimization_leaderboard(limit=limit)]
    logger.info("optimization_leaderboard", items=len(payload))
    return payload


@app.post("/ops/optimization/online/apply")
async def optimization_online_apply(window_minutes: int = 60, apply_best: bool = True) -> dict:
    payload = online_tuner.run_window(
        repo=repo,
        window_minutes=window_minutes,
        apply_best=apply_best,
        execution_dry_run=execution_engine.dry_run,
        run_profile=settings.run_profile,
    )
    if payload.get("applied") or payload.get("rolled_back"):
        _persist_current_params_to_path()
    logger.info(
        "optimization_online_apply",
        applied=payload["applied"],
        rolled_back=payload["rolled_back"],
        reason=payload["reason"],
    )
    return payload

