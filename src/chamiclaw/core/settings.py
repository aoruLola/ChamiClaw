from pydantic import BaseModel


class AppSettings(BaseModel):
    market_refresh_minutes: int = 5
    price_aggregate_seconds: int = 30
    strategy_loop_minutes: int = 3
    info_refresh_minutes: int = 10
    scheduler_enabled: bool = False
