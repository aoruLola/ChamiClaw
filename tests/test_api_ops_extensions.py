import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from chamiclaw.api import app as app_module
from chamiclaw.api.app import app
from chamiclaw.core.models import (
    Action,
    Mode,
    ModeState,
    NormalizedMarketTick,
    PhaseGateState,
    PortfolioState,
    Position,
    PriceSignal,
    ExecutionResult,
    Side,
    SpreadStatus,
    TradeStats,
)


@pytest.fixture(autouse=True)
def stub_price_stream(monkeypatch):
    def fake_refresh_market_subscriptions():
        return ["m1"]

    async def fake_bootstrap_market_pool(top_n: int = 10):
        _ = top_n
        return ["m1"]

    async def fake_run_price_stream(_market_ids):
        while True:
            await asyncio.sleep(0.05)

    monkeypatch.setattr(app_module.orchestrator, "refresh_market_subscriptions", fake_refresh_market_subscriptions)
    monkeypatch.setattr(app_module.orchestrator, "bootstrap_market_pool", fake_bootstrap_market_pool)
    monkeypatch.setattr(app_module.orchestrator, "run_price_stream", fake_run_price_stream)


def test_ops_phase_endpoint_exists():
    with TestClient(app) as client:
        res = client.get("/ops/phase")
    assert res.status_code == 200
    payload = res.json()
    assert "phase" in payload
    assert "allowed_mode_b" in payload


def test_ops_phase_evaluate_endpoint_exists():
    with TestClient(app) as client:
        res = client.post("/ops/phase/evaluate")
    assert res.status_code == 200
    assert "phase" in res.json()


def test_ops_dry_run_set_toggles_runtime_flag():
    with TestClient(app) as client:
        res = client.post("/ops/dry-run/set", params={"enabled": False})
    assert res.status_code == 200
    assert res.json()["execution_dry_run"] is False


def test_ops_metrics_summary_endpoint_exists():
    with TestClient(app) as client:
        res = client.get("/ops/metrics/summary")
    assert res.status_code == 200
    payload = res.json()
    assert "win_rate" in payload
    assert "rr" in payload


def test_ops_replay_endpoint_exists():
    with TestClient(app) as client:
        res = client.post("/ops/replay/run", params={"minutes": 30})
    assert res.status_code == 200
    payload = res.json()
    assert payload["minutes"] == 30
    assert "events" in payload
    assert isinstance(payload["events"], list)


def test_ops_execution_reconcile_endpoint_exists():
    with TestClient(app) as client:
        res = client.post("/ops/execution/reconcile")
    assert res.status_code == 200
    payload = res.json()
    assert "equity" in payload
    assert "cash" in payload


def test_ops_execution_reconcile_orders_endpoint_exists():
    with TestClient(app) as client:
        res = client.post("/ops/execution/reconcile-orders", params={"limit": 10})
    assert res.status_code == 200
    payload = res.json()
    assert "updated_orders" in payload


def test_ops_execution_health_endpoint_exists():
    with TestClient(app) as client:
        res = client.get("/ops/execution/health")
    assert res.status_code == 200
    payload = res.json()
    assert "consecutive_failures" in payload
    assert "circuit_open" in payload


def test_ops_execution_compensation_drain_endpoint_exists():
    with TestClient(app) as client:
        res = client.post("/ops/execution/compensations/drain", params={"max_items": 5})
    assert res.status_code == 200
    payload = res.json()
    assert "drained" in payload
    assert "pending_compensations" in payload


def test_ops_tick_includes_reconciled_orders_field():
    with TestClient(app) as client:
        res = client.post("/ops/tick")
    assert res.status_code == 200
    payload = res.json()
    assert "reconciled_orders" in payload


def test_health_and_ops_state_expose_price_stream_fields():
    with TestClient(app) as client:
        health = client.get("/health")
        state = client.get("/ops/state")
    assert health.status_code == 200
    assert state.status_code == 200
    health_payload = health.json()
    state_payload = state.json()
    assert "price_stream_running" in health_payload
    assert "price_stream_last_event_ts" in health_payload
    assert "price_stream_reconnects" in health_payload
    assert "price_stream_running" in state_payload
    assert "price_stream_last_event_ts" in state_payload
    assert "price_stream_reconnects" in state_payload


