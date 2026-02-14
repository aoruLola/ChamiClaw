import unittest

from chamiclaw.signal.engine import SignalEngine


class _FailingLlm1:
    def infer(self, market_prob, features):
        raise RuntimeError("llm1 down")


class SignalEngineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "signal": {
                "enter_edge_bps": 250,
                "exit_edge_bps": 80,
                "no_trade_zone_bps": 120,
                "min_confidence": 0.62,
                "trading_fee_pct": 0.01,
                "slippage_bps": 40,
            },
            "llm": {"mode": "mock"},
        }
        self.market = {"market_id": "m1"}
        self.quote = {
            "yes_mid": 0.45,
            "no_mid": 0.45,
            "spread_bps": 40,
            "depth_imbalance": 0.1,
            "sigma_5m": 0.02,
        }

    def test_llm_success_records_two_predictions(self):
        engine = SignalEngine(self.cfg)
        signal = engine.generate(self.market, self.quote, "v1")
        self.assertIsNotNone(signal)
        self.assertFalse(signal["model_degraded"])
        self.assertEqual(len(signal["predictions"]), 2)
        self.assertEqual(signal["predictions"][0]["model_name"], "llm1")
        self.assertEqual(signal["predictions"][1]["model_name"], "llm2")

    def test_llm_failure_degrades_to_structural(self):
        engine = SignalEngine(self.cfg)
        engine.llm1 = _FailingLlm1()
        signal = engine.generate(self.market, self.quote, "v1")
        self.assertIsNotNone(signal)
        self.assertTrue(signal["model_degraded"])
        self.assertEqual(signal["signal_type"], "pair_cost_arb")
        self.assertEqual(signal["predictions"][0]["model_name"], "llm_error")


if __name__ == "__main__":
    unittest.main()
