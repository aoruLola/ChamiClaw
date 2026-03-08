from __future__ import annotations

from collections.abc import Callable

from chamiclaw.core.settings import AppSettings


class TaskScheduler:
    """Cadence registry with optional APScheduler wiring."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.jobs: dict[str, tuple[int, Callable]] = {}
        self._live_scheduler = None

    def register(self, name: str, every_seconds: int, fn: Callable) -> None:
        self.jobs[name] = (every_seconds, fn)

    def bootstrap_defaults(
        self,
        market_refresh_fn: Callable,
        price_aggregate_fn: Callable,
        strategy_loop_fn: Callable,
        info_refresh_fn: Callable,
    ) -> None:
        market_refresh_minutes = self.settings.market_refresh_minutes
        info_refresh_minutes = self.settings.info_refresh_minutes
        strategy_loop_minutes = self.settings.strategy_loop_minutes
        if self.settings.weather_enabled:
            market_refresh_minutes = self.settings.weather_market_refresh_minutes
            info_refresh_minutes = self.settings.weather_info_refresh_minutes
            strategy_loop_minutes = self.settings.weather_strategy_loop_minutes
        self.register("market_refresh", market_refresh_minutes * 60, market_refresh_fn)
        self.register("price_aggregate", self.settings.price_aggregate_seconds, price_aggregate_fn)
        self.register("strategy_loop", strategy_loop_minutes * 60, strategy_loop_fn)
        self.register("info_refresh", info_refresh_minutes * 60, info_refresh_fn)

    def start(self) -> bool:
        if not self.settings.scheduler_enabled:
            return False
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except Exception:
            return False
        scheduler = AsyncIOScheduler()
        for name, (seconds, fn) in self.jobs.items():
            scheduler.add_job(fn, trigger="interval", seconds=seconds, id=name, replace_existing=True)
        scheduler.start()
        self._live_scheduler = scheduler
        return True

    def stop(self) -> None:
        if self._live_scheduler is not None:
            self._live_scheduler.shutdown(wait=False)
            self._live_scheduler = None
