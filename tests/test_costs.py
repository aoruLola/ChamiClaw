import unittest

from chamiclaw.signal.costs import estimate_cost_bps


class CostModelTest(unittest.TestCase):
    def test_depth_impacts_slippage(self):
        cfg = {
            "signal": {
                "trading_fee_pct": 0.02,
                "slippage_bps": 40,
                "assumed_order_notional_usd": 200,
                "chain_cost_bps": 5,
            }
        }
        deep = estimate_cost_bps(cfg, {"depth_usd": 2000})
        shallow = estimate_cost_bps(cfg, {"depth_usd": 20})
        self.assertEqual(deep.fee_bps, 200.0)
        self.assertEqual(deep.chain_bps, 5.0)
        self.assertGreater(shallow.slippage_bps, deep.slippage_bps)


if __name__ == "__main__":
    unittest.main()
