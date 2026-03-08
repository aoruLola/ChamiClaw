from datetime import date, datetime, timedelta, timezone

import asyncio

from chamiclaw.adapters.simmer import SimmerAdapter
from chamiclaw.adapters.base import ExecutionAdapter
from chamiclaw.clients.clob import CLOBClient
from chamiclaw.core.models import (
    Action,
    BatchTradeCandidate,
    ForecastConsensus,
    InfoSignal,
    LlmReviewDecision,
    MarketCard,
    Mode,
    ModeState,
    NormalizedMarketTick,
    OrderIntent,
    OrderStatus,
    OrderRecord,
    OrderMode,
    OrderType,
    Position,
    PositionSnapshot,
    BalanceSnapshot,
    ExecutionResult,
    PriceSnapshot,
    PriceSignal,
    Side,
    SpreadStatus,
    WeatherMarketMeta,
)
from chamiclaw.engines.execution import ExecutionEngine
from chamiclaw.engines.info import InfoEngine
from chamiclaw.engines.market import MarketService
from chamiclaw.engines.mode import ModeEngine
from chamiclaw.engines.phase_gate import PhaseGateService
from chamiclaw.engines.price import PriceEngine
from chamiclaw.engines.risk import RiskEngine
from chamiclaw.engines.strategy import StrategyEngine
from chamiclaw.orchestration.runtime import RuntimeOrchestrator
from chamiclaw.storage.repository import InMemoryRepository


class ReconcileAdapter(ExecutionAdapter):
    async def place_order(self, intent, *, dry_run: bool):
        raise NotImplementedError

    async def cancel_order(self, order_id: str):
        raise NotImplementedError

    async def fetch_order(self, order_id: str) -> OrderStatus:
        return OrderStatus(order_id=order_id, status="unknown")

    async def fetch_positions(self) -> list[PositionSnapshot]:
        return [PositionSnapshot(market_id="m1", side=Side.YES, size=10.0, avg_price=0.52, u_pnl=1.2)]

    async def fetch_balances(self) -> BalanceSnapshot:
        return BalanceSnapshot(cash=9500.0, equity=10020.0)


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
    assert len(repo.order_records) == 1
    assert len(repo.fill_records) == 1
    assert len(repo.portfolio.positions) == 1
    assert len(repo.positions_snapshots) == 1
    assert len(repo.trade_logs) == 1
    assert "slippage" in repo.trade_logs[0]


