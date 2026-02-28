from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from chamiclaw.core.models import BacktestRequest, StrategyParams, StrategyParamsSetRequest


def _call_sync(name: str, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
    from chamiclaw.api import app as app_module

    fn = getattr(app_module, name)
    if asyncio.iscoroutinefunction(fn):
        return asyncio.run(fn(*args, **kwargs))
    return fn(*args, **kwargs)


def _load_runtime_repo():
    from chamiclaw.api import app as app_module

    return app_module.repo


def _build_daily_report(date_text: str) -> dict:
    from chamiclaw.api import app as app_module

    try:
        target = datetime.fromisoformat(date_text).date()
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    trades = []
    for row in app_module.repo.trade_logs:
        raw_ts = row.get("ts")
        if not isinstance(raw_ts, str):
            continue
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.date() != target:
            continue
        trades.append(row)
    pnl = sum(float(item.get("pnl", 0.0)) for item in trades)
    return {"date": target.isoformat(), "trades": len(trades), "pnl": pnl}


def _json_dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_preflight(_args: argparse.Namespace) -> int:
    _json_dump(_call_sync("ops_preflight"))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    env.update({"RUN_PROFILE": args.profile, "EXECUTION_DRY_RUN": "true" if args.profile == "sim" else "false"})
    command = ["uvicorn", "chamiclaw.api.app:app", "--host", "127.0.0.1", "--port", str(args.port)]
    completed = subprocess.run(command, check=False, env=env)
    return int(completed.returncode)


def _cmd_params_show(_args: argparse.Namespace) -> int:
    _json_dump(_call_sync("get_strategy_params"))
    return 0


def _cmd_params_set(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    params = StrategyParams.model_validate(payload)
    req = StrategyParamsSetRequest(params=params, source="cli")
    _json_dump(_call_sync("set_strategy_params", req, False))
    return 0


def _cmd_backtest_run(args: argparse.Namespace) -> int:
    params_version_id = args.params
    if args.params:
        candidate = Path(args.params)
        if candidate.exists() and candidate.is_file():
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            params = StrategyParams.model_validate(raw)
            version = _load_runtime_repo().save_params_version(
                params,
                source="cli_backtest_file",
                make_current=False,
            )
            params_version_id = version.version_id
    request = BacktestRequest(
        from_ts=datetime.fromisoformat(args.from_ts),
        to_ts=datetime.fromisoformat(args.to_ts),
        params_version_id=params_version_id,
    )
    _json_dump(_call_sync("backtest_run", request))
    return 0


def _cmd_optimize_online(args: argparse.Namespace) -> int:
    _json_dump(_call_sync("optimization_online_apply", args.window_minutes, args.apply_best))
    return 0


def _cmd_report_daily(args: argparse.Namespace) -> int:
    _json_dump(_build_daily_report(args.date))
    return 0


def _cmd_db_backup(args: argparse.Namespace) -> int:
    from chamiclaw.api import app as app_module

    src = Path(app_module.settings.sqlite_path)
    if not src.exists():
        raise FileNotFoundError(f"sqlite db not found: {src}")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{src.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(src, target)
    _json_dump({"backup": str(target)})
    return 0


def _cmd_db_vacuum(_args: argparse.Namespace) -> int:
    from chamiclaw.api import app as app_module

    db = Path(app_module.settings.sqlite_path)
    with sqlite3.connect(db) as conn:
        conn.execute("VACUUM")
    _json_dump({"vacuum": "ok", "db": str(db)})
    return 0


def _cmd_db_export(args: argparse.Namespace) -> int:
    from chamiclaw.api import app as app_module

    rows = list(app_module.repo.trade_logs)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "json":
        output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    else:
        fieldnames = sorted({k for row in rows for k in row.keys()}) if rows else []
        with output_path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
    _json_dump({"exported": len(rows), "path": str(output_path), "format": args.format})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chamiclaw")
    sub = parser.add_subparsers(dest="command", required=True)

    p_preflight = sub.add_parser("preflight")
    p_preflight.set_defaults(func=_cmd_preflight)

    p_run = sub.add_parser("run")
    p_run.add_argument("--profile", choices=["sim", "live"], required=True)
    p_run.add_argument("--port", type=int, default=8000)
    p_run.set_defaults(func=_cmd_run)

    p_params = sub.add_parser("params")
    p_params_sub = p_params.add_subparsers(dest="params_cmd", required=True)
    p_params_show = p_params_sub.add_parser("show")
    p_params_show.set_defaults(func=_cmd_params_show)
    p_params_set = p_params_sub.add_parser("set")
    p_params_set.add_argument("--file", required=True)
    p_params_set.set_defaults(func=_cmd_params_set)

    p_backtest = sub.add_parser("backtest")
    p_backtest_sub = p_backtest.add_subparsers(dest="backtest_cmd", required=True)
    p_backtest_run = p_backtest_sub.add_parser("run")
    p_backtest_run.add_argument("--from", dest="from_ts", required=True)
    p_backtest_run.add_argument("--to", dest="to_ts", required=True)
    p_backtest_run.add_argument("--params", default=None)
    p_backtest_run.set_defaults(func=_cmd_backtest_run)

    p_opt = sub.add_parser("optimize")
    p_opt_sub = p_opt.add_subparsers(dest="opt_cmd", required=True)
    p_opt_online = p_opt_sub.add_parser("online")
    p_opt_online.add_argument("--window-minutes", type=int, default=60)
    p_opt_online.add_argument("--apply-best", action="store_true")
    p_opt_online.set_defaults(func=_cmd_optimize_online)

    p_report = sub.add_parser("report")
    p_report_sub = p_report.add_subparsers(dest="report_cmd", required=True)
    p_report_daily = p_report_sub.add_parser("daily")
    p_report_daily.add_argument("--date", required=True)
    p_report_daily.set_defaults(func=_cmd_report_daily)

    p_db = sub.add_parser("db")
    p_db_sub = p_db.add_subparsers(dest="db_cmd", required=True)
    p_db_backup = p_db_sub.add_parser("backup")
    p_db_backup.add_argument("--output-dir", default="data/backups")
    p_db_backup.set_defaults(func=_cmd_db_backup)
    p_db_vacuum = p_db_sub.add_parser("vacuum")
    p_db_vacuum.set_defaults(func=_cmd_db_vacuum)
    p_db_export = p_db_sub.add_parser("export")
    p_db_export_sub = p_db_export.add_subparsers(dest="export_scope", required=True)
    p_db_export_trades = p_db_export_sub.add_parser("trades")
    p_db_export_trades.add_argument("--format", choices=["csv", "json"], default="json")
    p_db_export_trades.add_argument("--output", required=True)
    p_db_export_trades.set_defaults(func=_cmd_db_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
