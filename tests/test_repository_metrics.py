from chamiclaw.core.models import TradeStats
from chamiclaw.storage.repository import InMemoryRepository


def test_metrics_summary_uses_realized_trade_denominator_for_win_rate():
    repo = InMemoryRepository()
    repo.trade_stats = TradeStats(
        total_trades=20,
        wins=3,
        losses=2,
        gross_profit=30.0,
        gross_loss=10.0,
    )

    payload = repo.metrics_summary()

    assert payload["win_rate"] == 0.6
    assert payload["rr"] == 3.0
