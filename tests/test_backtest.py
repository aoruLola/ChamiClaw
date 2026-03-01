from datetime import datetime, timedelta, timezone

from chamiclaw.core.models import (
    Action,
    BacktestRequest,
    ModeState,
    Mode,
    OrderMode,
    OrderRecord,
    OrderType,
    PriceSignal,
    Side,
    SpreadStatus,
)
from chamiclaw.optimization.backtest import BacktestEngine
from chamiclaw.storage.repository import InMemoryRepository


def test_backtest_engine_is_deterministic_for_same_input():
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.put_price_signal(
        PriceSignal(
            ts=now - timedelta(minutes=10),
            market_id="m1",
            change_5m=0.02,
            vol_ratio_15m=1.5,
            spread=0.01,
            spread_status=SpreadStatus.stable,
            mid=0.52,
        )
    )
    repo.put_mode_state(ModeState(ts=now - timedelta(minutes=10), market_id="m1", mode=Mode.MODE_A))
    repo.record_order(
        OrderRecord(
            ts=now - timedelta(minutes=9),
            order_id="o1",
            market_id="m1",
            side=Side.YES,
            action=Action.CLOSE,
            order_type=OrderType.LIMIT,
            limit_price=0.52,
            size_usd=52.0,
            status="simulated",
            adapter="SimmerAdapter",
            mode=OrderMode.A,
            dry_run=True,
        )
    )
    repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=9)).isoformat(),
            "market_id": "m1",
            "mode": "A",
            "action": "CLOSE",
            "pnl": 2.0,
        }
    )
    repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=8)).isoformat(),
            "market_id": "m1",
            "mode": "A",
            "action": "CLOSE",
            "pnl": -1.0,
        }
    )

    engine = BacktestEngine()
    req = BacktestRequest(
        from_ts=now - timedelta(minutes=30),
        to_ts=now,
        params_version_id=repo.get_current_params().version_id,
    )
    first = engine.run(repo, req)
    second = engine.run(repo, req)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.total_trades == 2
    assert first.mode_a_trades == 2
    assert first.mode_b_trades == 0


def test_backtest_drawdown_detects_losing_only_sequence():
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=4)).isoformat(),
            "market_id": "m1",
            "mode": "A",
            "action": "CLOSE",
            "pnl": -1.0,
        }
    )
    repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=3)).isoformat(),
            "market_id": "m1",
            "mode": "A",
            "action": "CLOSE",
            "pnl": -2.0,
        }
    )
    req = BacktestRequest(
        from_ts=now - timedelta(minutes=30),
        to_ts=now,
        params_version_id=repo.get_current_params().version_id,
    )
    report = BacktestEngine().run(repo, req)

    assert report.total_trades == 2
    assert report.max_drawdown_pct > 0


def test_backtest_score_depends_on_params_version():
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=5)).isoformat(),
            "market_id": "m1",
            "mode": "A",
            "action": "CLOSE",
            "pnl": 1.5,
        }
    )
    repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=4)).isoformat(),
            "market_id": "m1",
            "mode": "B",
            "action": "CLOSE",
            "pnl": -0.4,
        }
    )

    base = repo.get_current_params().params
    conservative = base.model_copy(
        update={
            "a_risk_pct": 0.003,
            "b_risk_pct": 0.004,
            "a_change_5m_min_abs": 0.011,
            "b_change_15m_min_abs": 0.031,
            "a_vol_ratio_15m_min": 1.3,
            "b_vol_ratio_15m_min": 2.2,
        }
    )
    aggressive = base.model_copy(
        update={
            "a_risk_pct": 0.01,
            "b_risk_pct": 0.02,
            "a_change_5m_min_abs": 0.02,
            "b_change_15m_min_abs": 0.05,
            "a_vol_ratio_15m_min": 2.0,
            "b_vol_ratio_15m_min": 3.0,
            "max_b_share": 0.6,
        }
    )
    conservative_version = repo.save_params_version(conservative, source="test", make_current=False)
    aggressive_version = repo.save_params_version(aggressive, source="test", make_current=False)

    engine = BacktestEngine()
    conservative_report = engine.run(
        repo,
        BacktestRequest(
            from_ts=now - timedelta(minutes=30),
            to_ts=now,
            params_version_id=conservative_version.version_id,
        ),
    )
    aggressive_report = engine.run(
        repo,
        BacktestRequest(
            from_ts=now - timedelta(minutes=30),
            to_ts=now,
            params_version_id=aggressive_version.version_id,
        ),
    )

    assert conservative_report.params_version_id == conservative_version.version_id
    assert aggressive_report.params_version_id == aggressive_version.version_id
    assert conservative_report.score != aggressive_report.score