def test_runtime_blocks_mode_b_when_phase_gate_not_open():
    repo = InMemoryRepository()
    repo.phase_gate_state.allowed_mode_b = False
    repo.upsert_market(
        MarketCard(
            market_id="m2",
            question="Q2",
            end_time=datetime.now(timezone.utc) + timedelta(days=1),
            status="active",
            rule_clarity_score=0.9,
        )
    )
    repo.put_mode_state(ModeState(market_id="m2", mode=Mode.MODE_B_ALLOWED))
    repo.put_price_signal(
        PriceSignal(
            market_id="m2",
            change_15m=0.04,
            vol_ratio_15m=2.2,
            breakout_15m=True,
            mid=0.5,
            spread=0.01,
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
    )
    executed = asyncio.run(orchestrator.strategy_loop())
    assert executed == 0


def test_runtime_strategy_loop_closes_existing_position_and_realizes_pnl():
    repo = InMemoryRepository()
    repo.portfolio.positions = [Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.50, u_pnl=0.0)]
    repo.portfolio.cash = 9_950.0
    repo.portfolio.equity = 10_000.0
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    repo.put_price_signal(
        PriceSignal(
            market_id="m1",
            change_5m=0.02,
            vol_ratio_15m=1.3,
            spread=0.01,
            mid=0.52,
            spread_status=SpreadStatus.stable,
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
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    executed = asyncio.run(orchestrator.strategy_loop())

    assert executed == 1
    assert len(repo.order_records) == 1
    assert repo.order_records[0].action == Action.CLOSE
    assert len(repo.fill_records) == 1
    assert repo.portfolio.positions == []
    assert repo.portfolio.realized_pnl > 0
    assert repo.trade_stats.total_trades == 1
    assert repo.trade_stats.wins == 1
    assert repo.trade_stats.losses == 0
    assert repo.trade_stats.gross_profit > 0
    assert repo.trade_stats.gross_loss == 0


def test_runtime_strategy_loop_closes_existing_no_position_with_no_side():
    repo = InMemoryRepository()
    repo.portfolio.positions = [Position(market_id="m1", side=Side.NO, size=100.0, avg_price=0.50, u_pnl=0.0)]
    repo.portfolio.cash = 9_950.0
    repo.portfolio.equity = 10_000.0
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    repo.put_price_signal(
        PriceSignal(
            market_id="m1",
            change_5m=-0.02,
            vol_ratio_15m=1.3,
            spread=0.01,
            mid=0.48,
            spread_status=SpreadStatus.stable,
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
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    executed = asyncio.run(orchestrator.strategy_loop())

    assert executed == 1
    assert len(repo.order_records) == 1
    assert repo.order_records[0].action == Action.CLOSE
    assert repo.order_records[0].side == Side.NO


def test_runtime_strategy_loop_auto_evaluates_phase_gate_after_trade():
    repo = InMemoryRepository()
    repo.phase_gate_state.allowed_mode_b = False
    repo.portfolio.positions = [Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.50, u_pnl=0.0)]
    repo.portfolio.cash = 9_950.0
    repo.portfolio.equity = 10_000.0
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    repo.put_price_signal(
        PriceSignal(
            market_id="m1",
            change_5m=0.02,
            vol_ratio_15m=1.3,
            spread=0.01,
            mid=0.52,
            spread_status=SpreadStatus.stable,
        )
    )
    gate = PhaseGateService(min_trades=1, min_win_rate=0.0, min_rr=0.0, max_drawdown=1.0)
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        phase_gate_service=gate,
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    executed = asyncio.run(orchestrator.strategy_loop())

    assert executed == 1
    assert repo.phase_gate_state.allowed_mode_b is True


def test_runtime_reconcile_execution_state_updates_portfolio_and_snapshots():
    repo = InMemoryRepository()
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=ReconcileAdapter()),
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    updated = asyncio.run(orchestrator.reconcile_execution_state())

    assert updated.equity == 10020.0
    assert updated.cash == 9500.0
    assert len(updated.positions) == 1
    assert len(repo.positions_snapshots) == 1


def test_runtime_reconcile_order_statuses_updates_order_and_backfills_fill():
    repo = InMemoryRepository()
    repo.order_records.append(
        OrderRecord(
            order_id="o-live",
            market_id="m1",
            side=Side.YES,
            action=Action.OPEN,
            order_type=OrderType.LIMIT,
            limit_price=0.50,
            size_usd=100.0,
            status="submitted",
            adapter="SimmerAdapter",
            mode=OrderMode.A,
            dry_run=False,
        )
    )
    repo.portfolio.cash = 10_000.0
    repo.portfolio.equity = 10_000.0
    repo.put_price_snapshot(
        PriceSnapshot(
            market_id="m1",
            best_bid=0.49,
            best_ask=0.51,
            mid=0.50,
            spread=0.02,
            last=0.50,
            volume_1m=0.0,
            trades_1m=0,
        )
    )

    class StatusAdapter(ReconcileAdapter):
        async def fetch_order(self, order_id: str) -> OrderStatus:
            return OrderStatus(
                order_id=order_id,
                status="filled",
                raw={"fill_price": 0.50, "fill_size": 200.0, "fee": 1.5},
            )

    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=StatusAdapter()),
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    reconciled = asyncio.run(orchestrator.reconcile_order_statuses(limit=10))

    assert reconciled == 1
    assert repo.order_records[0].status == "filled"
    assert len(repo.fill_records) == 1
    assert repo.fill_records[0].order_id == "o-live"


def test_runtime_handle_market_tick_persists_snapshot_and_signal():
    repo = InMemoryRepository()
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

    tick = NormalizedMarketTick(
        market_id="m1",
        best_bid=0.49,
        best_ask=0.51,
        last=0.5,
        volume_1m=12.0,
        trades_1m=3,
    )

    signal = orchestrator.handle_market_tick(tick)

    assert repo.price_snapshots["m1"].best_bid == 0.49
    assert repo.price_signals["m1"].market_id == "m1"
    assert signal.market_id == "m1"


def test_runtime_anomaly_triggers_info_refresh_with_debounce():
    repo = InMemoryRepository()
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
    calls = {"count": 0}

    def fake_info_refresh(*, anomaly_only: bool = False) -> int:
        if anomaly_only:
            calls["count"] += 1
        return 0

    orchestrator.info_refresh = fake_info_refresh  # type: ignore[assignment]
    tick = NormalizedMarketTick(
        market_id="m1",
        best_bid=0.40,
        best_ask=0.80,
        last=0.60,
        volume_1m=10.0,
        trades_1m=2,
    )

    orchestrator.handle_market_tick(tick)
    orchestrator.handle_market_tick(tick)
    assert calls["count"] == 1


def test_runtime_price_stream_consumes_ws_messages_and_persists():
    repo = InMemoryRepository()
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")

    async def fake_stream(_market_ids, **_kwargs):
        yield {"type": "book", "market_id": "m1", "best_bid": 0.48, "best_ask": 0.52, "last": 0.5, "volume_1m": 1.0, "trades_1m": 1}
        yield {"type": "book", "market_id": "m1", "best_bid": 0.49, "best_ask": 0.51, "last": 0.5, "volume_1m": 2.0, "trades_1m": 2}
        yield {"type": "book", "market_id": "m1", "best_bid": 0.50, "best_ask": 0.52, "last": 0.51, "volume_1m": 3.0, "trades_1m": 3}

    client.stream_orderbook = fake_stream  # type: ignore[assignment]
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        price_engine=PriceEngine(),
        clob_client=client,
        ws_backoff_base_seconds=0.01,
    )

    async def run_and_stop():
        task = asyncio.create_task(orchestrator.run_price_stream(["m1"]))
        while len(repo.price_signal_events) < 3:
            await asyncio.sleep(0.01)
        orchestrator.request_price_stream_stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(run_and_stop())

    assert len(repo.price_signal_events) == 3
    assert len(repo.price_snapshots) == 1


