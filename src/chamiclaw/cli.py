from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from chamiclaw.app import ChamiClawApp
from chamiclaw.db.sqlite import Database
from chamiclaw.evaluate.backtest import run_simple_backtest
from chamiclaw.evaluate.calibration import bucket_calibration, calibrate_predictions
from chamiclaw.evaluate.report import write_daily_report
from chamiclaw.ops.alerting import post_discord_alert
from chamiclaw.ops.secrets import get_secret_access_snapshot
from chamiclaw.ops.state_machine import SystemStateMachine
from chamiclaw.ops.drill import run_all_drills, run_failure_drill
from chamiclaw.reconcile.engine import ReconcileEngine
from chamiclaw.settings import load_settings
from chamiclaw.utils.json_logger import JsonLogger
from chamiclaw.signal.engine import SignalEngine


def _build_app(config_path: str) -> tuple[dict, Database, ChamiClawApp]:
    settings = load_settings(config_path)
    cfg = settings.raw
    db = Database(settings.db_path)
    logger = JsonLogger("logs/events.jsonl")
    return cfg, db, ChamiClawApp(cfg, db, logger)


def cmd_init_db(config: str) -> None:
    _, db, _ = _build_app(config)
    db.init_schema("sql/schema.sql")
    print("Database initialized")


def cmd_run_once(config: str) -> None:
    cfg, db, app_obj = _build_app(config)
    db.init_schema("sql/schema.sql")
    result = app_obj.run_once()
    db.insert_audit_event(
        level="INFO",
        category="pipeline",
        code="RUN_ONCE_SUMMARY",
        message="run_once_completed",
        context={
            "scanned_markets": result.scanned_markets,
            "quotes_written": result.quotes_written,
            "signals_generated": result.signals_generated,
            "orders_submitted": result.orders_submitted,
            "signal_drop_counts": result.signal_drop_counts,
        },
    )
    print(json.dumps(
        {
            "scanned_markets": result.scanned_markets,
            "quotes_written": result.quotes_written,
            "signals_generated": result.signals_generated,
            "orders_submitted": result.orders_submitted,
            "dry_run": cfg["execution"].get("dry_run", True),
            "signal_drop_counts": result.signal_drop_counts,
        },
        ensure_ascii=True,
    ))


def cmd_run_loop(config: str) -> None:
    cfg, db, app_obj = _build_app(config)
    db.init_schema("sql/schema.sql")
    interval = int(cfg["scan"].get("interval_sec", 60))
    print(f"Running loop every {interval}s")
    while True:
        result = app_obj.run_once()
        print(
            f"scan={result.scanned_markets} quotes={result.quotes_written} "
            f"signals={result.signals_generated} orders={result.orders_submitted}"
        )
        time.sleep(interval)


def cmd_status(config: str) -> None:
    _, db, _ = _build_app(config)
    with db.connect() as conn:
        counts = {
            "markets": conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0],
            "quotes": conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],
            "signals": conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
            "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "audit_events": conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
        }
    print(json.dumps(counts, ensure_ascii=True))


def cmd_reconcile(config: str) -> None:
    cfg, db, _ = _build_app(config)
    engine = ReconcileEngine(cfg)
    result = engine.run(db, apply_state=True)
    print(json.dumps(result, ensure_ascii=True))


