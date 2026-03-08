import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from chamiclaw.api import app as app_module
from chamiclaw.api.app import app


def test_ops_preflight_includes_weather_and_llm_checks(monkeypatch):
    monkeypatch.setattr(app_module.settings, "weather_enabled", True)
    monkeypatch.setattr(app_module.settings, "llm_enabled", True)

    def fake_probe(url: str, timeout_seconds: float = 3.0):
        _ = timeout_seconds
        if "open-meteo" in url:
            return True, "http_200"
        if "weather.gov" in url:
            return True, "http_200"
        if "llm.example.com" in url:
            return True, "http_200"
        return True, "http_200"

    monkeypatch.setattr(app_module.settings, "openmeteo_base_url", "https://api.open-meteo.com/v1")
    monkeypatch.setattr(app_module.settings, "nws_base_url", "https://api.weather.gov")
    monkeypatch.setattr(app_module.settings, "llm_base_url", "https://llm.example.com")
    monkeypatch.setattr(app_module, "_probe_http", fake_probe)

    payload = app_module.build_preflight_report()
    names = {item["name"] for item in payload["checks"]}

    assert "openmeteo_connectivity" in names
    assert "nws_connectivity" in names
    assert "llm_connectivity" in names



def test_ops_preflight_endpoint_exists():
    with TestClient(app) as client:
        res = client.get("/ops/preflight")
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] in {True, False}


def test_strategy_params_show_and_set_round_trip(monkeypatch):
    original_params = app_module.repo.get_current_params().params.model_copy(deep=True)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            params_path = Path(tmpdir) / "params.json"
            monkeypatch.setattr(app_module.settings, "params_path", str(params_path))

            with TestClient(app) as client:
                get_res = client.get("/ops/strategy/params")
                assert get_res.status_code == 200
                params = get_res.json()["params"]
                params["a_change_5m_min_abs"] = 0.023
                set_res = client.post("/ops/strategy/params/set", json={"params": params, "source": "test-api"})
                assert set_res.status_code == 200
                assert set_res.json()["params"]["a_change_5m_min_abs"] == 0.023
                assert params_path.exists()
    finally:
        app_module.repo.save_params_version(original_params, source="test-reset", make_current=True)


def test_backtest_and_leaderboard_endpoints(monkeypatch):
    with TestClient(app) as client:
        current = client.get("/ops/strategy/params").json()
        version_id = current["version_id"]
        res = client.post(
            "/ops/backtest/run",
            json={
                "from_ts": "2026-01-01T00:00:00+00:00",
                "to_ts": "2026-01-02T00:00:00+00:00",
                "params_version_id": version_id,
            },
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["params_version_id"] == version_id
        board = client.get("/ops/optimization/leaderboard")
        assert board.status_code == 200
        assert isinstance(board.json(), list)


def test_online_optimization_endpoint_runs(monkeypatch):
    with TestClient(app) as client:
        res = client.post("/ops/optimization/online/apply", params={"window_minutes": 30, "apply_best": True})
    assert res.status_code == 200
    payload = res.json()
    assert "applied" in payload
    assert "reason" in payload



def test_ops_preflight_failed_sends_webhook_notification(monkeypatch):
    sent: list[tuple[str, str, dict]] = []

    async def fake_send_notification(event_type: str, summary: str, details: dict):
        sent.append((event_type, summary, details))
        return True

    monkeypatch.setattr(
        app_module,
        "build_preflight_report",
        lambda: {
            "ok": False,
            "checks": [{"name": "gamma_connectivity", "ok": False, "message": "down"}],
        },
    )
    monkeypatch.setattr(app_module, "_send_notification", fake_send_notification)

    with TestClient(app) as client:
        res = client.get("/ops/preflight")

    assert res.status_code == 200
    assert sent
    assert sent[0][0] == "preflight_failed"
    assert sent[0][2]["failed_checks"][0]["name"] == "gamma_connectivity"



def test_lifespan_startup_degraded_sends_webhook_notification(monkeypatch):
    sent: list[tuple[str, str, dict]] = []

    async def fake_send_notification(event_type: str, summary: str, details: dict):
        sent.append((event_type, summary, details))
        return True

    async def failing_bootstrap_market_pool(top_n: int = 10):
        raise RuntimeError("gamma unavailable")

    def failing_refresh_market_subscriptions():
        raise RuntimeError("subscription refresh unavailable")

    async def fake_run_price_stream(_market_ids):
        while True:
            await __import__("asyncio").sleep(0.05)

    monkeypatch.setattr(app_module, "_send_notification", fake_send_notification)
    monkeypatch.setattr(app_module.orchestrator, "bootstrap_market_pool", failing_bootstrap_market_pool)
    monkeypatch.setattr(app_module.orchestrator, "refresh_market_subscriptions", failing_refresh_market_subscriptions)
    monkeypatch.setattr(app_module.orchestrator, "run_price_stream", fake_run_price_stream)

    with TestClient(app) as client:
        res = client.get("/health")

    assert res.status_code == 200
    assert sent
    assert sent[0][0] == "startup_degraded"
    assert sent[0][2]["bootstrap_error"] == "gamma unavailable"
