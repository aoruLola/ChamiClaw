from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from chamiclaw.api import app as app_module
from chamiclaw.api.app import app


def test_ops_preflight_endpoint_returns_checklist(monkeypatch):
    def fake_preflight_report():
        return {
            "ok": True,
            "checks": [
                {"name": "execution_mode", "ok": True, "message": "ok"},
                {"name": "sqlite_writable", "ok": True, "message": "ok"},
            ],
        }

    monkeypatch.setattr(app_module, "build_preflight_report", fake_preflight_report)

    with TestClient(app) as client:
        res = client.get("/ops/preflight")

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert len(payload["checks"]) >= 2


def test_ops_strategy_params_get_and_set():
    with TestClient(app) as client:
        get_res = client.get("/ops/strategy/params")
        assert get_res.status_code == 200
        current = get_res.json()
        assert "version_id" in current
        params = current["params"]
        params["a_risk_pct"] = 0.0055
        set_res = client.post("/ops/strategy/params/set", json={"params": params, "source": "test-api"})
        assert set_res.status_code == 200
        updated = set_res.json()
        assert updated["params"]["a_risk_pct"] == 0.0055


def test_ops_backtest_and_optimization_leaderboard():
    now = datetime.now(timezone.utc)
    app_module.repo.record_trade_log(
        {
            "ts": (now - timedelta(minutes=2)).isoformat(),
            "market_id": "m1",
            "mode": "A",
            "action": "CLOSE",
            "pnl": 1.2,
        }
    )

    with TestClient(app) as client:
        backtest_res = client.post(
            "/ops/backtest/run",
            json={
                "from_ts": (now - timedelta(minutes=30)).isoformat(),
                "to_ts": now.isoformat(),
            },
        )
        assert backtest_res.status_code == 200
        backtest_payload = backtest_res.json()
        assert "score" in backtest_payload

        apply_res = client.post("/ops/optimization/online/apply", params={"window_minutes": 30, "apply_best": True})
        assert apply_res.status_code == 200
        apply_payload = apply_res.json()
        assert "applied" in apply_payload

        board = client.get("/ops/optimization/leaderboard")
        assert board.status_code == 200
        assert isinstance(board.json(), list)


def test_build_preflight_report_includes_account_state_check(monkeypatch):
    monkeypatch.setattr(app_module, "_probe_http", lambda url, timeout_seconds=3.0: (True, "ok"))
    report = app_module.build_preflight_report()
    names = {item["name"] for item in report["checks"]}

    assert "account_state" in names


def test_set_strategy_params_persists_to_params_path(tmp_path):
    previous_path = app_module.settings.params_path
    app_module.settings.params_path = str(tmp_path / "strategy_params.json")
    try:
        with TestClient(app) as client:
            current = client.get("/ops/strategy/params").json()
            params = current["params"]
            params["a_risk_pct"] = 0.009
            res = client.post("/ops/strategy/params/set", json={"params": params, "source": "persist-test"})
        assert res.status_code == 200
        params_file = tmp_path / "strategy_params.json"
        assert params_file.exists()
        payload = params_file.read_text(encoding="utf-8")
        assert '"a_risk_pct": 0.009' in payload
    finally:
        app_module.settings.params_path = previous_path


def test_optimization_online_apply_persists_params_on_rollback(monkeypatch):
    calls = {"count": 0}

    def fake_run_window(**kwargs):  # noqa: ANN003
        return {
            "window_minutes": kwargs["window_minutes"],
            "candidate_trials": 3,
            "best_trial": None,
            "applied": False,
            "rolled_back": True,
            "reason": "rolled_back_after_two_declines",
            "decline_streak": 2,
        }

    def fake_persist():
        calls["count"] += 1
        return True

    monkeypatch.setattr(app_module.online_tuner, "run_window", fake_run_window)
    monkeypatch.setattr(app_module, "_persist_current_params_to_path", fake_persist)

    with TestClient(app) as client:
        res = client.post("/ops/optimization/online/apply", params={"window_minutes": 30, "apply_best": True})

    assert res.status_code == 200
    assert calls["count"] == 1


def test_persist_current_params_to_path_handles_io_error(monkeypatch, tmp_path):
    previous_path = app_module.settings.params_path
    app_module.settings.params_path = str(tmp_path / "strategy_params.json")
    try:
        def boom(*args, **kwargs):  # noqa: ANN002,ANN003
            raise OSError("disk_full")

        monkeypatch.setattr(app_module.Path, "write_text", boom)
        ok = app_module._persist_current_params_to_path()
        assert ok is False
    finally:
        app_module.settings.params_path = previous_path
