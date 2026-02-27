from chamiclaw.core.settings import AppSettings
from chamiclaw.orchestration.scheduler import TaskScheduler


def test_scheduler_bootstraps_required_jobs():
    scheduler = TaskScheduler(AppSettings())
    scheduler.bootstrap_defaults(lambda: None, lambda: None, lambda: None, lambda: None)
    assert set(scheduler.jobs.keys()) == {"market_refresh", "price_aggregate", "strategy_loop", "info_refresh"}
    assert scheduler.jobs["price_aggregate"][0] == 30


def test_scheduler_start_skipped_when_disabled():
    scheduler = TaskScheduler(AppSettings(scheduler_enabled=False))
    scheduler.register("job", 1, lambda: None)
    assert scheduler.start() is False