def test_runtime_price_stream_reconnect_triggers_rest_backfill():
    repo = InMemoryRepository()
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    calls = {"backfill": 0}

    async def fake_fetch_top_of_book(_market_id: str):
        calls["backfill"] += 1
        return {"best_bid": 0.47, "best_ask": 0.53, "last": 0.5}

    async def fake_stream(_market_ids, **_kwargs):
        client.reconnect_count = 1
        yield {
            "type": "book",
            "market_id": "m1",
            "best_bid": 0.48,
            "best_ask": 0.52,
            "last": 0.5,
            "volume_1m": 1.0,
            "trades_1m": 1,
        }

    client.fetch_top_of_book = fake_fetch_top_of_book  # type: ignore[assignment]
    client.stream_orderbook = fake_stream  # type: ignore[assignment]
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        price_engine=PriceEngine(),
        clob_client=client,
        ws_backoff_base_seconds=0.01,
    )

    async def run_and_stop():
        task = asyncio.create_task(orchestrator.run_price_stream(["m1"]))
        while len(repo.price_signal_events) < 1:
            await asyncio.sleep(0.01)
        orchestrator.request_price_stream_stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(run_and_stop())
    assert calls["backfill"] >= 1


def test_runtime_price_stream_backfills_when_ws_cycle_has_no_payloads():
    repo = InMemoryRepository()
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    calls = {"backfill": 0, "stream_cycles": 0}

    async def fake_fetch_top_of_book(_market_id: str):
        calls["backfill"] += 1
        return {"best_bid": 0.47, "best_ask": 0.53, "last": 0.5}

    async def fake_stream(_market_ids, **_kwargs):
        calls["stream_cycles"] += 1
        client.reconnect_count = calls["stream_cycles"]
        if False:
            yield {}

    client.fetch_top_of_book = fake_fetch_top_of_book  # type: ignore[assignment]
    client.stream_orderbook = fake_stream  # type: ignore[assignment]
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        price_engine=PriceEngine(),
        clob_client=client,
        ws_backoff_base_seconds=0.01,
    )

    async def run_and_stop():
        task = asyncio.create_task(orchestrator.run_price_stream(["m1"]))
        for _ in range(100):
            if calls["backfill"] > 0:
                break
            await asyncio.sleep(0.01)
        orchestrator.request_price_stream_stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(run_and_stop())
    assert calls["backfill"] >= 1
    assert "m1" in repo.price_snapshots


