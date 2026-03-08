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



def test_scheduler_uses_weather_cadence_overrides_when_enabled():
    scheduler = TaskScheduler(
        AppSettings(
            weather_enabled=True,
            weather_market_refresh_minutes=360,
            weather_info_refresh_minutes=360,
            weather_strategy_loop_minutes=720,
        )
    )
    scheduler.bootstrap_defaults(lambda: None, lambda: None, lambda: None, lambda: None)

    assert scheduler.jobs["market_refresh"][0] == 360 * 60
    assert scheduler.jobs["info_refresh"][0] == 360 * 60
    assert scheduler.jobs["strategy_loop"][0] == 720 * 60
