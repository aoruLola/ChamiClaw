from datetime import datetime, timedelta, timezone

from chamiclaw.core.models import (
    Action,
    InfoSignal,
    MarketCard,
    Mode,
    ModeState,
    OrderIntent,
    OrderMode,
    OrderRecord,
    OrderType,
    Phase,
    PhaseGateState,
    PortfolioState,
    PriceSignal,
    Side,
    SpreadStatus,
)
from chamiclaw.core.settings import AppSettings
from chamiclaw.storage.repository import SqliteRepository, build_repository


def test_sqlite_repository_persists_market_mode_and_trade_stats(tmp_path):
    db = tmp_path / "repo.db"

    repo = SqliteRepository(str(db))
    market = MarketCard(
        market_id="m1",
        question="Q",
        end_time=datetime.now(timezone.utc) + timedelta(days=1),
        status="active",
        rule_clarity_score=0.9,
    )
    repo.upsert_market(market)
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    repo.register_trade("B")
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert "m1" in repo2.markets
    assert repo2.mode_states["m1"].mode == Mode.MODE_A
    assert repo2.trade_stats.total_trades == 1
    assert repo2.trade_stats.b_trades == 1
    repo2.close()


def test_build_repository_sqlite(tmp_path):
    settings = AppSettings(repository_backend="sqlite", sqlite_path=str(tmp_path / "x.db"))
    repo = build_repository(settings)
    assert repo.__class__.__name__ == "SqliteRepository"
    repo.close()


def test_sqlite_repository_reset_trade_stats_persists(tmp_path):
    db = tmp_path / "stats.db"
    repo = SqliteRepository(str(db))
    repo.register_trade("A")
    repo.register_trade("B")
    stats = repo.reset_trade_stats()
    assert stats.total_trades == 0
    assert stats.b_trades == 0
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert repo2.trade_stats.total_trades == 0
    assert repo2.trade_stats.b_trades == 0
    repo2.close()


def test_sqlite_repository_save_portfolio_persists_controls(tmp_path):
    db = tmp_path / "portfolio.db"
    repo = SqliteRepository(str(db))
    repo.portfolio.daily_halt = True
    repo.portfolio.consecutive_losses = 6
    repo.save_portfolio()
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert repo2.portfolio.daily_halt is True
    assert repo2.portfolio.consecutive_losses == 6
    repo2.close()


def test_sqlite_repository_reset_runtime_state_persists(tmp_path):
    db = tmp_path / "runtime_reset.db"
    repo = SqliteRepository(str(db))
    market = MarketCard(
        market_id="m1",
        question="Q",
        end_time=datetime.now(timezone.utc) + timedelta(days=1),
        status="active",
        rule_clarity_score=0.9,
    )
    repo.upsert_market(market)
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    counts = repo.reset_runtime_state(clear_markets=False)
    assert counts["markets"] == 1
    assert counts["mode_states"] == 0
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert "m1" in repo2.markets
    assert repo2.mode_states == {}
    repo2.close()


def test_sqlite_repository_reset_runtime_state_can_clear_controls_and_stats(tmp_path):
    db = tmp_path / "runtime_reset_controls.db"
    repo = SqliteRepository(str(db))
    repo.register_trade("A")
    repo.register_trade("B")
    repo.portfolio.daily_halt = True
    repo.portfolio.consecutive_losses = 7
    payload = repo.reset_runtime_state(
        clear_markets=False,
        clear_trade_stats=True,
        clear_portfolio_controls=True,
    )
    assert payload["trade_stats_total"] == 0
    assert payload["consecutive_losses"] == 0
    assert payload["daily_halt"] == 0
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert repo2.trade_stats.total_trades == 0
    assert repo2.trade_stats.b_trades == 0
    assert repo2.portfolio.daily_halt is False
    assert repo2.portfolio.consecutive_losses == 0
    repo2.close()


def test_sqlite_repository_persists_phase_state_and_orders(tmp_path):
    db = tmp_path / "phase_order.db"
    repo = SqliteRepository(str(db))
    repo.save_phase_gate(PhaseGateState(phase=Phase.PHASE_2, allowed_mode_b=True, reasons=["ok"]))
    repo.record_order(
        OrderRecord(
            order_id="o1",
            market_id="m1",
            side=Side.YES,
            action=Action.OPEN,
            order_type=OrderType.LIMIT,
            limit_price=0.5,
            size_usd=10.0,
            status="simulated",
            adapter="SimmerAdapter",
            mode=OrderMode.A,
            dry_run=True,
            raw={"foo": "bar"},
        )
    )
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert repo2.phase_gate_state.phase == Phase.PHASE_2
    assert repo2.phase_gate_state.allowed_mode_b is True
    assert len(repo2.order_records) == 1
    assert repo2.order_records[0].order_id == "o1"
    repo2.close()