def test_runtime_price_stream_flushes_by_window():
    repo = InMemoryRepository()
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")

    async def fake_stream(_market_ids, **_kwargs):
        yield {
            "type": "book",
            "market_id": "m1",
            "best_bid": 0.48,
            "best_ask": 0.52,
            "last": 0.5,
            "volume_1m": 1.0,
            "trades_1m": 1,
            "ts": "2026-01-01T00:00:00+00:00",
        }
        yield {
            "type": "book",
            "market_id": "m1",
            "best_bid": 0.49,
            "best_ask": 0.51,
            "last": 0.5,
            "volume_1m": 2.0,
            "trades_1m": 2,
            "ts": "2026-01-01T00:00:10+00:00",
        }
        yield {
            "type": "book",
            "market_id": "m1",
            "best_bid": 0.50,
            "best_ask": 0.52,
            "last": 0.51,
            "volume_1m": 3.0,
            "trades_1m": 3,
            "ts": "2026-01-01T00:00:20+00:00",
        }

    client.stream_orderbook = fake_stream  # type: ignore[assignment]
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        price_engine=PriceEngine(),
        clob_client=client,
        ws_backoff_base_seconds=0.01,
        price_flush_seconds=30,
    )

    async def run_and_stop():
        task = asyncio.create_task(orchestrator.run_price_stream(["m1"]))
        while len(repo.price_signal_events) < 1:
            await asyncio.sleep(0.01)
        orchestrator.request_price_stream_stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(run_and_stop())
    assert len(repo.price_signal_events) == 1
    assert repo.price_snapshots["m1"].best_bid == 0.50


def test_runtime_bootstrap_market_pool_fetches_and_upserts():
    repo = InMemoryRepository()
    card = MarketCard(
        market_id="gm1",
        question="Will X happen?",
        end_time=datetime.now(timezone.utc) + timedelta(days=1),
        status="active",
        liquidity_score=0.7,
        spread_stability=0.7,
        volume_density=0.7,
        rule_clarity_score=0.8,
    )

    class FakeMarketService(MarketService):
        async def refresh_pool(self, top_n: int = 10):
            assert top_n == 5
            return [card]

    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=FakeMarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    async def run_bootstrap():
        return await orchestrator.bootstrap_market_pool(top_n=5)

    subscriptions = asyncio.run(run_bootstrap())
    assert subscriptions == ["gm1"]
    assert "gm1" in repo.markets


def test_runtime_strategy_loop_respects_rate_limit_per_market_per_minute():
    fixed_now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now_fn() -> datetime:
        return fixed_now

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
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
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
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        execution_rate_limit_per_market_per_minute=1,
        execution_rate_limit_global_per_minute=10,
        now_fn=now_fn,
    )

    first = asyncio.run(orchestrator.strategy_loop())
    repo.portfolio.positions = []
    second = asyncio.run(orchestrator.strategy_loop())

    assert first == 1
    assert second == 0


