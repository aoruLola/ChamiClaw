import unittest

from chamiclaw.risk.engine import RiskEngine


class RiskEngineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "risk": {
                "max_spread_bps": 120,
                "per_market_pos_pct": 0.015,
                "event_cluster_exposure_pct": 0.10,
                "daily_max_drawdown_pct": 0.04,
                "pre_expiry_add_position_block_min": 120,
                "max_open_orders_per_market": 1,
            }
        }
        self.engine = RiskEngine(self.cfg)

    def test_reject_spread(self):
        d = self.engine.check(
            {
                "expected_edge_after_costs_bps": 10,
                "spread_bps": 200,
                "position_pct": 0.01,
                "cluster_exposure_pct": 0.01,
                "daily_drawdown_pct": 0.0,
                "open_orders_same_market": 0,
            }
        )
        self.assertFalse(d.approved)
        self.assertEqual(d.reject_code, "SPREAD_TOO_WIDE")

    def test_approve_nominal(self):
        d = self.engine.check(
            {
                "expected_edge_after_costs_bps": 20,
                "spread_bps": 50,
                "position_pct": 0.01,
                "cluster_exposure_pct": 0.01,
                "daily_drawdown_pct": 0.0,
                "open_orders_same_market": 0,
            }
        )
        self.assertTrue(d.approved)

    def test_negative_edge_has_higher_priority_than_spread(self):
        d = self.engine.check(
            {
                "expected_edge_after_costs_bps": -1,
                "spread_bps": 999,
                "position_pct": 0.01,
                "cluster_exposure_pct": 0.01,
                "daily_drawdown_pct": 0.0,
                "open_orders_same_market": 0,
            }
        )
        self.assertFalse(d.approved)
        self.assertEqual(d.reject_code, "NEGATIVE_EDGE")


if __name__ == "__main__":
    unittest.main()
