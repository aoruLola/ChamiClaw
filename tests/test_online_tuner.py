from chamiclaw.core.models import BacktestReport
from chamiclaw.optimization.online_tuner import OnlineTuner
from chamiclaw.storage.repository import InMemoryRepository


class FakeBacktestEngine:
    def __init__(self, scores: list[float]):
        self.scores = scores
        self.calls = 0

    def run(self, repo, request):  # noqa: ANN001
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return BacktestReport(
            from_ts=request.from_ts,
            to_ts=request.to_ts,
            params_version_id=request.params_version_id or repo.get_current_params().version_id,
            total_trades=10,
            win_rate=max(score, 0.0),
            rr=1.0,
            max_drawdown_pct=0.1,
            mode_a_trades=8,
            mode_b_trades=2,
            score=score,
            sampled_price_signals=5,
            sampled_mode_states=5,
            sampled_orders=5,
            sampled_fills=5,
        )


def test_online_tuner_blocks_auto_apply_in_live_mode():
    repo = InMemoryRepository()
    tuner = OnlineTuner(backtest_engine=FakeBacktestEngine([0.8, 0.7, 0.6]))

    result = tuner.run_window(
        repo=repo,
        window_minutes=60,
        apply_best=True,
        execution_dry_run=False,
        run_profile="live",
    )

    assert result["applied"] is False
    assert result["reason"] == "live_mode_manual_confirmation_required"


def test_online_tuner_blocks_auto_apply_in_live_profile_even_with_dry_run():
    repo = InMemoryRepository()
    tuner = OnlineTuner(backtest_engine=FakeBacktestEngine([0.8, 0.7, 0.6]))

    result = tuner.run_window(
        repo=repo,
        window_minutes=60,
        apply_best=True,
        execution_dry_run=True,
        run_profile="live",
    )

    assert result["applied"] is False
    assert result["reason"] == "live_mode_manual_confirmation_required"


def test_online_tuner_rolls_back_after_two_declines():
    repo = InMemoryRepository()
    tuner = OnlineTuner(backtest_engine=FakeBacktestEngine([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]))

    first = tuner.run_window(
        repo=repo,
        window_minutes=60,
        apply_best=True,
        execution_dry_run=True,
        run_profile="sim",
    )
    second = tuner.run_window(
        repo=repo,
        window_minutes=60,
        apply_best=True,
        execution_dry_run=True,
        run_profile="sim",
    )
    third = tuner.run_window(
        repo=repo,
        window_minutes=60,
        apply_best=True,
        execution_dry_run=True,
        run_profile="sim",
    )

    assert first["applied"] is True
    assert second["rolled_back"] is False
    assert third["rolled_back"] is True
    assert third["decline_streak"] >= 2