def test_runtime_can_sync_and_restore_execution_compensations():
    repo = InMemoryRepository()
    failing_engine = ExecutionEngine(adapter=SimmerAdapter(), dry_run=True)
    key_intent = OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=10.0,
        mode=OrderMode.A,
        thesis="seed",
        idempotency_key="intent-seed",
    )
    # seed via repo path, then restore into engine
    repo.upsert_execution_compensation("intent-seed", key_intent)
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=failing_engine,
    )

    restored = orchestrator.restore_execution_compensations()
    synced = orchestrator.sync_execution_compensations()

    assert restored == 1
    assert synced["pending"] == 1


def test_runtime_apply_execution_result_records_trade_and_evaluates_phase_gate():
    repo = InMemoryRepository()
    repo.phase_gate_state.allowed_mode_b = False
    repo.portfolio.positions = [Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.50, u_pnl=0.0)]
    repo.portfolio.cash = 9_950.0
    repo.portfolio.equity = 10_000.0
    gate = PhaseGateService(min_trades=1, min_win_rate=0.0, min_rr=0.0, max_drawdown=1.0)
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        phase_gate_service=gate,
        price_engine=PriceEngine(),
        clob_client=CLOBClient(rest_url="https://rest", ws_url="wss://ws"),
    )

    intent = OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.CLOSE,
        order_type=OrderType.LIMIT,
        limit_price=0.52,
        size_usd=52.0,
        mode=OrderMode.A,
        thesis="test-close",
    )
    result = ExecutionResult(accepted=True, order_id="o-close", status="simulated", dry_run=True)
    signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=0.02,
        vol_ratio_15m=1.3,
        mid=0.52,
        spread_status=SpreadStatus.stable,
    )

    updated = orchestrator.apply_execution_result(
        intent=intent,
        result=result,
        signal=signal,
        portfolio=repo.portfolio,
        risk_reason="approved",
    )

    assert updated.positions == []
    assert len(repo.order_records) == 1
    assert len(repo.fill_records) == 1
    assert len(repo.trade_logs) == 1
    assert repo.trade_stats.total_trades == 1
    assert repo.trade_stats.wins == 1
    assert repo.phase_gate_state.allowed_mode_b is True



class ApproveReviewClient:
    async def review_trade(self, _request):
        return LlmReviewDecision(decision="approve", size_multiplier=1.0, confidence=0.91, risk_tags=[], reason_summary="ok")


class ResizeReviewClient:
    async def review_trade(self, _request):
        return LlmReviewDecision(decision="resize", size_multiplier=0.5, confidence=0.84, risk_tags=["forecast_divergence"], reason_summary="reduce")


class FailingReviewClient:
    async def review_trade(self, _request):
        raise ValueError("bad response")


class WeatherInfoEngine(InfoEngine):
    async def fetch_weather_signal(self, meta: WeatherMarketMeta, *, forecast_date: date):
        return InfoSignal(
            market_id=meta.market_id,
            risk_score=0.2,
            confirmation_level=2,
            forecast_consensus=ForecastConsensus(
                market_id=meta.market_id,
                location=meta.location,
                forecast_date=forecast_date,
                consensus_probability=0.66,
                confidence=0.78,
                dispersion=0.09,
                freshness_minutes=18,
            ),
        )


class WeatherMarketService(MarketService):
    def extract_weather_markets(self, cards: list[MarketCard], top_n: int = 10):
        _ = cards, top_n
        return [WeatherMarketMeta(market_id="m1", question="Will it rain in NYC tomorrow?", location="New York, NY")]