def cmd_calibrate(config: str) -> None:
    cfg, db, _ = _build_app(config)
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.fair_prob, CASE WHEN pr.realized_edge_bps > 0 THEN 1 ELSE 0 END AS outcome
            FROM signals p
            JOIN paper_results pr ON p.signal_id = pr.signal_id
            WHERE p.fair_prob IS NOT NULL
            """
        ).fetchall()

    predictions = [{"fair_prob": r[0], "outcome": r[1]} for r in rows]
    bucket_width = cfg["evaluate"].get("calibration_bucket_width", 0.05)
    summary = bucket_calibration(predictions, bucket_width)
    method = str(cfg.get("evaluate", {}).get("calibration_method", "isotonic"))
    cal = calibrate_predictions(predictions, method=method)
    mae_before = 0.0
    mae_after = 0.0
    if predictions:
        mae_before = sum(abs(float(x["fair_prob"]) - float(x["outcome"])) for x in predictions) / len(predictions)
        mae_after = (
            sum(abs(float(row["calibrated"]) - float(predictions[i]["outcome"])) for i, row in enumerate(cal["calibrated"]))
            / len(predictions)
        )
    recommendation = {
        "method": cal["method"],
        "mae_before": mae_before,
        "mae_after": mae_after,
        "improvement": mae_before - mae_after,
        "suggested_min_confidence": max(0.5, min(0.9, float(cfg["signal"].get("min_confidence", 0.62)) + (0.02 if mae_after > mae_before else -0.01))),
    }

    out_path = Path("reports/calibration.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "bucket_summary": summary,
                "calibration": cal,
                "recommendation": recommendation,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    new_version = f"cal-{int(time.time())}"
    db.insert_strategy_version(
        strategy_version=new_version,
        config_snapshot={
            "signal": cfg.get("signal", {}),
            "evaluate": cfg.get("evaluate", {}),
            "recommendation": recommendation,
        },
        notes=f"auto calibration via {cal['method']}",
    )
    print(f"Calibration written to {out_path}; strategy_version={new_version}")


def cmd_doctor(config: str) -> None:
    settings = load_settings(config)
    secret_access = get_secret_access_snapshot()
    checks = {
        "config_exists": Path(config).exists(),
        "schema_exists": Path("sql/schema.sql").exists(),
        "db_path": settings.db_path,
        "db_parent_exists": Path(settings.db_path).parent.exists(),
        "runtime_role": secret_access.role,
        "private_key_visible": secret_access.private_key_visible,
    }
    print(json.dumps(checks, ensure_ascii=True))


def cmd_state() -> None:
    sm = SystemStateMachine()
    s = sm.load()
    print(json.dumps({"state": s.state, "reason": s.reason, "updated_at_utc": s.updated_at_utc}, ensure_ascii=True))


def cmd_llm_fallback_check(config: str, iterations: int) -> None:
    cfg, _, _ = _build_app(config)
    engine = SignalEngine(cfg)

    class _FailingLlm:
        def infer(self, market_prob, features):
            raise RuntimeError("forced_llm_failure")

    engine.llm1 = _FailingLlm()  # type: ignore[assignment]
    ok = 0
    dropped = 0
    for i in range(max(1, int(iterations))):
        debug = {}
        signal = engine.generate(
            market={"market_id": f"fallback-{i}", "event_id": "evt", "end_time_utc": "2099-01-01T00:00:00Z"},
            quote={
                "yes_mid": 0.45,
                "no_mid": 0.45,
                "spread_bps": 80,
                "depth_imbalance": 0.1,
                "sigma_5m": 0.02,
                "depth_usd": 1000,
            },
            strategy_version="fallback-check",
            peer_markets=[],
            debug=debug,
        )
        if signal:
            ok += 1
        else:
            dropped += 1
    print(
        json.dumps(
            {
                "iterations": int(iterations),
                "signals_generated_under_forced_llm_failure": ok,
                "dropped": dropped,
                "pass": ok == int(iterations),
            },
            ensure_ascii=True,
        )
    )


def cmd_go_no_go(config: str) -> None:
    _, db, _ = _build_app(config)
    snap = db.get_go_no_go_snapshot()
    payload = _build_go_no_go_payload(snap)
    print(json.dumps(payload, ensure_ascii=True))


def _build_go_no_go_payload(snap: dict) -> dict:
    risk_complete_rate = 1.0
    if snap["total_risk_rejects"] > 0:
        risk_complete_rate = snap["risk_reject_complete"] / snap["total_risk_rejects"]
    llm_degrade_rate = 0.0
    if snap["llm_total_preds"] > 0:
        llm_degrade_rate = snap["llm_error_preds"] / snap["llm_total_preds"]
    checks = {
        "reconcile_stable_recent": snap["reconcile_recent_bad"] == 0 and snap["reconcile_recent_total"] > 0,
        "duplicate_orders_zero": snap["duplicate_order_signals"] == 0,
        "risk_reject_trace_complete": risk_complete_rate >= 1.0,
        "edge_check_coverage_ok": snap["edge_violation_orders"] == 0,
        "llm_degrade_controlled": llm_degrade_rate <= 0.2,
    }
    blockers = [k for k, v in checks.items() if not v]
    return {
        "verdict": "GO" if not blockers else "NO_GO",
        "checks": checks,
        "blockers": blockers,
        "metrics": {
            **snap,
            "risk_reject_trace_complete_rate": risk_complete_rate,
            "llm_degrade_rate": llm_degrade_rate,
        },
    }


def cmd_backtest(config: str) -> None:
    cfg, db, _ = _build_app(config)
    with db.connect() as conn:
        q_rows = conn.execute(
            "SELECT market_id, yes_mid, ts_utc FROM quotes ORDER BY ts_utc ASC LIMIT 20000"
        ).fetchall()
        s_rows = conn.execute(
            "SELECT market_id, side, strategy_version, created_at_utc FROM signals ORDER BY created_at_utc ASC LIMIT 5000"
        ).fetchall()
    quotes = [{"market_id": r[0], "yes_mid": r[1], "ts_utc": r[2]} for r in q_rows]
    signals = [{"market_id": r[0], "side": r[1], "strategy_version": r[2], "created_at_utc": r[3]} for r in s_rows]
    metrics = run_simple_backtest(
        quotes,
        signals,
        queue_delay_quotes=int(cfg.get("evaluate", {}).get("backtest_queue_delay_quotes", 1)),
        slippage_bps=float(cfg.get("evaluate", {}).get("backtest_slippage_bps", 10)),
    )
    by_strategy: dict[str, dict] = {}
    versions = sorted({str(s.get("strategy_version") or "unknown") for s in signals})
    for version in versions:
        sub = [s for s in signals if str(s.get("strategy_version") or "unknown") == version]
        m = run_simple_backtest(
            quotes,
            sub,
            queue_delay_quotes=int(cfg.get("evaluate", {}).get("backtest_queue_delay_quotes", 1)),
            slippage_bps=float(cfg.get("evaluate", {}).get("backtest_slippage_bps", 10)),
        )
        by_strategy[version] = {
            "total_return_pct": m.total_return_pct,
            "max_drawdown_pct": m.max_drawdown_pct,
            "sharpe_like": m.sharpe_like,
            "win_rate": m.win_rate,
            "trades": m.trades,
            "profit_factor": m.profit_factor,
            "avg_trade_return_pct": m.avg_trade_return_pct,
        }
    print(
        json.dumps(
            {
                "total_return_pct": metrics.total_return_pct,
                "max_drawdown_pct": metrics.max_drawdown_pct,
                "sharpe_like": metrics.sharpe_like,
                "win_rate": metrics.win_rate,
                "trades": metrics.trades,
                "profit_factor": metrics.profit_factor,
                "avg_trade_return_pct": metrics.avg_trade_return_pct,
                "by_strategy_version": by_strategy,
            },
            ensure_ascii=True,
        )
    )


def cmd_report(config: str) -> None:
    _, db, _ = _build_app(config)
    with db.connect() as conn:
        counts = {
            "markets": conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0],
            "quotes": conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],
            "signals": conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
            "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "audit_events": conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
        }
    sm = SystemStateMachine()
    state = sm.load()
    latest_strategy = db.get_latest_strategy_version()
    calibration_obj = {}
    cal_path = Path("reports/calibration.json")
    if cal_path.exists():
        try:
            calibration_obj = json.loads(cal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            calibration_obj = {}
    drill_summary = {"count": 0, "latest": None}
    drill_path = Path("reports/drills.jsonl")
    if drill_path.exists():
        lines = [x for x in drill_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        drill_summary["count"] = len(lines)
        if lines:
            try:
                drill_summary["latest"] = json.loads(lines[-1])
            except json.JSONDecodeError:
                drill_summary["latest"] = {"raw": lines[-1]}
    go_no_go = _build_go_no_go_payload(db.get_go_no_go_snapshot())
    latest_run_once = None
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT context_json
            FROM audit_events
            WHERE category='pipeline' AND code='RUN_ONCE_SUMMARY'
            ORDER BY ts_utc DESC
            LIMIT 1
            """
        ).fetchone()
    if row:
        try:
            latest_run_once = json.loads(row["context_json"] or "{}")
        except json.JSONDecodeError:
            latest_run_once = {"raw": row["context_json"]}
    write_daily_report(
        "reports/daily.json",
        {
            "system_state": state.state,
            "state_reason": state.reason,
            "counts": counts,
            "latest_strategy_version": latest_strategy["strategy_version"] if latest_strategy else None,
            "calibration_recommendation": calibration_obj.get("recommendation", {}),
            "drill_summary": drill_summary,
            "go_no_go": go_no_go,
            "latest_run_once": latest_run_once,
        },
    )
    print("Report written to reports/daily.json")


