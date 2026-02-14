import tempfile
import unittest
from pathlib import Path

from chamiclaw.db.sqlite import Database
from chamiclaw.evaluate.threshold_grid import parse_float_grid, run_threshold_grid_scan
from chamiclaw.utils.time import utc_now_iso


class ThresholdGridScanTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "signal": {
                "enter_edge_bps": 250,
                "llm_enter_edge_bps": 80,
                "exit_edge_bps": 80,
                "no_trade_zone_bps": 120,
                "min_confidence": 0.62,
                "trading_fee_pct": 0.0,
                "slippage_bps": 0.0,
                "chain_cost_bps": 0.0,
                "enable_cross_market_signal": True,
                "cross_market_gap_bps": 50,
                "enable_term_structure_signal": True,
                "term_structure_gap_bps": 50,
            },
            "llm": {"mode": "mock"},
        }

    def test_parse_float_grid(self):
        self.assertEqual(parse_float_grid("80, 100,80"), [80.0, 100.0])
        self.assertEqual(parse_float_grid(""), [])

    def test_threshold_grid_scan_outputs_rows_and_drop_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "x.db"))
            db.init_schema("sql/schema.sql")

            db.upsert_market(
                {
                    "market_id": "m1",
                    "event_id": "e1",
                    "slug": "s1",
                    "question": "q1",
                    "description": "",
                    "end_time_utc": "2099-01-01T00:00:00Z",
                    "liquidity_usd": 1000,
                    "volume_usd": 100,
                    "rule_summary": {},
                    "tradable": True,
                    "tradable_reason": "ok",
                }
            )
            db.upsert_market(
                {
                    "market_id": "m2",
                    "event_id": "e1",
                    "slug": "s2",
                    "question": "q2",
                    "description": "",
                    "end_time_utc": "2099-01-02T00:00:00Z",
                    "liquidity_usd": 1000,
                    "volume_usd": 100,
                    "rule_summary": {},
                    "tradable": True,
                    "tradable_reason": "ok",
                }
            )

            db.insert_quote(
                {
                    "market_id": "m1",
                    "ts_utc": utc_now_iso(),
                    "yes_bid": 0.44,
                    "yes_ask": 0.46,
                    "no_bid": 0.54,
                    "no_ask": 0.56,
                    "yes_mid": 0.45,
                    "no_mid": 0.55,
                    "spread_bps": 40,
                    "depth_usd": 2000,
                    "depth_imbalance": 0.1,
                    "sigma_5m": 0.01,
                    "raw": {},
                }
            )
            db.insert_quote(
                {
                    "market_id": "m2",
                    "ts_utc": utc_now_iso(),
                    "yes_bid": 0.54,
                    "yes_ask": 0.56,
                    "no_bid": 0.44,
                    "no_ask": 0.46,
                    "yes_mid": 0.55,
                    "no_mid": 0.45,
                    "spread_bps": 40,
                    "depth_usd": 2000,
                    "depth_imbalance": -0.1,
                    "sigma_5m": 0.01,
                    "raw": {},
                }
            )

            out = run_threshold_grid_scan(
                config=self.cfg,
                db=db,
                llm_enter_grid=[80.0, 250.0],
                min_conf_grid=[0.6, 0.8],
                market_limit=20,
            )

        self.assertEqual(len(out["rows"]), 4)
        for row in out["rows"]:
            self.assertIn("generated_signals", row)
            self.assertIn("drop_reasons", row)


if __name__ == "__main__":
    unittest.main()
