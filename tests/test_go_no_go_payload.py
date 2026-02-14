import json
import tempfile
import unittest
from pathlib import Path

from chamiclaw.ops.go_no_go_validation import build_go_no_go_payload, load_go_no_go_validation_summary


class GoNoGoPayloadTest(unittest.TestCase):
    def test_payload_contains_metrics_and_blockers_match_failed_checks(self):
        snap = {
            "duplicate_order_signals": 1,
            "edge_violation_orders": 0,
            "total_risk_rejects": 4,
            "risk_reject_complete": 2,
            "reconcile_recent_total": 5,
            "reconcile_recent_bad": 1,
            "llm_error_preds": 1,
            "llm_total_preds": 2,
        }
        payload = build_go_no_go_payload(snap)

        self.assertIn("metrics", payload)
        self.assertIn("risk_reject_trace_complete_rate", payload["metrics"])
        self.assertIn("llm_degrade_rate", payload["metrics"])

        expected_blockers = [k for k, ok in payload["checks"].items() if not ok]
        self.assertEqual(sorted(payload["blockers"]), sorted(expected_blockers))

    def test_load_validation_summary_from_latest_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "go_no_go_validation.json"
            path.write_text(
                json.dumps(
                    {
                        "final": {
                            "verdict": "NO_GO",
                            "blockers": ["go_streak_not_met"],
                            "best_go_streak": 1,
                            "required_go_streak": 3,
                            "latest_go_no_go": {"checks": {"duplicate_orders_zero": False}},
                        }
                    },
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            summary = load_go_no_go_validation_summary(str(path))

        self.assertEqual(summary["verdict"], "NO_GO")
        self.assertEqual(summary["blockers"], ["go_streak_not_met"])
        self.assertEqual(summary["best_go_streak"], 1)
        self.assertEqual(summary["required_go_streak"], 3)

    def test_payload_splits_flow_and_trading_verdict(self):
        snap = {
            "duplicate_order_signals": 0,
            "edge_violation_orders": 0,
            "total_risk_rejects": 1,
            "risk_reject_complete": 1,
            "reconcile_recent_total": 5,
            "reconcile_recent_bad": 0,
            "llm_error_preds": 0,
            "llm_total_preds": 10,
            "recent_run_once_cycles": 5,
            "recent_signals_generated": 0,
            "edge_sample_count": 0,
            "edge_positive_after_cost_count": 0,
            "edge_positive_after_cost_ratio": 0.0,
        }
        payload = build_go_no_go_payload(
            snap,
            policy={
                "min_recent_cycles": 3,
                "min_recent_signals": 1,
                "min_edge_sample_size": 3,
                "min_edge_positive_ratio": 0.5,
            },
        )

        self.assertEqual(payload["flow_verdict"], "GO")
        self.assertEqual(payload["trading_verdict"], "NO_GO")
        self.assertEqual(payload["verdict"], "NO_GO")
        self.assertIn("min_signals_generated_recent", payload["trading_blockers"])


if __name__ == "__main__":
    unittest.main()
