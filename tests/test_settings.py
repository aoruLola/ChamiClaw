from chamiclaw.core.settings import AppSettings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("REPOSITORY_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", "data/test.db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = AppSettings.load()
    assert settings.scheduler_enabled is True
    assert settings.repository_backend == "sqlite"
    assert settings.sqlite_path == "data/test.db"
    assert settings.log_level == "DEBUG"
