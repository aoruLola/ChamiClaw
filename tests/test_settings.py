import os

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
