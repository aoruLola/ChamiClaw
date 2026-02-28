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
