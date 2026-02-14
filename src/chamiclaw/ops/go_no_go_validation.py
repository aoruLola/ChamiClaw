from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chamiclaw.app import ChamiClawApp
from chamiclaw.db.sqlite import Database
from chamiclaw.reconcile.engine import ReconcileEngine
from chamiclaw.settings import load_settings
from chamiclaw.signal.engine import SignalEngine
from chamiclaw.utils.json_logger import JsonLogger


def _build_app(config_path: str) -> tuple[dict[str, Any], Database, ChamiClawApp]:
    settings = load_settings(config_path)
    cfg = settings.raw
    db = Database(settings.db_path)
    logger = JsonLogger("logs/events.jsonl")
    return cfg, db, ChamiClawApp(cfg, db, logger)


def build_go_no_go_payload(snap: dict[str, Any]) -> dict[str, Any]:
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
    blockers = [k for k, ok in checks.items() if not ok]
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


def load_go_no_go_validation_summary(path: str = "reports/go_no_go_validation.json") -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"raw": p.read_text(encoding="utf-8")}
    final = raw.get("final", {}) if isinstance(raw, dict) else {}
    if not isinstance(final, dict):
        return {"raw": raw}
    return {
        "verdict": final.get("verdict"),
        "blockers": final.get("blockers", []),
        "best_go_streak": final.get("best_go_streak"),
        "required_go_streak": final.get("required_go_streak"),
        "latest_go_no_go_checks": (final.get("latest_go_no_go", {}) or {}).get("checks", {}),
    }


def run_llm_fallback_probe(cfg: dict[str, Any], iterations: int) -> dict[str, Any]:
    engine = SignalEngine(cfg)

    class _FailingLlm:
        def infer(self, market_prob, features):
            raise RuntimeError("forced_llm_failure")

    engine.llm1 = _FailingLlm()  # type: ignore[assignment]

    total = max(1, int(iterations))
    ok = 0
    dropped = 0
    for i in range(total):
        debug: dict[str, Any] = {}
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
    return {
        "iterations": total,
        "signals_generated_under_forced_llm_failure": ok,
        "dropped": dropped,
        "pass": ok == total,
    }


def _record_run_once_summary(db: Database, result: Any) -> dict[str, Any]:
    summary = {
        "scanned_markets": int(result.scanned_markets),
        "quotes_written": int(result.quotes_written),
        "signals_generated": int(result.signals_generated),
        "orders_submitted": int(result.orders_submitted),
        "signal_drop_counts": dict(result.signal_drop_counts),
    }
    db.insert_audit_event(
        level="INFO",
        category="pipeline",
        code="RUN_ONCE_SUMMARY",
        message="run_once_completed",
        context=summary,
    )
    return summary


def run_go_no_go_validation(
    config_path: str,
    cycles: int = 20,
    reconcile_every: int = 5,
    fallback_iterations: int = 50,
    require_go_streak: int = 3,
    output_path: str = "reports/go_no_go_validation.json",
) -> dict[str, Any]:
    cfg, db, app_obj = _build_app(config_path)
    db.init_schema("sql/schema.sql")

    total_cycles = max(1, int(cycles))
    reconcile_interval = max(1, int(reconcile_every))
    required_streak = max(1, int(require_go_streak))

    reconcile_engine = ReconcileEngine(cfg)
    go_streak = 0
    best_go_streak = 0
    rows: list[dict[str, Any]] = []

    for i in range(1, total_cycles + 1):
        run_once_result = app_obj.run_once()
        run_once_summary = _record_run_once_summary(db, run_once_result)

        reconcile_result = None
        if i % reconcile_interval == 0:
            reconcile_result = reconcile_engine.run(db, apply_state=True)

        go_no_go = build_go_no_go_payload(db.get_go_no_go_snapshot())
        if go_no_go["verdict"] == "GO":
            go_streak += 1
        else:
            go_streak = 0
        best_go_streak = max(best_go_streak, go_streak)

        rows.append(
            {
                "cycle": i,
                "run_once": run_once_summary,
                "reconcile": reconcile_result,
                "go_no_go": go_no_go,
                "go_streak": go_streak,
            }
        )

    fallback = run_llm_fallback_probe(cfg, fallback_iterations)
    last_go = rows[-1]["go_no_go"] if rows else build_go_no_go_payload(db.get_go_no_go_snapshot())
    blockers = list(last_go["blockers"])
    if best_go_streak < required_streak:
        blockers.append("go_streak_not_met")
    if not bool(fallback.get("pass")):
        blockers.append("llm_fallback_failed")

    report = {
        "config_path": config_path,
        "params": {
            "cycles": total_cycles,
            "reconcile_every": reconcile_interval,
            "fallback_iterations": int(fallback_iterations),
            "require_go_streak": required_streak,
        },
        "cycles": rows,
        "llm_fallback": fallback,
        "final": {
            "verdict": "GO" if not blockers else "NO_GO",
            "blockers": blockers,
            "best_go_streak": best_go_streak,
            "required_go_streak": required_streak,
            "latest_go_no_go": last_go,
        },
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report