def test_lifespan_starts_and_stops_price_stream_task(monkeypatch):
    lifecycle = {"started": False, "cancelled": False}

    def fake_refresh_market_subscriptions():
        return ["m1"]

    async def fake_run_price_stream(_market_ids):
        lifecycle["started"] = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            lifecycle["cancelled"] = True
            raise

    monkeypatch.setattr(app_module.orchestrator, "refresh_market_subscriptions", fake_refresh_market_subscriptions)
    monkeypatch.setattr(app_module.orchestrator, "run_price_stream", fake_run_price_stream)

    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200

    assert lifecycle["started"] is True
    assert lifecycle["cancelled"] is True


def test_lifespan_price_stream_updates_price_state(monkeypatch):
    def fake_refresh_market_subscriptions():
        return ["m1"]

    async def fake_run_price_stream(_market_ids):
        for idx in range(3):
            app_module.orchestrator.handle_market_tick(
                NormalizedMarketTick(
                    market_id="m1",
                    best_bid=0.48 + idx * 0.01,
                    best_ask=0.52 + idx * 0.01,
                    last=0.50 + idx * 0.01,
                    volume_1m=1.0 + idx,
                    trades_1m=1 + idx,
                )
            )
            await asyncio.sleep(0.01)
        while True:
            await asyncio.sleep(0.1)

    monkeypatch.setattr(app_module.orchestrator, "refresh_market_subscriptions", fake_refresh_market_subscriptions)
    monkeypatch.setattr(app_module.orchestrator, "run_price_stream", fake_run_price_stream)

    with TestClient(app) as client:
        time.sleep(0.1)
        state = client.get("/ops/state")
    assert state.status_code == 200
    payload = state.json()
    assert payload["price_snapshots"] >= 1
    assert payload["price_signals"] >= 1


def test_strategy_run_endpoint_closes_position_and_updates_stats():
    previous_dry_run = app_module.execution_engine.dry_run
    app_module.execution_engine.set_dry_run(True)
    try:
        app_module.repo.mode_states.clear()
        app_module.repo.order_records.clear()
        app_module.repo.fill_records.clear()
        app_module.repo.trade_logs.clear()
        app_module.repo.positions_snapshots.clear()
        app_module.repo.price_signals.clear()
        app_module.repo.trade_stats = TradeStats()
        app_module.repo.phase_gate_state = PhaseGateState()
        app_module.repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
        app_module.repo.portfolio = PortfolioState(
            equity=10_000.0,
            cash=9_950.0,
            positions=[Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.50, u_pnl=0.0)],
        )

        price_signal = PriceSignal(
            market_id="m1",
            spread=0.01,
            change_5m=0.02,
            vol_ratio_15m=1.3,
            mid=0.52,
            spread_status=SpreadStatus.stable,
        )

        with TestClient(app) as client:
            res = client.post(
                "/strategy/run",
                params={"mode_market_id": "m1"},
                json={
                    "price_signal": price_signal.model_dump(mode="json"),
                    "portfolio": app_module.repo.portfolio.model_dump(mode="json"),
                },
            )
        assert res.status_code == 200
        payload = res.json()
        assert payload["approved"] is True
        assert payload["execution"]["accepted"] is True
        assert len(app_module.repo.order_records) == 1
        assert len(app_module.repo.fill_records) == 1
        assert len(app_module.repo.trade_logs) == 1
        assert "m1" in app_module.repo.price_signals
        assert app_module.repo.price_signals["m1"].mid == 0.52
        assert app_module.repo.trade_stats.total_trades == 1
        assert app_module.repo.trade_stats.wins == 1
        assert app_module.repo.portfolio.positions == []
    finally:
        app_module.execution_engine.set_dry_run(previous_dry_run)