def test_sqlite_repository_replay_window_filters_by_time(tmp_path):
    db = tmp_path / "replay.db"
    repo = SqliteRepository(str(db))
    old_order = OrderRecord(
        ts=datetime.now(timezone.utc) - timedelta(minutes=120),
        order_id="old",
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.4,
        size_usd=10.0,
        status="simulated",
        adapter="SimmerAdapter",
        mode=OrderMode.A,
        dry_run=True,
    )
    new_order = OrderRecord(
        order_id="new",
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=10.0,
        status="simulated",
        adapter="SimmerAdapter",
        mode=OrderMode.A,
        dry_run=True,
    )
    repo.record_order(old_order)
    repo.record_order(new_order)

    payload = repo.replay_window(minutes=30)
    assert payload["orders"] == 1
    assert "events" in payload
    repo.close()


def test_sqlite_replay_includes_strategy_input_snapshots(tmp_path):
    db = tmp_path / "replay_snapshots.db"
    repo = SqliteRepository(str(db))
    repo.put_price_signal(
        PriceSignal(
            market_id="m1",
            spread=0.01,
            mid=0.5,
            change_5m=0.02,
            vol_ratio_15m=1.5,
            spread_status=SpreadStatus.stable,
        )
    )
    repo.put_info_signal(InfoSignal(market_id="m1", risk_score=0.1, confirmation_level=2))
    repo.put_mode_state(ModeState(market_id="m1", mode=Mode.MODE_A))
    repo.record_order(
        OrderRecord(
            order_id="o-snap",
            market_id="m1",
            side=Side.YES,
            action=Action.OPEN,
            order_type=OrderType.LIMIT,
            limit_price=0.5,
            size_usd=20.0,
            status="simulated",
            adapter="SimmerAdapter",
            mode=OrderMode.A,
            dry_run=True,
        )
    )

    replay = repo.replay_window(minutes=60)
    order_events = [e for e in replay["events"] if e.get("event") == "order"]
    assert len(order_events) == 1
    assert order_events[0]["strategy_input"]["price_signal"]["market_id"] == "m1"
    assert order_events[0]["strategy_input"]["mode_state"]["market_id"] == "m1"
    repo.close()


def test_sqlite_repository_persists_price_stream_runtime_state(tmp_path):
    db = tmp_path / "stream_state.db"
    repo = SqliteRepository(str(db))
    now = datetime.now(timezone.utc)
    repo.update_price_stream_state(running=True, last_event_ts=now, reconnects=3)
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert repo2.price_stream_running is True
    assert repo2.price_stream_reconnects == 3
    assert repo2.price_stream_last_event_ts is not None
    repo2.close()


def test_sqlite_repository_persists_positions_snapshots(tmp_path):
    db = tmp_path / "positions_snapshots.db"
    repo = SqliteRepository(str(db))
    snapshot = PortfolioState(equity=12345.0, cash=12000.0)
    repo.record_positions_snapshot(snapshot)
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert len(repo2.positions_snapshots) == 1
    assert repo2.positions_snapshots[0].equity == 12345.0
    repo2.close()


def test_sqlite_repository_update_order_status_persists(tmp_path):
    db = tmp_path / "order_status.db"
    repo = SqliteRepository(str(db))
    repo.record_order(
        OrderRecord(
            order_id="o-status",
            market_id="m1",
            side=Side.YES,
            action=Action.OPEN,
            order_type=OrderType.LIMIT,
            limit_price=0.5,
            size_usd=10.0,
            status="submitted",
            adapter="SimmerAdapter",
            mode=OrderMode.A,
        )
    )
    ok = repo.update_order_status("o-status", "filled", raw={"fill_price": 0.5})
    assert ok is True
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert repo2.order_records[0].status == "filled"
    assert repo2.order_records[0].raw["fill_price"] == 0.5
    repo2.close()


def test_sqlite_repository_persists_execution_compensations(tmp_path):
    db = tmp_path / "execution_compensations.db"
    repo = SqliteRepository(str(db))
    intent = OrderIntent(
        market_id="m1",
        side=Side.YES,
        action=Action.OPEN,
        order_type=OrderType.LIMIT,
        limit_price=0.5,
        size_usd=10.0,
        mode=OrderMode.A,
        thesis="retry",
        idempotency_key="intent-xyz",
    )
    repo.upsert_execution_compensation("intent-xyz", intent)
    repo.close()

    repo2 = SqliteRepository(str(db))
    assert "intent-xyz" in repo2.execution_compensations
    assert repo2.execution_compensations["intent-xyz"].market_id == "m1"
    repo2.close()
