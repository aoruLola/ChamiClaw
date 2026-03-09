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
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
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
    weather_enabled: bool = True
    weather_only_us_markets: bool = True
    weather_market_type: str = "daily_precipitation"
    weather_batch_max_candidates: int = 12
    weather_batch_max_orders: int = 6
    weather_market_refresh_minutes: int = 360
    weather_info_refresh_minutes: int = 360
    weather_strategy_loop_minutes: int = 720
    weather_max_position_per_market_usd: float = 50.0
    weather_max_batch_risk_usd: float = 200.0
    weather_event_tag_slugs: list[str] = ["weather", "rain", "precipitation", "forecast"]
    weather_event_page_size: int = 50
    weather_event_max_pages: int = 5
    weather_search_fallback_enabled: bool = True
    weather_search_terms: list[str] = ["rain", "precipitation", "rainfall", "showers"]
    weather_search_limit_per_term: int = 10
    openmeteo_base_url: str = "https://api.open-meteo.com/v1"
    nws_base_url: str = "https://api.weather.gov"
    llm_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 10.0
    llm_max_retries: int = 1
    llm_decision_temperature: float = 0.0
    llm_failsafe_mode: str = "reject"
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_timeout_seconds: float = 5.0
    webhook_max_retries: int = 1
    webhook_service_name: str = "chamiclaw"
    webhook_environment: str = "local"

    @staticmethod
    def _as_bool(value: str, default: bool = False) -> bool:
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _as_list(value: str | None, default: list[str]) -> list[str]:
        if value is None:
            return list(default)
        items = [part.strip() for part in value.split(",") if part.strip()]
        return items or list(default)

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

    @staticmethod
    def _validate_weather_runtime(
        *,
        weather_batch_max_candidates: int,
        weather_batch_max_orders: int,
        weather_market_refresh_minutes: int,
        weather_info_refresh_minutes: int,
        weather_strategy_loop_minutes: int,
        weather_max_position_per_market_usd: float,
        weather_max_batch_risk_usd: float,
        weather_event_page_size: int,
        weather_event_max_pages: int,
        weather_search_limit_per_term: int,
    ) -> None:
        if weather_batch_max_candidates <= 0:
            raise ValueError("WEATHER_BATCH_MAX_CANDIDATES must be > 0.")
        if weather_batch_max_orders <= 0:
            raise ValueError("WEATHER_BATCH_MAX_ORDERS must be > 0.")
        if weather_batch_max_orders > weather_batch_max_candidates:
            raise ValueError("WEATHER_BATCH_MAX_ORDERS must be <= WEATHER_BATCH_MAX_CANDIDATES.")
        if weather_market_refresh_minutes <= 0:
            raise ValueError("WEATHER_MARKET_REFRESH_MINUTES must be > 0.")
        if weather_info_refresh_minutes <= 0:
            raise ValueError("WEATHER_INFO_REFRESH_MINUTES must be > 0.")
        if weather_strategy_loop_minutes <= 0:
            raise ValueError("WEATHER_STRATEGY_LOOP_MINUTES must be > 0.")
        if weather_max_position_per_market_usd <= 0:
            raise ValueError("WEATHER_MAX_POSITION_PER_MARKET_USD must be > 0.")
        if weather_max_batch_risk_usd <= 0:
            raise ValueError("WEATHER_MAX_BATCH_RISK_USD must be > 0.")
        if weather_event_page_size <= 0:
            raise ValueError("WEATHER_EVENT_PAGE_SIZE must be > 0.")
        if weather_event_max_pages <= 0:
            raise ValueError("WEATHER_EVENT_MAX_PAGES must be > 0.")
        if weather_search_limit_per_term <= 0:
            raise ValueError("WEATHER_SEARCH_LIMIT_PER_TERM must be > 0.")

    @staticmethod
    def _validate_webhook_runtime(
        *,
        webhook_enabled: bool,
        webhook_url: str,
        webhook_timeout_seconds: float,
        webhook_max_retries: int,
    ) -> None:
        if webhook_timeout_seconds <= 0:
            raise ValueError("WEBHOOK_TIMEOUT_SECONDS must be > 0.")
        if webhook_max_retries < 0:
            raise ValueError("WEBHOOK_MAX_RETRIES must be >= 0.")
        if webhook_enabled and not webhook_url:
            raise ValueError("WEBHOOK_URL is required when WEBHOOK_ENABLED=true.")

    @staticmethod
    def _validate_llm_runtime(
        *,
        llm_enabled: bool,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
        llm_timeout_seconds: float,
        llm_max_retries: int,
        llm_decision_temperature: float,
        llm_failsafe_mode: str,
    ) -> None:
        if llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be > 0.")
        if llm_max_retries < 0:
            raise ValueError("LLM_MAX_RETRIES must be >= 0.")
        if not 0 <= llm_decision_temperature <= 2:
            raise ValueError("LLM_DECISION_TEMPERATURE must be in [0, 2].")
        if llm_failsafe_mode not in {"reject", "min_size"}:
            raise ValueError("LLM_FAILSAFE_MODE must be one of: reject, min_size.")
        if llm_enabled and (not llm_base_url or not llm_api_key or not llm_model):
            raise ValueError("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required when LLM_ENABLED=true.")

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

        weather_batch_max_candidates = int(os.getenv("WEATHER_BATCH_MAX_CANDIDATES", "12"))
        weather_batch_max_orders = int(os.getenv("WEATHER_BATCH_MAX_ORDERS", "6"))
        weather_market_refresh_minutes = int(os.getenv("WEATHER_MARKET_REFRESH_MINUTES", "360"))
        weather_info_refresh_minutes = int(os.getenv("WEATHER_INFO_REFRESH_MINUTES", "360"))
        weather_strategy_loop_minutes = int(os.getenv("WEATHER_STRATEGY_LOOP_MINUTES", "720"))
        weather_max_position_per_market_usd = float(os.getenv("WEATHER_MAX_POSITION_PER_MARKET_USD", "50.0"))
        weather_max_batch_risk_usd = float(os.getenv("WEATHER_MAX_BATCH_RISK_USD", "200.0"))
        weather_event_tag_slugs = cls._as_list(
            os.getenv("WEATHER_EVENT_TAG_SLUGS"), ["weather", "rain", "precipitation", "forecast"]
        )
        weather_event_page_size = int(os.getenv("WEATHER_EVENT_PAGE_SIZE", "50"))
        weather_event_max_pages = int(os.getenv("WEATHER_EVENT_MAX_PAGES", "5"))
        weather_search_fallback_enabled = cls._as_bool(
            os.getenv("WEATHER_SEARCH_FALLBACK_ENABLED", "true"),
            default=True,
        )
        weather_search_terms = cls._as_list(
            os.getenv("WEATHER_SEARCH_TERMS"), ["rain", "precipitation", "rainfall", "showers"]
        )
        weather_search_limit_per_term = int(os.getenv("WEATHER_SEARCH_LIMIT_PER_TERM", "10"))
        cls._validate_weather_runtime(
            weather_batch_max_candidates=weather_batch_max_candidates,
            weather_batch_max_orders=weather_batch_max_orders,
            weather_market_refresh_minutes=weather_market_refresh_minutes,
            weather_info_refresh_minutes=weather_info_refresh_minutes,
            weather_strategy_loop_minutes=weather_strategy_loop_minutes,
            weather_max_position_per_market_usd=weather_max_position_per_market_usd,
            weather_max_batch_risk_usd=weather_max_batch_risk_usd,
            weather_event_page_size=weather_event_page_size,
            weather_event_max_pages=weather_event_max_pages,
            weather_search_limit_per_term=weather_search_limit_per_term,
        )

        llm_enabled = cls._as_bool(os.getenv("LLM_ENABLED", "false"), default=False)
        llm_base_url = os.getenv("LLM_BASE_URL", "").strip()
        llm_api_key = os.getenv("LLM_API_KEY", "").strip()
        llm_model = os.getenv("LLM_MODEL", "").strip()
        llm_timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "10.0"))
        llm_max_retries = int(os.getenv("LLM_MAX_RETRIES", "1"))
        llm_decision_temperature = float(os.getenv("LLM_DECISION_TEMPERATURE", "0.0"))
        llm_failsafe_mode = os.getenv("LLM_FAILSAFE_MODE", "reject").strip().lower() or "reject"
        webhook_enabled = cls._as_bool(os.getenv("WEBHOOK_ENABLED", "false"), default=False)
        webhook_url = os.getenv("WEBHOOK_URL", "").strip()
        webhook_timeout_seconds = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "5.0"))
        webhook_max_retries = int(os.getenv("WEBHOOK_MAX_RETRIES", "1"))
        webhook_service_name = os.getenv("WEBHOOK_SERVICE_NAME", "chamiclaw").strip() or "chamiclaw"
        webhook_environment = os.getenv("WEBHOOK_ENVIRONMENT", run_profile).strip() or run_profile
        cls._validate_webhook_runtime(
            webhook_enabled=webhook_enabled,
            webhook_url=webhook_url,
            webhook_timeout_seconds=webhook_timeout_seconds,
            webhook_max_retries=webhook_max_retries,
        )
        cls._validate_llm_runtime(
            llm_enabled=llm_enabled,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_timeout_seconds=llm_timeout_seconds,
            llm_max_retries=llm_max_retries,
            llm_decision_temperature=llm_decision_temperature,
            llm_failsafe_mode=llm_failsafe_mode,
        )

        return cls(
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "false").lower() == "true",
            repository_backend=os.getenv("REPOSITORY_BACKEND", "memory"),
            sqlite_path=os.getenv("SQLITE_PATH", "data/chamiclaw_t1.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            gamma_base_url=os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
            clob_ws_url=os.getenv("CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
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
            weather_enabled=cls._as_bool(os.getenv("WEATHER_ENABLED", "true"), default=True),
            weather_only_us_markets=cls._as_bool(os.getenv("WEATHER_ONLY_US_MARKETS", "true"), default=True),
            weather_market_type=os.getenv("WEATHER_MARKET_TYPE", "daily_precipitation").strip() or "daily_precipitation",
            weather_batch_max_candidates=weather_batch_max_candidates,
            weather_batch_max_orders=weather_batch_max_orders,
            weather_market_refresh_minutes=weather_market_refresh_minutes,
            weather_info_refresh_minutes=weather_info_refresh_minutes,
            weather_strategy_loop_minutes=weather_strategy_loop_minutes,
            weather_max_position_per_market_usd=weather_max_position_per_market_usd,
            weather_max_batch_risk_usd=weather_max_batch_risk_usd,
            weather_event_tag_slugs=weather_event_tag_slugs,
            weather_event_page_size=weather_event_page_size,
            weather_event_max_pages=weather_event_max_pages,
            weather_search_fallback_enabled=weather_search_fallback_enabled,
            weather_search_terms=weather_search_terms,
            weather_search_limit_per_term=weather_search_limit_per_term,
            openmeteo_base_url=os.getenv("OPENMETEO_BASE_URL", "https://api.open-meteo.com/v1").strip() or "https://api.open-meteo.com/v1",
            nws_base_url=os.getenv("NWS_BASE_URL", "https://api.weather.gov").strip() or "https://api.weather.gov",
            llm_enabled=llm_enabled,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_timeout_seconds=llm_timeout_seconds,
            llm_max_retries=llm_max_retries,
            llm_decision_temperature=llm_decision_temperature,
            llm_failsafe_mode=llm_failsafe_mode,
            webhook_enabled=webhook_enabled,
            webhook_url=webhook_url,
            webhook_timeout_seconds=webhook_timeout_seconds,
            webhook_max_retries=webhook_max_retries,
            webhook_service_name=webhook_service_name,
            webhook_environment=webhook_environment,
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