def test_strategy_run_uses_repo_portfolio_as_source_of_truth():
    previous_dry_run = app_module.execution_engine.dry_run
    app_module.execution_engine.set_dry_run(True)
    try:
        app_module.repo.mode_states.clear()
        app_module.repo.order_records.clear()
        app_module.repo.fill_records.clear()
        app_module.repo.trade_logs.clear()
        app_module.repo.positions_snapshots.clear()
        app_module.repo.trade_stats = TradeStats()
        app_module.repo.phase_gate_state = PhaseGateState()
        app_module.repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
        app_module.repo.portfolio = PortfolioState(
            equity=10_000.0,
            cash=9_950.0,
            positions=[Position(market_id="m1", side=Side.YES, size=100.0, avg_price=0.50, u_pnl=0.0)],
        )

        price_signal = PriceSignal(
            market_id="m1",
            spread=0.01,
            change_5m=0.02,
            vol_ratio_15m=1.3,
            mid=0.52,
            spread_status=SpreadStatus.stable,
        )
        request_portfolio = PortfolioState(equity=10_000.0, cash=10_000.0, positions=[])

        with TestClient(app) as client:
            res = client.post(
                "/strategy/run",
                params={"mode_market_id": "m1"},
                json={
                    "price_signal": price_signal.model_dump(mode="json"),
                    "portfolio": request_portfolio.model_dump(mode="json"),
                },
            )
        assert res.status_code == 200
        assert len(app_module.repo.order_records) == 1
        assert app_module.repo.order_records[0].action == Action.CLOSE
    finally:
        app_module.execution_engine.set_dry_run(previous_dry_run)


def test_strategy_run_syncs_execution_compensations_on_failure(monkeypatch):
    app_module.repo.mode_states.clear()
    app_module.repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    app_module.repo.portfolio = PortfolioState(equity=10_000.0, cash=10_000.0, positions=[])
    price_signal = PriceSignal(
        market_id="m1",
        spread=0.01,
        change_5m=0.02,
        vol_ratio_15m=1.3,
        mid=0.5,
        spread_status=SpreadStatus.stable,
    )
    sync_calls = {"count": 0}

    async def fake_execute(_approved):
        return ExecutionResult(accepted=False, status="execution_error", dry_run=True)

    def fake_sync():
        sync_calls["count"] += 1
        return {"pending": 1, "stored": 1}

    monkeypatch.setattr(app_module.execution_engine, "execute", fake_execute)
    monkeypatch.setattr(app_module.orchestrator, "sync_execution_compensations", fake_sync)

    with TestClient(app) as client:
        res = client.post(
            "/strategy/run",
            params={"mode_market_id": "m1"},
            json={
                "price_signal": price_signal.model_dump(mode="json"),
                "portfolio": app_module.repo.portfolio.model_dump(mode="json"),
            },
        )
    assert res.status_code == 200
    assert sync_calls["count"] == 1


def test_strategy_run_respects_repo_risk_controls_when_repo_has_no_position():
    previous_dry_run = app_module.execution_engine.dry_run
    app_module.execution_engine.set_dry_run(True)
    try:
        app_module.repo.mode_states.clear()
        app_module.repo.order_records.clear()
        app_module.repo.fill_records.clear()
        app_module.repo.trade_logs.clear()
        app_module.repo.positions_snapshots.clear()
        app_module.repo.trade_stats = TradeStats()
        app_module.repo.phase_gate_state = PhaseGateState()
        app_module.repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
        app_module.repo.portfolio = PortfolioState(
            equity=10_000.0,
            cash=10_000.0,
            positions=[],
            daily_halt=True,
        )

        price_signal = PriceSignal(
            market_id="m1",
            spread=0.01,
            change_5m=0.02,
            vol_ratio_15m=1.3,
            mid=0.5,
            spread_status=SpreadStatus.stable,
        )
        request_portfolio = PortfolioState(equity=10_000.0, cash=10_000.0, positions=[], daily_halt=False)

        with TestClient(app) as client:
            res = client.post(
                "/strategy/run",
                params={"mode_market_id": "m1"},
                json={
                    "price_signal": price_signal.model_dump(mode="json"),
                    "portfolio": request_portfolio.model_dump(mode="json"),
                },
            )

        assert res.status_code == 200
        payload = res.json()
        assert payload["approved"] is False
        assert payload["reason"] == "daily_drawdown_halt"
        assert len(app_module.repo.order_records) == 0
    finally:
        app_module.execution_engine.set_dry_run(previous_dry_run)
