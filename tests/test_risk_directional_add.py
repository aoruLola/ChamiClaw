import unittest

from chamiclaw.risk.engine import RiskEngine


class RiskDirectionalAddTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "risk": {
                "max_spread_bps": 300,
                "per_market_pos_pct": 0.5,
                "event_cluster_exposure_pct": 0.8,
                "daily_max_drawdown_pct": 0.2,
                "pre_expiry_add_position_block_min": 0,
                "max_open_orders_per_market": 10,
                "forbid_directional_add": True,
            }
        }
        self.engine = RiskEngine(self.cfg)

    def test_reject_when_directional_add_is_true(self):
        decision = self.engine.check(
            {
                "expected_edge_after_costs_bps": 50,
                "spread_bps": 40,
                "position_pct": 0.1,
                "cluster_exposure_pct": 0.1,
                "daily_drawdown_pct": 0.0,
                "open_orders_same_market": 0,
                "directional_add": True,
            }
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reject_code, "DIRECTIONAL_ADD_BLOCKED")

    def test_allow_when_directional_add_is_false(self):
        decision = self.engine.check(
            {
                "expected_edge_after_costs_bps": 50,
                "spread_bps": 40,
                "position_pct": 0.1,
                "cluster_exposure_pct": 0.1,
                "daily_drawdown_pct": 0.0,
                "open_orders_same_market": 0,
                "directional_add": False,
            }
        )
        self.assertTrue(decision.approved)


if __name__ == "__main__":
    unittest.main()
