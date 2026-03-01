import pytest

from chamiclaw.core.settings import AppSettings


def test_settings_loads_local_profile_defaults(monkeypatch):
    monkeypatch.delenv("RUN_PROFILE", raising=False)
    monkeypatch.delenv("PARAMS_PATH", raising=False)
    monkeypatch.delenv("DATA_RETENTION_DAYS", raising=False)

    settings = AppSettings.load()

    assert settings.run_profile == "sim"
    assert settings.params_path == "data/strategy_params.json"
    assert settings.data_retention_days == 30


def test_settings_supports_live_profile(monkeypatch):
    monkeypatch.setenv("RUN_PROFILE", "live")

    settings = AppSettings.load()

    assert settings.run_profile == "live"


def test_settings_rejects_invalid_profile(monkeypatch):
    monkeypatch.setenv("RUN_PROFILE", "invalid")

    with pytest.raises(ValueError):
        AppSettings.load()

