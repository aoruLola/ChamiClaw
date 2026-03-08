from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from chamiclaw.clients.clob import CLOBClient
from chamiclaw.core.models import (
    Action,
    BatchTradeCandidate,
    ExecutionResult,
    FillRecord,
    LlmReviewRequest,
    Mode,
    NormalizedMarketTick,
    OrderIntent,
    OrderMode,
    OrderRecord,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    PriceSignal,
    Side,
    StrategyParams,
    WeatherMarketMeta,
)
from chamiclaw.engines.price import PriceEngine
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.phase_gate import PhaseGateService
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.portfolio import PortfolioEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.obs.logging import get_logger
from chamiclaw.storage.repository import Repository

logger = get_logger("chamiclaw.runtime")


class RuntimeOrchestrator:
    """Coordinates periodic loops for T1 runtime wiring."""

    def __init__(
        self,
        repo: Repository,
        market_service: MarketService,
        info_engine: InfoEngine,
        mode_engine: ModeEngine,
        strategy_engine: StrategyEngine,
        risk_engine: RiskEngine,
        execution_engine: ExecutionEngine,
        portfolio_engine: PortfolioEngine | None = None,
        phase_gate_service: PhaseGateService | None = None,
        price_engine: PriceEngine | None = None,
        clob_client: CLOBClient | None = None,
        ws_max_retries: int = 10,
        ws_backoff_base_seconds: float = 1.0,
        ws_backoff_max_seconds: float = 30.0,
        ws_stale_timeout_seconds: int = 90,
        price_flush_seconds: int = 30,
        anomaly_debounce_seconds: int = 30,
        execution_rate_limit_per_market_per_minute: int = 3,
        execution_rate_limit_global_per_minute: int = 20,
        llm_review_client: object | None = None,
        llm_failsafe_mode: str = 'reject',
        weather_batch_max_orders: int = 6,
        weather_max_batch_risk_usd: float = 200.0,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.repo = repo
        self.market_service = market_service
        self.info_engine = info_engine
        self.mode_engine = mode_engine
        self.strategy_engine = strategy_engine
        self.risk_engine = risk_engine
        self.execution_engine = execution_engine
        self.portfolio_engine = portfolio_engine or PortfolioEngine()
        self.phase_gate_service = phase_gate_service
        self.price_engine = price_engine or PriceEngine()
        self.clob_client = clob_client
        self.ws_max_retries = ws_max_retries
        self.ws_backoff_base_seconds = ws_backoff_base_seconds
        self.ws_backoff_max_seconds = ws_backoff_max_seconds
        self.ws_stale_timeout_seconds = ws_stale_timeout_seconds
        self.price_flush_seconds = max(price_flush_seconds, 0)
        self.anomaly_debounce_seconds = max(anomaly_debounce_seconds, 1)
        self.execution_rate_limit_per_market_per_minute = max(execution_rate_limit_per_market_per_minute, 1)
        self.execution_rate_limit_global_per_minute = max(execution_rate_limit_global_per_minute, 1)
        self.llm_review_client = llm_review_client
        self.llm_failsafe_mode = llm_failsafe_mode
        self.weather_batch_max_orders = max(weather_batch_max_orders, 1)
        self.weather_max_batch_risk_usd = max(weather_max_batch_risk_usd, 0.0)
        self.last_weather_batch_summary: dict[str, int | float] = {
            'candidates': 0,
            'reviewed': 0,
            'executed': 0,
            'rejected': 0,
        }
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._price_stream_stop = asyncio.Event()
        self._last_anomaly_refresh_ts: datetime | None = None
        self._position_opened_at: dict[str, datetime] = {}
        self._market_order_tape: dict[str, deque[datetime]] = defaultdict(deque)
        self._global_order_tape: deque[datetime] = deque()

    def market_refresh(self) -> int:
        cards = list(self.repo.markets.values())
        ranked = self.market_service.rank_markets(cards, top_n=10) if cards else []
        for card in ranked:
            self.repo.upsert_market(card)
        return len(ranked)

    async def bootstrap_market_pool(self, top_n: int = 10) -> list[str]:
        refreshed = await self.market_service.refresh_pool(top_n=top_n)
        for card in refreshed:
            self.repo.upsert_market(card)
        return [card.market_id for card in refreshed]

    def info_refresh(self, anomaly_only: bool = False) -> int:
        count = 0
        market_ids = list(self.repo.markets.keys())
        if anomaly_only:
            market_ids = [m for m, s in self.repo.price_signals.items() if s.anomaly_flag]

        for market_id in market_ids:
            signal = self.info_engine.analyze(market_id=market_id, source_tiers=[1], event_detected=False)
            self.repo.put_info_signal(signal)
            count += 1
        return count

    async def info_refresh_weather(
        self,
        *,
        top_n: int | None = None,
        forecast_date: date | None = None,
    ) -> int:
        cards = list(self.repo.markets.values())
        if not cards:
            return 0
        weather_markets = self.market_service.extract_weather_markets(
            cards,
            top_n=top_n or max(len(cards), 1),
        )
        refreshed = 0
        default_forecast_date = forecast_date or self._now_fn().date()
        for meta in weather_markets:
            target_date = meta.settlement_date or default_forecast_date
            signal = await self.info_engine.fetch_weather_signal(meta, forecast_date=target_date)
            self.repo.put_info_signal(signal)
            refreshed += 1
        return refreshed

    def evaluate_phase_gate(self, admin_override: bool = False) -> None:
        if self.phase_gate_service is None:
            return
        state = self.phase_gate_service.evaluate(
            self.repo.trade_stats,
            max_drawdown_pct=self.repo.portfolio.max_drawdown_pct,
            current=self.repo.phase_gate_state,
            admin_override=admin_override,
        )
        self.repo.save_phase_gate(state)

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
        self.refresh_strategy_params()
        pf = portfolio or self.repo.portfolio
        executed = 0
        for market_id, mode_state in self.repo.mode_states.items():
            if mode_state.mode == Mode.NO_TRADE:
                continue
            price_signal = self.repo.price_signals.get(market_id)
            if price_signal is None:
                continue
            position_size, position_avg_price, position_side, held_minutes = self._position_context(pf, market_id)
            intent = self.strategy_engine.generate_intent(
                pf.equity,
                mode_state,
                price_signal,
                trade_stats=self.repo.trade_stats,
                position_size=position_size,
                position_avg_price=position_avg_price,
                position_side=position_side,
                held_minutes=held_minutes,
                allow_mode_b=self.repo.phase_gate_state.allowed_mode_b,
            )
            if intent is None:
                continue
            approved = self.risk_engine.validate(intent, pf)
            if approved.approved and approved.intent is not None:
                if not self._allow_execution_rate_limit(approved.intent.market_id, approved.intent.action):
                    continue
            result = await self.execution_engine.execute(approved)
            if approved.approved and result and result.accepted and approved.intent is not None:
                pf = self.apply_execution_result(
                    intent=approved.intent,
                    result=result,
                    signal=price_signal,
                    portfolio=pf,
                    risk_reason=approved.reason,
                    evaluate_phase_gate=False,
                )
                executed += 1
        if executed > 0:
            self.evaluate_phase_gate(admin_override=False)
        self.sync_execution_compensations()
        return executed

    def refresh_strategy_params(self) -> StrategyParams:
        current = self.repo.get_current_params()
        self.strategy_engine.configure(current.params)
        return current.params


    def collect_weather_candidates(
        self,
        *,
        max_candidates: int,
        per_market_cap_usd: float,
        portfolio: PortfolioState | None = None,
    ) -> list[BatchTradeCandidate]:
        markets: list[WeatherMarketMeta] = []
        consensuses: dict[str, object] = {}
        for card in self.repo.markets.values():
            info = self.repo.info_signals.get(card.market_id)
            if info is None or info.forecast_consensus is None:
                continue
            markets.append(
                WeatherMarketMeta(
                    market_id=card.market_id,
                    question=card.question,
                    location=card.rule_summary or card.question,
                    resolution_source=(card.resolution_sources[0] if card.resolution_sources else ''),
                    rule_text=card.rule_text,
                    active=card.status == 'active',
                )
            )
            consensuses[card.market_id] = info.forecast_consensus
        working_portfolio = portfolio or self.repo.portfolio
        return self.strategy_engine.rank_weather_candidates(
            markets,
            price_snapshots=self.repo.price_snapshots,
            consensuses=consensuses,
            portfolio_equity=working_portfolio.equity,
            max_candidates=max_candidates,
            per_market_cap_usd=per_market_cap_usd,
        )

    async def review_weather_candidates(self, candidates: list[BatchTradeCandidate]) -> list[BatchTradeCandidate]:
        if self.llm_review_client is None:
            return [candidate.model_copy(deep=True) for candidate in candidates]
        reviewed: list[BatchTradeCandidate] = []
        for candidate in candidates:
            meta = candidate.weather_meta or WeatherMarketMeta(market_id=candidate.market_id)
            request = LlmReviewRequest(
                market_id=candidate.market_id,
                market_question=candidate.market_question,
                market_rule=meta.rule_text,
                location=meta.location,
                forecast_date=meta.settlement_date or self._now_fn().date(),
                market_probability=candidate.market_probability,
                consensus_probability=candidate.consensus_probability,
                consensus_confidence=candidate.consensus_confidence,
                data_freshness_minutes=candidate.data_freshness_minutes,
                edge=candidate.edge,
                suggested_size_usd=candidate.suggested_size_usd,
                risk_tags=list(candidate.risk_tags),
            )
            try:
                decision = await self.llm_review_client.review_trade(request)
            except Exception:
                if self.llm_failsafe_mode == 'min_size':
                    resized = candidate.model_copy(deep=True)
                    resized.suggested_size_usd = round(max(resized.suggested_size_usd * 0.25, 0.0), 4)
                    if resized.suggested_size_usd > 0:
                        reviewed.append(resized)
                continue
            if decision.decision == 'reject':
                continue
            next_candidate = candidate.model_copy(deep=True)
            if decision.decision == 'resize':
                multiplier = max(min(decision.size_multiplier, 1.0), 0.0)
                next_candidate.suggested_size_usd = round(next_candidate.suggested_size_usd * multiplier, 4)
            if next_candidate.suggested_size_usd <= 0:
                continue
            reviewed.append(next_candidate)
        return reviewed

    async def run_weather_batch(
        self,
        *,
        max_candidates: int | None = None,
        per_market_cap_usd: float | None = None,
        portfolio: PortfolioState | None = None,
    ) -> dict[str, int | float]:
        working_portfolio = portfolio or self.repo.portfolio
        candidates = self.collect_weather_candidates(
            max_candidates=max_candidates or 12,
            per_market_cap_usd=per_market_cap_usd or 50.0,
            portfolio=working_portfolio,
        )
        reviewed = await self.review_weather_candidates(candidates)
        executable: list[BatchTradeCandidate] = []
        batch_risk_used = 0.0
        for candidate in reviewed:
            if len(executable) >= self.weather_batch_max_orders:
                break
            if self.weather_max_batch_risk_usd > 0 and batch_risk_used + candidate.suggested_size_usd > self.weather_max_batch_risk_usd:
                continue
            executable.append(candidate)
            batch_risk_used += candidate.suggested_size_usd
        reviewed = executable
        executed = 0
        for candidate in reviewed:
            intent = OrderIntent(
                market_id=candidate.market_id,
                side=Side.YES if candidate.edge >= 0 else Side.NO,
                action=Action.OPEN,
                order_type=OrderType.LIMIT,
                limit_price=candidate.market_probability,
                size_usd=candidate.suggested_size_usd,
                mode=OrderMode.A,
                thesis='WEATHER_BATCH edge entry',
                ttl_seconds=600,
            )
            approved = self.risk_engine.validate(intent, working_portfolio)
            if approved.approved and approved.intent is not None:
                if not self._allow_execution_rate_limit(approved.intent.market_id, approved.intent.action):
                    continue
            result = await self.execution_engine.execute(approved)
            if approved.approved and approved.intent is not None and result and result.accepted:
                signal = PriceSignal(
                    market_id=candidate.market_id,
                    spread=0.0,
                    mid=candidate.market_probability,
                )
                working_portfolio = self.apply_execution_result(
                    intent=approved.intent,
                    result=result,
                    signal=signal,
                    portfolio=working_portfolio,
                    risk_reason=approved.reason,
                    evaluate_phase_gate=False,
                )
                executed += 1
        if executed > 0:
            self.evaluate_phase_gate(admin_override=False)
        self.sync_execution_compensations()
        summary = {
            'candidates': len(candidates),
            'reviewed': len(reviewed),
            'executed': executed,
            'rejected': max(len(candidates) - len(reviewed), 0),
        }
        self.last_weather_batch_summary = summary
        return summary

    def apply_execution_result(
        self,
        *,
        intent: OrderIntent,
        result: ExecutionResult,
        signal: PriceSignal,
        portfolio: PortfolioState,
        risk_reason: str = "approved",
        evaluate_phase_gate: bool = True,
    ) -> PortfolioState:
        order = OrderRecord(
            order_id=result.order_id or "",
            market_id=intent.market_id,
            side=intent.side,
            action=intent.action,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
            size_usd=intent.size_usd,
            status=result.status,
            adapter=self.execution_engine.adapter.__class__.__name__,
            mode=intent.mode,
            dry_run=result.dry_run,
            idempotency_key=intent.idempotency_key,
            raw=result.raw,
        )
        self.repo.record_order(order)
        fill = self._build_fill(order=order, signal=signal)
        self.repo.record_fill(fill)
        snapshot = self.repo.price_snapshots.get(order.market_id)
        updated_portfolio, attribution = self.portfolio_engine.apply_fill(portfolio, order, fill, snapshot=snapshot)
        self.repo.portfolio = updated_portfolio
        self._update_position_opened_at(order)
        self.repo.record_positions_snapshot(updated_portfolio)
        save_fn = getattr(self.repo, "save_portfolio", None)
        if callable(save_fn):
            save_fn()
        realized_pnl_for_stats: float | None = None
        if order.action == Action.CLOSE and abs(attribution.actual_pnl) > 1e-9:
            realized_pnl_for_stats = attribution.actual_pnl
        self.repo.register_trade(order.mode.value, realized_pnl=realized_pnl_for_stats)
        if evaluate_phase_gate:
            self.evaluate_phase_gate(admin_override=False)
        self.repo.record_trade_log(
            {
                "ts": fill.ts.isoformat(),
                "market_id": order.market_id,
                "mode": order.mode.value,
                "action": order.action.value,
                "order_id": order.order_id,
                "fill_price": fill.fill_price,
                "fill_size": fill.fill_size,
                "fee": fill.fee,
                "pnl": attribution.actual_pnl,
                "theoretical_pnl": attribution.theoretical_pnl,
                "slippage": attribution.slippage,
                "spread_entry": attribution.spread_at_entry,
                "spread_exit": attribution.spread_at_exit,
                "reason_json": {"thesis": intent.thesis, "risk_reason": risk_reason},
            }
        )
        return updated_portfolio

    def restore_execution_compensations(self) -> int:
        stored = getattr(self.repo, "execution_compensations", {})
        if not isinstance(stored, dict):
            return 0
        return self.execution_engine.load_compensations(stored)

    def sync_execution_compensations(self) -> dict[str, int]:
        pending = self.execution_engine.export_compensations()
        stored = getattr(self.repo, "execution_compensations", {})
        existing_keys = list(stored.keys()) if isinstance(stored, dict) else []
        for key, intent in pending.items():
            self.repo.upsert_execution_compensation(key, intent)
        for key in existing_keys:
            if key not in pending:
                self.repo.delete_execution_compensation(key)
        current = getattr(self.repo, "execution_compensations", {})
        current_size = len(current) if isinstance(current, dict) else 0
        return {"pending": len(pending), "stored": current_size}

    async def reconcile_execution_state(self) -> PortfolioState:
        balances, positions = await self.execution_engine.sync_account_state()
        self.repo.portfolio.cash = balances.cash
        self.repo.portfolio.equity = balances.equity
        self.repo.portfolio.positions = [
            Position(
                market_id=pos.market_id,
                side=pos.side,
                size=pos.size,
                avg_price=pos.avg_price,
                u_pnl=pos.u_pnl,
            )
            for pos in positions
        ]
        self.repo.portfolio.unrealized_pnl = sum(p.u_pnl for p in self.repo.portfolio.positions)
        self.repo.record_positions_snapshot(self.repo.portfolio)
        save_fn = getattr(self.repo, "save_portfolio", None)
        if callable(save_fn):
            save_fn()
        return self.repo.portfolio

    async def reconcile_order_statuses(self, limit: int = 50) -> int:
        terminal = {"filled", "cancelled", "rejected", "expired", "simulated", "simulated-filled"}
        reconciled = 0
        candidates = list(self.repo.order_records)[-max(limit, 1) :]
        for order in candidates:
            if order.status.lower() in terminal:
                continue
            try:
                status = await self.execution_engine.fetch_order_status(order.order_id)
            except Exception as exc:
                logger.warning("order_reconcile_fetch_failed", order_id=order.order_id, error=str(exc))
                continue

            if status.status != order.status or status.raw:
                if self.repo.update_order_status(order.order_id, status.status, raw=status.raw):
                    reconciled += 1

            if status.status.lower() != "filled":
                continue
            if self.repo.has_fill(order.order_id):
                continue

            fill = self._fill_from_order_status(order, status)
            self.repo.record_fill(fill)
            snapshot = self.repo.price_snapshots.get(order.market_id)
            portfolio, attribution = self.portfolio_engine.apply_fill(self.repo.portfolio, order, fill, snapshot=snapshot)
            self.repo.record_positions_snapshot(portfolio)
            self._update_position_opened_at(order)
            save_fn = getattr(self.repo, "save_portfolio", None)
            if callable(save_fn):
                save_fn()
            self.repo.record_trade_log(
                {
                    "ts": fill.ts.isoformat(),
                    "market_id": order.market_id,
                    "mode": order.mode.value,
                    "action": order.action.value,
                    "order_id": order.order_id,
                    "fill_price": fill.fill_price,
                    "fill_size": fill.fill_size,
                    "fee": fill.fee,
                    "pnl": attribution.actual_pnl,
                    "theoretical_pnl": attribution.theoretical_pnl,
                    "slippage": attribution.slippage,
                    "spread_entry": attribution.spread_at_entry,
                    "spread_exit": attribution.spread_at_exit,
                    "reason_json": {"source": "order_reconcile"},
                }
            )
        return reconciled

    def refresh_market_subscriptions(self) -> list[str]:
        self.market_refresh()
        ranked = sorted(self.repo.markets.values(), key=lambda card: card.market_score, reverse=True)
        return [card.market_id for card in ranked[:10]]

    def handle_market_tick(self, tick: NormalizedMarketTick) -> PriceSignal:
        snapshot, signal = self.price_engine.on_quote(
            market_id=tick.market_id,
            best_bid=tick.best_bid,
            best_ask=tick.best_ask,
            last=tick.last,
            volume_1m=tick.volume_1m,
            trades_1m=tick.trades_1m,
        )
        snapshot.ts = tick.ts
        signal.ts = tick.ts
        self.repo.put_price_snapshot(snapshot)
        self.repo.put_price_signal(signal)
        self.repo.update_price_stream_state(last_event_ts=tick.ts)
        self._trigger_anomaly_info_refresh_if_needed(signal, tick.ts)
        return signal

    async def run_price_stream(self, market_ids: list[str]) -> None:
        if self.clob_client is None:
            logger.warning("price_stream_skipped", reason="missing_clob_client")
            return
        self._price_stream_stop.clear()
        self.repo.update_price_stream_state(
            running=True,
            reconnects=self.clob_client.reconnect_count,
        )
        active_market_ids = list(market_ids)
        logger.info("price_stream_started", market_count=len(active_market_ids))

        last_reconnect_count = self.clob_client.reconnect_count
        dropped = 0
        processed = 0
        pending_ticks: dict[str, NormalizedMarketTick] = {}
        window_start_ts: dict[str, datetime] = {}
        try:
            while not self._price_stream_stop.is_set():
                if not active_market_ids:
                    active_market_ids = self.refresh_market_subscriptions()
                    if not active_market_ids:
                        await asyncio.sleep(self.ws_backoff_base_seconds)
                        continue
                async for payload in self.clob_client.stream_orderbook(
                    active_market_ids,
                    max_retries=self.ws_max_retries,
                    backoff_base_seconds=self.ws_backoff_base_seconds,
                    backoff_max_seconds=self.ws_backoff_max_seconds,
                    stale_timeout_seconds=self.ws_stale_timeout_seconds,
                ):
                    if self._price_stream_stop.is_set():
                        break

                    reconnects = self.clob_client.reconnect_count
                    if reconnects > last_reconnect_count:
                        await self._rest_backfill_after_reconnect(active_market_ids)
                        last_reconnect_count = reconnects
                        self.repo.update_price_stream_state(reconnects=reconnects)
                        logger.warning("price_stream_reconnected", reconnects=reconnects)

                    tick = self.clob_client.normalize_ws_event(payload)
                    if tick is None:
                        dropped += 1
                        continue
                    flushed = self._collect_and_flush_tick(
                        tick=tick,
                        pending_ticks=pending_ticks,
                        window_start_ts=window_start_ts,
                    )
                    if flushed:
                        processed += flushed
                        self.repo.update_price_stream_state(
                            reconnects=self.clob_client.reconnect_count,
                            last_event_ts=tick.ts,
                        )
                    if processed > 0 and processed % 100 == 0:
                        logger.info(
                            "price_stream_progress",
                            processed=processed,
                            dropped=dropped,
                            reconnects=self.clob_client.reconnect_count,
                        )
                reconnects_after_cycle = self.clob_client.reconnect_count
                if reconnects_after_cycle > last_reconnect_count:
                    await self._rest_backfill_after_reconnect(active_market_ids)
                    last_reconnect_count = reconnects_after_cycle
                    self.repo.update_price_stream_state(reconnects=reconnects_after_cycle)
                    logger.warning("price_stream_reconnected", reconnects=reconnects_after_cycle)
                if not self._price_stream_stop.is_set():
                    processed += self._flush_pending_ticks(
                        pending_ticks=pending_ticks,
                        window_start_ts=window_start_ts,
                    )
                    active_market_ids = self.refresh_market_subscriptions() or active_market_ids
                    logger.warning("price_stream_loop_restarting", reconnects=self.clob_client.reconnect_count)
                    await asyncio.sleep(self.ws_backoff_base_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("price_stream_unhandled_error", error=str(exc))
        finally:
            processed += self._flush_pending_ticks(
                pending_ticks=pending_ticks,
                window_start_ts=window_start_ts,
            )
            self.repo.update_price_stream_state(
                running=False,
                reconnects=self.clob_client.reconnect_count if self.clob_client else 0,
            )
            logger.info("price_stream_stopped", processed=processed, dropped=dropped)

    def request_price_stream_stop(self) -> None:
        self._price_stream_stop.set()

    def _trigger_anomaly_info_refresh_if_needed(self, signal: PriceSignal, event_ts: datetime) -> None:
        if not signal.anomaly_flag:
            return
        now_ts = event_ts if event_ts.tzinfo is not None else event_ts.replace(tzinfo=timezone.utc)
        if self._last_anomaly_refresh_ts is None:
            should_refresh = True
        else:
            elapsed = (now_ts - self._last_anomaly_refresh_ts).total_seconds()
            should_refresh = elapsed >= self.anomaly_debounce_seconds
        if should_refresh:
            self.info_refresh(anomaly_only=True)
            self._last_anomaly_refresh_ts = now_ts

    async def _rest_backfill_after_reconnect(self, market_ids: list[str]) -> None:
        if self.clob_client is None:
            return
        for market_id in market_ids:
            try:
                payload = await self.clob_client.fetch_top_of_book(market_id)
            except Exception as exc:
                logger.warning("price_stream_backfill_failed", market_id=market_id, error=str(exc))
                continue
            if not isinstance(payload, dict):
                continue
            fallback_payload = {
                "type": "book",
                "market_id": market_id,
                "best_bid": payload.get("best_bid") or payload.get("bid"),
                "best_ask": payload.get("best_ask") or payload.get("ask"),
                "last": payload.get("last") or payload.get("mid") or payload.get("price"),
                "volume_1m": payload.get("volume_1m") or 0.0,
                "trades_1m": payload.get("trades_1m") or 0,
                "ts": payload.get("ts"),
            }
            tick = self.clob_client.normalize_ws_event(fallback_payload)
            if tick is not None:
                self.handle_market_tick(tick)

    def _collect_and_flush_tick(
        self,
        *,
        tick: NormalizedMarketTick,
        pending_ticks: dict[str, NormalizedMarketTick],
        window_start_ts: dict[str, datetime],
    ) -> int:
        market_id = tick.market_id
        if market_id not in window_start_ts:
            window_start_ts[market_id] = tick.ts
        pending_ticks[market_id] = tick

        if self.price_flush_seconds <= 0:
            self.handle_market_tick(pending_ticks.pop(market_id))
            window_start_ts[market_id] = tick.ts
            return 1

        elapsed = (tick.ts - window_start_ts[market_id]).total_seconds()
        if elapsed >= self.price_flush_seconds:
            self.handle_market_tick(pending_ticks.pop(market_id))
            window_start_ts[market_id] = tick.ts
            return 1
        return 0

    def _flush_pending_ticks(
        self,
        *,
        pending_ticks: dict[str, NormalizedMarketTick],
        window_start_ts: dict[str, datetime],
    ) -> int:
        if not pending_ticks:
            return 0
        flushed = 0
        for market_id, tick in list(pending_ticks.items()):
            self.handle_market_tick(tick)
            flushed += 1
            pending_ticks.pop(market_id, None)
            window_start_ts[market_id] = tick.ts
        return flushed

    def _position_context(self, portfolio: PortfolioState, market_id: str) -> tuple[float, float | None, str | None, int]:
        for pos in portfolio.positions:
            if pos.market_id != market_id:
                continue
            key = self._position_key(market_id, pos.side.value)
            opened_at = self._position_opened_at.get(key)
            if opened_at is None:
                opened_at = datetime.now(timezone.utc)
                self._position_opened_at[key] = opened_at
            held_minutes = int(max(0.0, (datetime.now(timezone.utc) - opened_at).total_seconds() // 60))
            return pos.size, pos.avg_price, pos.side.value, held_minutes
        return 0.0, None, None, 0

    def _allow_execution_rate_limit(self, market_id: str, action: Action) -> bool:
        if action == Action.CLOSE:
            return True
        now = self._now_fn()
        cutoff = now - timedelta(seconds=60)
        market_tape = self._market_order_tape[market_id]
        while market_tape and market_tape[0] < cutoff:
            market_tape.popleft()
        while self._global_order_tape and self._global_order_tape[0] < cutoff:
            self._global_order_tape.popleft()
        if len(market_tape) >= self.execution_rate_limit_per_market_per_minute:
            return False
        if len(self._global_order_tape) >= self.execution_rate_limit_global_per_minute:
            return False
        market_tape.append(now)
        self._global_order_tape.append(now)
        return True

    @staticmethod
    def _build_fill(order: OrderRecord, signal: PriceSignal) -> FillRecord:
        fill_price = order.limit_price if order.limit_price is not None else signal.mid
        fill_price = float(fill_price if fill_price > 0 else signal.mid if signal.mid > 0 else 0.5)
        fill_size = order.size_usd / max(fill_price, 1e-9)
        raw_fee = order.raw.get("fee") if isinstance(order.raw, dict) else None
        try:
            fee = float(raw_fee) if raw_fee is not None else 0.0
        except (TypeError, ValueError):
            fee = 0.0
        return FillRecord(
            order_id=order.order_id,
            market_id=order.market_id,
            fill_price=fill_price,
            fill_size=fill_size,
            fee=fee,
            raw={"source": "runtime_synthetic_fill"},
        )

    @staticmethod
    def _fill_from_order_status(order: OrderRecord, status: OrderStatus) -> FillRecord:
        raw = status.raw if isinstance(status.raw, dict) else {}
        fill_price = RuntimeOrchestrator._float_or_none(raw.get("fill_price")) or RuntimeOrchestrator._float_or_none(
            raw.get("price")
        )
        if fill_price is None:
            fill_price = order.limit_price if order.limit_price is not None else 0.5
        fill_size = RuntimeOrchestrator._float_or_none(raw.get("fill_size")) or RuntimeOrchestrator._float_or_none(
            raw.get("size")
        )
        if fill_size is None:
            fill_size = order.size_usd / max(fill_price, 1e-9)
        fee = RuntimeOrchestrator._float_or_none(raw.get("fee")) or 0.0
        return FillRecord(
            order_id=order.order_id,
            market_id=order.market_id,
            fill_price=float(fill_price),
            fill_size=float(fill_size),
            fee=float(fee),
            raw={"source": "order_reconcile", "status_raw": raw},
        )

    def _update_position_opened_at(self, order: OrderRecord) -> None:
        key = self._position_key(order.market_id, order.side.value)
        if order.action.value == "OPEN":
            self._position_opened_at[key] = datetime.now(timezone.utc)
            return
        still_open = any(
            p.market_id == order.market_id and p.side.value == order.side.value and p.size > 0
            for p in self.repo.portfolio.positions
        )
        if not still_open:
            self._position_opened_at.pop(key, None)

    @staticmethod
    def _position_key(market_id: str, side: str) -> str:
        return f"{market_id}:{side}"

    @staticmethod
    def _float_or_none(value: object) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