def test_runtime_info_refresh_weather_fetches_signals_for_weather_markets():
    repo = InMemoryRepository()
    repo.upsert_market(
        MarketCard(
            market_id="m1",
            question="Will it rain in New York, NY tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(days=1),
            status="active",
            rule_text="Official NOAA precipitation observation.",
            rule_summary="New York, NY",
            resolution_sources=["NOAA"],
            rule_clarity_score=0.95,
            liquidity_score=0.8,
            spread_stability=0.8,
            volume_density=0.8,
        )
    )
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=WeatherMarketService(),
        info_engine=WeatherInfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
    )

    refreshed = asyncio.run(orchestrator.info_refresh_weather())

    assert refreshed == 1
    assert repo.info_signals["m1"].forecast_consensus is not None
    assert repo.info_signals["m1"].forecast_consensus.consensus_probability == 0.66


def test_runtime_review_weather_candidates_applies_resize():
    repo = InMemoryRepository()
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        llm_review_client=ResizeReviewClient(),
    )
    candidates = [
        BatchTradeCandidate(
            market_id="m1",
            market_question="Will it rain in NYC?",
            market_probability=0.40,
            consensus_probability=0.70,
            consensus_confidence=0.80,
            edge=0.30,
            suggested_size_usd=40.0,
            weather_meta=WeatherMarketMeta(market_id="m1", question="Will it rain in NYC?", location="New York, NY", rule_text="official source"),
        )
    ]

    reviewed = asyncio.run(orchestrator.review_weather_candidates(candidates))

    assert len(reviewed) == 1
    assert reviewed[0].suggested_size_usd == 20.0


def test_runtime_review_weather_candidates_rejects_on_llm_failure_when_failsafe_is_reject():
    repo = InMemoryRepository()
    orchestrator = RuntimeOrchestrator(
        repo=repo,
        market_service=MarketService(),
        info_engine=InfoEngine(),
        mode_engine=ModeEngine(),
        strategy_engine=StrategyEngine(),
        risk_engine=RiskEngine(),
        execution_engine=ExecutionEngine(adapter=SimmerAdapter()),
        llm_review_client=FailingReviewClient(),
        llm_failsafe_mode="reject",
    )
    candidates = [
        BatchTradeCandidate(
            market_id="m1",
            market_question="Will it rain in NYC?",
            market_probability=0.40,
            consensus_probability=0.70,
            consensus_confidence=0.80,
            edge=0.30,
            suggested_size_usd=40.0,
            weather_meta=WeatherMarketMeta(market_id="m1", question="Will it rain in NYC?", location="New York, NY", rule_text="official source"),
        )
    ]

    reviewed = asyncio.run(orchestrator.review_weather_candidates(candidates))

    assert reviewed == []


def test_runtime_run_weather_batch_executes_reviewed_candidates():
    repo = InMemoryRepository()
    repo.upsert_market(
        MarketCard(
            market_id="m1",
            question="Will it rain in NYC tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(days=1),
            status="active",
            rule_text="Official NOAA precipitation measurement determines outcome.",
            rule_summary="New York, NY",
            liquidity_score=0.8,
            spread_stability=0.8,
            volume_density=0.8,
            rule_clarity_score=0.9,
        )
    )
    repo.put_price_snapshot(
        PriceSnapshot(market_id="m1", best_bid=0.39, best_ask=0.41, mid=0.40, spread=0.02, last=0.40)
    )
    repo.put_info_signal(
        InfoSignal(
            market_id="m1",
            risk_score=0.2,
            confirmation_level=2,
            forecast_consensus=ForecastConsensus(
                market_id="m1",
                location="New York, NY",
                forecast_date=datetime.now(timezone.utc).date(),
                consensus_probability=0.70,
                confidence=0.80,
                dispersion=0.1,
                freshness_minutes=20,
            ),
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
        llm_review_client=ApproveReviewClient(),
    )

    summary = asyncio.run(orchestrator.run_weather_batch(max_candidates=5, per_market_cap_usd=40.0))

    assert summary["candidates"] == 1
    assert summary["reviewed"] == 1
    assert summary["executed"] == 1
    assert len(repo.order_records) == 1

