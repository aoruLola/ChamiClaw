from __future__ import annotations

import os

from pydantic import BaseModel


class AppSettings(BaseModel):
    market_refresh_minutes: int = 5
    price_aggregate_seconds: int = 30
    strategy_loop_minutes: int = 3
    info_refresh_minutes: int = 10
    scheduler_enabled: bool = False
    repository_backend: str = "memory"
    sqlite_path: str = "data/chamiclaw_t1.db"
    log_level: str = "INFO"

    @classmethod
    def load(cls) -> "AppSettings":
        return cls(
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "false").lower() == "true",
            repository_backend=os.getenv("REPOSITORY_BACKEND", "memory"),
            sqlite_path=os.getenv("SQLITE_PATH", "data/chamiclaw_t1.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
