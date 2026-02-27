from datetime import datetime, timedelta, timezone

from chamiclaw.core.models import MarketCard, Mode, ModeState
from chamiclaw.storage.repository import SqliteRepository


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


from chamiclaw.core.settings import AppSettings
from chamiclaw.storage.repository import build_repository


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
