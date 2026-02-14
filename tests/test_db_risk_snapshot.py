import tempfile
import unittest
from pathlib import Path

from chamiclaw.db.sqlite import Database
from chamiclaw.utils.time import utc_now_iso


class DatabaseRiskSnapshotTest(unittest.TestCase):
    def test_snapshot_uses_positions_orders_and_daily_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            db = Database(db_path)
            db.init_schema("sql/schema.sql")

            db.upsert_market(
                {
                    "market_id": "m1",
                    "event_id": "e1",
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
            db.insert_quote(
                {
                    "market_id": "m1",
                    "ts_utc": utc_now_iso(),
                    "yes_bid": 0.48,
                    "yes_ask": 0.52,
                    "no_bid": 0.48,
                    "no_ask": 0.52,
                    "yes_mid": 0.5,
                    "no_mid": 0.5,
                    "spread_bps": 80,
                    "depth_usd": 500,
                    "depth_imbalance": 0.0,
                    "sigma_5m": 0.01,
                    "raw": {},
                }
            )

            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO positions (market_id, yes_qty, no_qty, updated_at_utc) VALUES (?, ?, ?, ?)",
                    ("m1", 100.0, 0.0, utc_now_iso()),
                )
                conn.execute(
                    """
                    INSERT INTO trades (
                      trade_id, order_id, market_id, side, fill_price, fill_qty,
                      fee_usd, slippage_bps, pnl_usd, ts_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("t1", "o1", "m1", "buy_yes", 0.5, 10, 0.0, 0.0, -50.0, utc_now_iso()),
                )
            db.insert_order(
                {
                    "order_id": "o1",
                    "signal_id": "s1",
                    "market_id": "m1",
                    "side": "buy_yes",
                    "limit_price": 0.5,
                    "quantity": 10,
                    "status": "submitted",
                    "retries": 0,
                    "created_at_utc": utc_now_iso(),
                    "updated_at_utc": utc_now_iso(),
                }
            )

            snap = db.build_risk_snapshot(
                market_id="m1",
                event_id="e1",
                quote={"yes_mid": 0.5, "no_mid": 0.5},
                account_equity_usd=1000.0,
            )

        self.assertAlmostEqual(snap["position_pct"], 0.05, places=6)
        self.assertAlmostEqual(snap["cluster_exposure_pct"], 0.05, places=6)
        self.assertAlmostEqual(snap["daily_drawdown_pct"], 0.05, places=6)
        self.assertEqual(snap["open_orders_same_market"], 1)


if __name__ == "__main__":
    unittest.main()
