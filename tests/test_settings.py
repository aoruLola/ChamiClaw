import os
from pathlib import Path

import pytest

from chamiclaw.core.settings import AppSettings


def test_settings_loads_execution_and_phase_gate_defaults(monkeypatch):
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)
    monkeypatch.delenv("SIMMER_BASE_URL", raising=False)
    monkeypatch.setenv("EXECUTION_DRY_RUN", "true")

    settings = AppSettings.load()

    assert settings.execution_dry_run is True
    assert settings.execution_max_retries == 2
    assert settings.execution_retry_backoff_seconds == 0.5
    assert settings.execution_breaker_failures == 5
    assert settings.execution_breaker_cooldown_seconds == 60
    assert settings.execution_rate_limit_per_market_per_minute == 3
    assert settings.execution_rate_limit_global_per_minute == 20
    assert settings.phase_gate_enabled is True
    assert settings.phase1_min_trades == 200
    assert settings.phase1_min_win_rate == 0.57
    assert settings.phase1_min_rr == 1.1
    assert settings.phase1_max_drawdown == 0.05


def test_settings_requires_simmer_credentials_when_live(monkeypatch):
    monkeypatch.setenv("EXECUTION_DRY_RUN", "false")
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)
    monkeypatch.delenv("SIMMER_BASE_URL", raising=False)

    with pytest.raises(ValueError):
        AppSettings.load()


def test_settings_allows_live_when_simmer_credentials_present(monkeypatch):
    monkeypatch.setenv("EXECUTION_DRY_RUN", "false")
    monkeypatch.setenv("SIMMER_BASE_URL", "https://simmer.example.com")
    monkeypatch.setenv("SIMMER_API_KEY", "secret")

    settings = AppSettings.load()

    assert settings.execution_dry_run is False
    assert settings.simmer_base_url == "https://simmer.example.com"
    assert settings.simmer_api_key == "secret"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PHASE1_MIN_WIN_RATE", "1.2"),
        ("PHASE1_MIN_RR", "0.0"),
        ("PHASE1_MAX_DRAWDOWN", "0.0"),
    ],
)
def test_settings_rejects_invalid_thresholds(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        AppSettings.load()


def test_settings_restores_environment(monkeypatch):
    monkeypatch.setenv("EXECUTION_DRY_RUN", "true")
    _ = AppSettings.load()
    assert os.getenv("EXECUTION_DRY_RUN") == "true"


def test_settings_loads_ws_stream_runtime_defaults(monkeypatch):
    monkeypatch.delenv("CLOB_WS_MAX_RETRIES", raising=False)
    monkeypatch.delenv("CLOB_WS_BACKOFF_BASE_SECONDS", raising=False)
    monkeypatch.delenv("CLOB_WS_BACKOFF_MAX_SECONDS", raising=False)
    monkeypatch.delenv("PRICE_FLUSH_SECONDS", raising=False)
    monkeypatch.delenv("WS_STALE_TIMEOUT_SECONDS", raising=False)

    settings = AppSettings.load()

    assert settings.clob_ws_max_retries == 10
    assert settings.clob_ws_backoff_base_seconds == 1.0
    assert settings.clob_ws_backoff_max_seconds == 30.0
    assert settings.price_flush_seconds == 30
    assert settings.ws_stale_timeout_seconds == 90


def test_settings_loads_values_from_dotenv_file(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAMICLAW_LOAD_DOTENV", "true")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "REPOSITORY_BACKEND=sqlite",
                "SIMMER_BASE_URL=https://api.simmer.markets",
                "SIMMER_API_KEY=from-dotenv",
                "BRAVE_API_KEY=from-dotenv-brave",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("REPOSITORY_BACKEND", raising=False)
    monkeypatch.delenv("SIMMER_BASE_URL", raising=False)
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("EXECUTION_DRY_RUN", "true")

    settings = AppSettings.load()

    assert settings.repository_backend == "sqlite"
    assert settings.simmer_base_url == "https://api.simmer.markets"
    assert settings.simmer_api_key == "from-dotenv"
    assert settings.brave_api_key == "from-dotenv-brave"


def test_settings_environment_variable_overrides_dotenv(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAMICLAW_LOAD_DOTENV", "true")
    (tmp_path / ".env").write_text("REPOSITORY_BACKEND=sqlite", encoding="utf-8")
    monkeypatch.setenv("REPOSITORY_BACKEND", "memory")

    settings = AppSettings.load()

    assert settings.repository_backend == "memory"


def test_settings_loads_weather_and_llm_defaults(monkeypatch):
    monkeypatch.setenv("EXECUTION_DRY_RUN", "true")
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = AppSettings.load()

    assert settings.weather_enabled is True
    assert settings.weather_only_us_markets is True
    assert settings.weather_market_type == "daily_precipitation"
    assert settings.weather_batch_max_candidates == 12
    assert settings.weather_batch_max_orders == 6
    assert settings.weather_market_refresh_minutes == 360
    assert settings.weather_info_refresh_minutes == 360
    assert settings.weather_strategy_loop_minutes == 720
    assert settings.weather_event_tag_slugs == ["weather", "rain", "precipitation", "forecast"]
    assert settings.weather_event_page_size == 50
    assert settings.weather_event_max_pages == 5
    assert settings.weather_search_fallback_enabled is True
    assert settings.weather_search_terms == ["rain", "precipitation", "rainfall", "showers"]
    assert settings.weather_search_limit_per_term == 10
    assert settings.llm_enabled is False
    assert settings.llm_failsafe_mode == "reject"
    assert settings.webhook_enabled is False
    assert settings.webhook_timeout_seconds == 5.0
    assert settings.webhook_max_retries == 1


def test_settings_requires_llm_credentials_when_enabled(monkeypatch):
    monkeypatch.setenv("EXECUTION_DRY_RUN", "true")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ValueError):
        AppSettings.load()



def test_settings_requires_webhook_url_when_enabled(monkeypatch):
    monkeypatch.setenv("EXECUTION_DRY_RUN", "true")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)

    with pytest.raises(ValueError):
        AppSettings.load()


