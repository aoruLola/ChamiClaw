import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chamiclaw.app import PipelineResult


class _FakeDb:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.audit = []

    def init_schema(self, _):
        return None

    def insert_audit_event(self, **kwargs):
        self.audit.append(kwargs)

    def get_go_no_go_snapshot(self):
        if self._snapshots:
            return self._snapshots.pop(0)
        return {
            "duplicate_order_signals": 0,
            "edge_violation_orders": 0,
            "total_risk_rejects": 1,
            "risk_reject_complete": 1,
            "reconcile_recent_total": 1,
            "reconcile_recent_bad": 0,
            "llm_error_preds": 0,
            "llm_total_preds": 1,
        }


class _FakeApp:
    def run_once(self):
        return PipelineResult(
            scanned_markets=10,
            quotes_written=10,
            signals_generated=2,
            orders_submitted=1,
            signal_drop_counts={"EDGE_BELOW_ENTER_THRESHOLD": 1},
        )


class _FakeReconcileEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, db, apply_state=True):
        return {"state": "RUNNING", "mismatch_count": 0, "apply_state": apply_state}


class GoNoGoValidationTest(unittest.TestCase):
    def _good_snapshot(self):
        return {
            "duplicate_order_signals": 0,
            "edge_violation_orders": 0,
            "total_risk_rejects": 1,
            "risk_reject_complete": 1,
            "reconcile_recent_total": 1,
            "reconcile_recent_bad": 0,
            "llm_error_preds": 0,
            "llm_total_preds": 1,
        }

    def _bad_snapshot(self):
        x = self._good_snapshot()
        x["duplicate_order_signals"] = 1
        return x

    def test_validation_writes_report_and_reaches_required_streak(self):
        from chamiclaw.ops.go_no_go_validation import run_go_no_go_validation

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "go_no_go_validation.json"
            fake_db = _FakeDb([self._bad_snapshot(), self._good_snapshot(), self._good_snapshot()])
            fake_cfg = {"execution": {"dry_run": True}}

            with (
                patch("chamiclaw.ops.go_no_go_validation._build_app", return_value=(fake_cfg, fake_db, _FakeApp())),
                patch("chamiclaw.ops.go_no_go_validation.ReconcileEngine", _FakeReconcileEngine),
                patch(
                    "chamiclaw.ops.go_no_go_validation.run_llm_fallback_probe",
                    return_value={
                        "iterations": 20,
                        "signals_generated_under_forced_llm_failure": 20,
                        "dropped": 0,
                        "pass": True,
                    },
                ),
            ):
                report = run_go_no_go_validation(
                    config_path="config/config.yaml",
                    cycles=3,
                    reconcile_every=2,
                    fallback_iterations=20,
                    require_go_streak=2,
                    output_path=str(output),
                )

            self.assertTrue(output.exists())
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(report["cycles"]), 3)
            self.assertEqual(len(persisted["cycles"]), 3)
            self.assertEqual(report["final"]["verdict"], "GO")
            self.assertEqual(report["final"]["blockers"], [])

    def test_validation_adds_streak_blocker_when_not_met(self):
        from chamiclaw.ops.go_no_go_validation import run_go_no_go_validation

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "go_no_go_validation.json"
            fake_db = _FakeDb([self._good_snapshot(), self._bad_snapshot(), self._good_snapshot()])
            fake_cfg = {"execution": {"dry_run": True}}

            with (
                patch("chamiclaw.ops.go_no_go_validation._build_app", return_value=(fake_cfg, fake_db, _FakeApp())),
                patch("chamiclaw.ops.go_no_go_validation.ReconcileEngine", _FakeReconcileEngine),
                patch(
                    "chamiclaw.ops.go_no_go_validation.run_llm_fallback_probe",
                    return_value={
                        "iterations": 20,
                        "signals_generated_under_forced_llm_failure": 20,
                        "dropped": 0,
                        "pass": True,
                    },
                ),
            ):
                report = run_go_no_go_validation(
                    config_path="config/config.yaml",
                    cycles=3,
                    reconcile_every=2,
                    fallback_iterations=20,
                    require_go_streak=2,
                    output_path=str(output),
                )

            self.assertEqual(report["final"]["verdict"], "NO_GO")
            self.assertIn("go_streak_not_met", report["final"]["blockers"])


if __name__ == "__main__":
    unittest.main()
