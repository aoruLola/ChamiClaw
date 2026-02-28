from chamiclaw.core.models import OptimizationTrial, StrategyParams
from chamiclaw.storage.repository import InMemoryRepository, SqliteRepository


def test_inmemory_repository_persists_current_strategy_params_version():
    repo = InMemoryRepository()
    base = repo.get_current_params()
    updated = base.params.model_copy(update={"a_risk_pct": 0.006})

    saved = repo.save_params_version(updated, source="test", score=1.23, make_current=True)

    assert saved.version_id != base.version_id
    assert repo.get_current_params().params.a_risk_pct == 0.006
    assert len(repo.strategy_params_history) >= 2


def test_inmemory_repository_leaderboard_is_descending():
    repo = InMemoryRepository()
    t1 = OptimizationTrial(params_version_id=repo.get_current_params().version_id, window_minutes=60, score=0.2)
    t2 = OptimizationTrial(params_version_id=repo.get_current_params().version_id, window_minutes=60, score=0.9)
    t3 = OptimizationTrial(params_version_id=repo.get_current_params().version_id, window_minutes=60, score=0.5)
    repo.save_optimization_trial(t1)
    repo.save_optimization_trial(t2)
    repo.save_optimization_trial(t3)

    leaderboard = repo.optimization_leaderboard(limit=3)

    assert [item.score for item in leaderboard] == [0.9, 0.5, 0.2]


def test_sqlite_repository_persists_params_and_trials(tmp_path):
    db = tmp_path / "params_trials.db"
    repo = SqliteRepository(str(db))
    version = repo.save_params_version(
        StrategyParams(a_risk_pct=0.007),
        source="sqlite-test",
        score=0.33,
        make_current=True,
    )
    repo.save_optimization_trial(OptimizationTrial(params_version_id=version.version_id, window_minutes=30, score=0.33))
    repo.close()

    repo2 = SqliteRepository(str(db))
    current = repo2.get_current_params()
    leaderboard = repo2.optimization_leaderboard(limit=10)

    assert current.version_id == version.version_id
    assert current.params.a_risk_pct == 0.007
    assert len(leaderboard) == 1
    assert leaderboard[0].params_version_id == version.version_id
    repo2.close()