def cmd_alert_test(message: str, level: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print(json.dumps({"ok": False, "error": "DISCORD_WEBHOOK_URL not set"}, ensure_ascii=True))
        return
    res = post_discord_alert(webhook, level=level, title="ChamiClaw alert-test", detail=message, context={"source": "cli"})
    print(json.dumps({"ok": res.ok, "status": res.status, "detail": res.detail}, ensure_ascii=True))


def cmd_drill(scenario: str, apply_state: bool) -> None:
    if scenario == "all":
        rows = run_all_drills(apply_state=apply_state)
        print(
            json.dumps(
                [
                    {
                        "scenario": r.scenario,
                        "recommended_state": r.recommended_state,
                        "reason": r.reason,
                        "action_taken": r.action_taken,
                        "next_step": r.next_step,
                    }
                    for r in rows
                ],
                ensure_ascii=True,
            )
        )
        return
    result = run_failure_drill(scenario=scenario, apply_state=apply_state)
    print(
        json.dumps(
            {
                "scenario": result.scenario,
                "recommended_state": result.recommended_state,
                "reason": result.reason,
                "action_taken": result.action_taken,
                "next_step": result.next_step,
            },
            ensure_ascii=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ChamiClaw automated Polymarket system")
    p.add_argument(
        "command",
        choices=[
            "init-db",
            "run-once",
            "run-loop",
            "status",
            "reconcile",
            "calibrate",
            "doctor",
            "state",
            "llm-fallback-check",
            "go-no-go",
            "backtest",
            "report",
            "alert-test",
            "drill",
        ],
    )
    p.add_argument("--config", default="config/config.yaml", help="Path to config YAML/JSON")
    p.add_argument("--message", default="ChamiClaw alert test", help="Alert message for alert-test command")
    p.add_argument("--level", default="INFO", choices=["INFO", "WARN", "CRITICAL"], help="Alert level for alert-test")
    p.add_argument(
        "--scenario",
        default="api-failure",
        choices=["api-failure", "reconcile-mismatch", "drawdown-limit", "all"],
        help="Failure drill scenario for drill command",
    )
    p.add_argument(
        "--apply-state",
        action="store_true",
        help="Apply state transition during drill (default: dry-run)",
    )
    p.add_argument("--iterations", default=20, type=int, help="Iterations for llm-fallback-check")
    return p


def main() -> None:
    args = build_parser().parse_args()
    cmd = args.command

    if cmd == "init-db":
        cmd_init_db(args.config)
    elif cmd == "run-once":
        cmd_run_once(args.config)
    elif cmd == "run-loop":
        cmd_run_loop(args.config)
    elif cmd == "status":
        cmd_status(args.config)
    elif cmd == "reconcile":
        cmd_reconcile(args.config)
    elif cmd == "calibrate":
        cmd_calibrate(args.config)
    elif cmd == "doctor":
        cmd_doctor(args.config)
    elif cmd == "state":
        cmd_state()
    elif cmd == "llm-fallback-check":
        cmd_llm_fallback_check(args.config, args.iterations)
    elif cmd == "go-no-go":
        cmd_go_no_go(args.config)
    elif cmd == "backtest":
        cmd_backtest(args.config)
    elif cmd == "report":
        cmd_report(args.config)
    elif cmd == "alert-test":
        cmd_alert_test(args.message, args.level)
    elif cmd == "drill":
        cmd_drill(args.scenario, args.apply_state)


if __name__ == "__main__":
    main()
