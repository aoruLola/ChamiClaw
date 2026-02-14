import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chamiclaw.app import ChamiClawApp
from chamiclaw.db.sqlite import Database
from chamiclaw.utils.json_logger import JsonLogger


class AppPipelineTest(unittest.TestCase):
    def test_run_once_writes_signal_and_predictions(self):
        cfg = {
            "apis": {
                "gamma_base": "https://gamma-api.polymarket.com",
                "clob_base": "https://clob.polymarket.com",
            },
            "scan": {
                "min_liquidity_usd": 1,
                "min_depth_usd": 1,
                "min_time_to_expiry_min": 1,
                "max_time_to_expiry_days": 365,
                "max_markets_per_scan": 10,
            },
            "signal": {
                "enter_edge_bps": 250,
                "exit_edge_bps": 80,
                "no_trade_zone_bps": 120,
                "min_confidence": 0.1,
                "trading_fee_pct": 0.01,
                "slippage_bps": 10,
            },
            "risk": {
                "account_equity_usd": 10000,
                "per_market_pos_pct": 1.0,
                "event_cluster_exposure_pct": 1.0,
                "daily_max_drawdown_pct": 1.0,
                "max_spread_bps": 1000,
                "pre_expiry_add_position_block_min": 0,
                "max_open_orders_per_market": 10,
            },
            "execution": {"dry_run": True},
            "evaluate": {"paper_horizons_min": [5]},
            "llm": {"mode": "mock"},
        }

        fake_markets = [
            {
                "market_id": "m1",
                "event_id": "e1",
                "slug": "s1",
                "question": "q1",
                "description": "",
                "end_time_utc": "2099-01-01T00:00:00Z",
                "liquidity_usd": 100000,
                "volume_usd": 1000,
                "rule_summary": {},
                "tradable": True,
                "tradable_reason": "ok",
                "outcome_prices": [0.45, 0.45],
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "x.db"))
            db.init_schema("sql/schema.sql")
            app = ChamiClawApp(cfg, db, JsonLogger(str(Path(tmp) / "events.jsonl")))

            with patch("chamiclaw.app.scan_markets", return_value=fake_markets):
                result = app.run_once()

            self.assertEqual(result.signals_generated, 1)
            with db.connect() as conn:
                signal_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
                prediction_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            self.assertEqual(signal_count, 1)
            self.assertGreaterEqual(prediction_count, 2)


if __name__ == "__main__":
    unittest.main()
