from __future__ import annotations

import os
from pathlib import Path

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
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_ws_url: str = "wss://clob.polymarket.com/ws"
    clob_rest_url: str = "https://clob.polymarket.com"
    clob_ws_max_retries: int = 10
    clob_ws_backoff_base_seconds: float = 1.0
    clob_ws_backoff_max_seconds: float = 30.0
    price_flush_seconds: int = 30
    ws_stale_timeout_seconds: int = 90
    brave_api_key: str = ""
    simmer_base_url: str = ""
    simmer_api_key: str = ""
    execution_dry_run: bool = True
    execution_max_retries: int = 2
    execution_retry_backoff_seconds: float = 0.5
    execution_breaker_failures: int = 5
    execution_breaker_cooldown_seconds: int = 60
    execution_rate_limit_per_market_per_minute: int = 3
    execution_rate_limit_global_per_minute: int = 20
    phase_gate_enabled: bool = True
    phase1_min_trades: int = 200
    phase1_min_win_rate: float = 0.57
    phase1_min_rr: float = 1.1
    phase1_max_drawdown: float = 0.05
    run_profile: str = "sim"
    params_path: str = "data/strategy_params.json"
    data_retention_days: int = 30

    @staticmethod
    def _as_bool(value: str, default: bool = False) -> bool:
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _validate_thresholds(
        min_win_rate: float,
        min_rr: float,
        max_drawdown: float,
    ) -> None:
        if not 0 < min_win_rate <= 1:
            raise ValueError("PHASE1_MIN_WIN_RATE must be in (0, 1].")
        if min_rr <= 0:
            raise ValueError("PHASE1_MIN_RR must be > 0.")
        if not 0 < max_drawdown < 1:
            raise ValueError("PHASE1_MAX_DRAWDOWN must be in (0, 1).")

    @staticmethod
    def _validate_ws_runtime(
        *,
        max_retries: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
        price_flush_seconds: int,
        ws_stale_timeout_seconds: int,
    ) -> None:
        if max_retries < 0:
            raise ValueError("CLOB_WS_MAX_RETRIES must be >= 0.")
        if backoff_base_seconds <= 0:
            raise ValueError("CLOB_WS_BACKOFF_BASE_SECONDS must be > 0.")
        if backoff_max_seconds <= 0:
            raise ValueError("CLOB_WS_BACKOFF_MAX_SECONDS must be > 0.")
        if backoff_max_seconds < backoff_base_seconds:
            raise ValueError("CLOB_WS_BACKOFF_MAX_SECONDS must be >= CLOB_WS_BACKOFF_BASE_SECONDS.")
        if price_flush_seconds <= 0:
            raise ValueError("PRICE_FLUSH_SECONDS must be > 0.")
        if ws_stale_timeout_seconds <= 0:
            raise ValueError("WS_STALE_TIMEOUT_SECONDS must be > 0.")

    @staticmethod
    def _validate_local_runtime(*, run_profile: str, data_retention_days: int) -> None:
        if run_profile not in {"sim", "live"}:
            raise ValueError("RUN_PROFILE must be one of: sim, live.")
        if data_retention_days <= 0:
            raise ValueError("DATA_RETENTION_DAYS must be > 0.")

    @classmethod
    def load(cls) -> "AppSettings":
        cls._load_dotenv()
        phase1_min_trades = int(os.getenv("PHASE1_MIN_TRADES", "200"))
        phase1_min_win_rate = float(os.getenv("PHASE1_MIN_WIN_RATE", "0.57"))
        phase1_min_rr = float(os.getenv("PHASE1_MIN_RR", "1.1"))
        phase1_max_drawdown = float(os.getenv("PHASE1_MAX_DRAWDOWN", "0.05"))
        cls._validate_thresholds(
            min_win_rate=phase1_min_win_rate,
            min_rr=phase1_min_rr,
            max_drawdown=phase1_max_drawdown,
        )
        clob_ws_max_retries = int(os.getenv("CLOB_WS_MAX_RETRIES", "10"))
        clob_ws_backoff_base_seconds = float(os.getenv("CLOB_WS_BACKOFF_BASE_SECONDS", "1.0"))
        clob_ws_backoff_max_seconds = float(os.getenv("CLOB_WS_BACKOFF_MAX_SECONDS", "30.0"))
        price_flush_seconds = int(os.getenv("PRICE_FLUSH_SECONDS", "30"))
        ws_stale_timeout_seconds = int(os.getenv("WS_STALE_TIMEOUT_SECONDS", "90"))
        cls._validate_ws_runtime(
            max_retries=clob_ws_max_retries,
            backoff_base_seconds=clob_ws_backoff_base_seconds,
            backoff_max_seconds=clob_ws_backoff_max_seconds,
            price_flush_seconds=price_flush_seconds,
            ws_stale_timeout_seconds=ws_stale_timeout_seconds,
        )

        execution_dry_run = cls._as_bool(os.getenv("EXECUTION_DRY_RUN", "true"), default=True)
        execution_max_retries = int(os.getenv("EXECUTION_MAX_RETRIES", "2"))
        execution_retry_backoff_seconds = float(os.getenv("EXECUTION_RETRY_BACKOFF_SECONDS", "0.5"))
        execution_breaker_failures = int(os.getenv("EXECUTION_BREAKER_FAILURES", "5"))
        execution_breaker_cooldown_seconds = int(os.getenv("EXECUTION_BREAKER_COOLDOWN_SECONDS", "60"))
        execution_rate_limit_per_market_per_minute = int(
            os.getenv("EXECUTION_RATE_LIMIT_PER_MARKET_PER_MINUTE", "3")
        )
        execution_rate_limit_global_per_minute = int(os.getenv("EXECUTION_RATE_LIMIT_GLOBAL_PER_MINUTE", "20"))
        if execution_max_retries < 0:
            raise ValueError("EXECUTION_MAX_RETRIES must be >= 0.")
        if execution_retry_backoff_seconds < 0:
            raise ValueError("EXECUTION_RETRY_BACKOFF_SECONDS must be >= 0.")
        if execution_breaker_failures <= 0:
            raise ValueError("EXECUTION_BREAKER_FAILURES must be > 0.")
        if execution_breaker_cooldown_seconds <= 0:
            raise ValueError("EXECUTION_BREAKER_COOLDOWN_SECONDS must be > 0.")
        if execution_rate_limit_per_market_per_minute <= 0:
            raise ValueError("EXECUTION_RATE_LIMIT_PER_MARKET_PER_MINUTE must be > 0.")
        if execution_rate_limit_global_per_minute <= 0:
            raise ValueError("EXECUTION_RATE_LIMIT_GLOBAL_PER_MINUTE must be > 0.")
        simmer_base_url = os.getenv("SIMMER_BASE_URL", "").strip()
        simmer_api_key = os.getenv("SIMMER_API_KEY", "").strip()
        if not execution_dry_run and (not simmer_base_url or not simmer_api_key):
            raise ValueError("SIMMER_BASE_URL and SIMMER_API_KEY are required when EXECUTION_DRY_RUN=false.")
        run_profile = os.getenv("RUN_PROFILE", "sim").strip().lower() or "sim"
        data_retention_days = int(os.getenv("DATA_RETENTION_DAYS", "30"))
        cls._validate_local_runtime(run_profile=run_profile, data_retention_days=data_retention_days)

        return cls(
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "false").lower() == "true",
            repository_backend=os.getenv("REPOSITORY_BACKEND", "memory"),
            sqlite_path=os.getenv("SQLITE_PATH", "data/chamiclaw_t1.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            gamma_base_url=os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
            clob_ws_url=os.getenv("CLOB_WS_URL", "wss://clob.polymarket.com/ws"),
            clob_rest_url=os.getenv("CLOB_REST_URL", "https://clob.polymarket.com"),
            clob_ws_max_retries=clob_ws_max_retries,
            clob_ws_backoff_base_seconds=clob_ws_backoff_base_seconds,
            clob_ws_backoff_max_seconds=clob_ws_backoff_max_seconds,
            price_flush_seconds=price_flush_seconds,
            ws_stale_timeout_seconds=ws_stale_timeout_seconds,
            brave_api_key=os.getenv("BRAVE_API_KEY", ""),
            simmer_base_url=simmer_base_url,
            simmer_api_key=simmer_api_key,
            execution_dry_run=execution_dry_run,
            execution_max_retries=execution_max_retries,
            execution_retry_backoff_seconds=execution_retry_backoff_seconds,
            execution_breaker_failures=execution_breaker_failures,
            execution_breaker_cooldown_seconds=execution_breaker_cooldown_seconds,
            execution_rate_limit_per_market_per_minute=execution_rate_limit_per_market_per_minute,
            execution_rate_limit_global_per_minute=execution_rate_limit_global_per_minute,
            phase_gate_enabled=cls._as_bool(os.getenv("PHASE_GATE_ENABLED", "true"), default=True),
            phase1_min_trades=phase1_min_trades,
            phase1_min_win_rate=phase1_min_win_rate,
            phase1_min_rr=phase1_min_rr,
            phase1_max_drawdown=phase1_max_drawdown,
            run_profile=run_profile,
            params_path=os.getenv("PARAMS_PATH", "data/strategy_params.json"),
            data_retention_days=data_retention_days,
        )

    @staticmethod
    def _load_dotenv(path: str = ".env") -> None:
        toggle = os.getenv("CHAMICLAW_LOAD_DOTENV")
        if toggle is None and (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("PYTEST_VERSION")):
            return
        if toggle is not None and toggle.strip().lower() in {"0", "false", "no", "off"}:
            return

        env_path = Path(path)
        if not env_path.exists() or not env_path.is_file():
            return
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value

