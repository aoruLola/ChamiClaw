import json
from datetime import datetime, timezone

from chamiclaw import cli


def test_cli_params_show_outputs_json(capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "_call_sync",
        lambda name, *args, **kwargs: {
            "version_id": "v-test",
            "params": {"a_risk_pct": 0.004},
        },
    )

    exit_code = cli.main(["params", "show"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "v-test" in captured.out


def test_cli_preflight_outputs_report(capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "_call_sync",
        lambda name, *args, **kwargs: {
            "ok": True,
            "checks": [{"name": "execution_mode", "ok": True, "message": "ok"}],
        },
    )

    exit_code = cli.main(["preflight"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"ok": true' in captured.out.lower()


def test_cli_report_daily_returns_summary(capsys, monkeypatch):
    now = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(
        cli,
        "_build_daily_report",
        lambda date_text: {"date": date_text, "trades": 1, "pnl": 1.5},
    )

    exit_code = cli.main(["report", "daily", "--date", now])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"trades": 1' in captured.out


def test_cli_parser_supports_db_export_trades_subcommand():
    parser = cli.build_parser()
    args = parser.parse_args(["db", "export", "trades", "--format", "json", "--output", "data/out.json"])

    assert args.command == "db"
    assert args.db_cmd == "export"
    assert args.export_scope == "trades"


def test_cli_backtest_supports_params_file(tmp_path, capsys, monkeypatch):
    params_file = tmp_path / "params.json"
    params_file.write_text(json.dumps({"a_risk_pct": 0.005}), encoding="utf-8")

    class StubRepo:
        def save_params_version(self, params, source="cli", score=None, make_current=False):  # noqa: ANN001
            _ = (params, source, score, make_current)
            return type("Obj", (), {"version_id": "pv-file"})()

    calls = {"request_version_id": None}

    def fake_call_sync(name, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
        if name == "backtest_run":
            calls["request_version_id"] = args[0].params_version_id
            return {"score": 0.1}
        return {"ok": True}

    monkeypatch.setattr(cli, "_call_sync", fake_call_sync)
    monkeypatch.setattr(cli, "_load_runtime_repo", lambda: StubRepo())

    exit_code = cli.main(
        [
            "backtest",
            "run",
            "--from",
            "2026-01-01T00:00:00+00:00",
            "--to",
            "2026-01-01T01:00:00+00:00",
            "--params",
            str(params_file),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "score" in captured.out
    assert calls["request_version_id"] == "pv-file"


def test_cli_params_set_supports_params_version_envelope(tmp_path, monkeypatch):
    params_file = tmp_path / "strategy_params.json"
    params_file.write_text(
        json.dumps({"version_id": "pv-123", "params": {"a_risk_pct": 0.009, "b_risk_pct": 0.02}}),
        encoding="utf-8",
    )
    captured = {"a_risk_pct": None, "b_risk_pct": None}

    def fake_call_sync(name, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
        if name == "set_strategy_params":
            req = args[0]
            captured["a_risk_pct"] = req.params.a_risk_pct
            captured["b_risk_pct"] = req.params.b_risk_pct
            return {"ok": True}
        return {"ok": True}

    monkeypatch.setattr(cli, "_call_sync", fake_call_sync)

    exit_code = cli.main(["params", "set", "--file", str(params_file)])

    assert exit_code == 0
    assert captured["a_risk_pct"] == 0.009
    assert captured["b_risk_pct"] == 0.02


def test_cli_backtest_supports_params_version_envelope_file(tmp_path, capsys, monkeypatch):
    params_file = tmp_path / "params.json"
    params_file.write_text(
        json.dumps({"version_id": "pv-123", "params": {"a_risk_pct": 0.006, "b_risk_pct": 0.011}}),
        encoding="utf-8",
    )
    repo_saved = {"a_risk_pct": None, "b_risk_pct": None}

    class StubRepo:
        def save_params_version(self, params, source="cli", score=None, make_current=False):  # noqa: ANN001
            repo_saved["a_risk_pct"] = params.a_risk_pct
            repo_saved["b_risk_pct"] = params.b_risk_pct
            _ = (source, score, make_current)
            return type("Obj", (), {"version_id": "pv-file"})()

    monkeypatch.setattr(cli, "_load_runtime_repo", lambda: StubRepo())
    monkeypatch.setattr(cli, "_call_sync", lambda *args, **kwargs: {"score": 0.2})

    exit_code = cli.main(
        [
            "backtest",
            "run",
            "--from",
            "2026-01-01T00:00:00+00:00",
            "--to",
            "2026-01-01T01:00:00+00:00",
            "--params",
            str(params_file),
        ]
    )
    _ = capsys.readouterr()

    assert exit_code == 0
    assert repo_saved["a_risk_pct"] == 0.006
    assert repo_saved["b_risk_pct"] == 0.011
