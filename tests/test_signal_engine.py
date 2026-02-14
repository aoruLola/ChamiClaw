import unittest

from chamiclaw.signal.engine import SignalEngine


class _FailingLlm1:
    def infer(self, market_prob, features):
        raise RuntimeError("llm1 down")


class _StubLlm1:
    def infer(self, market_prob, features):
        return type(
            "Out",
            (),
            {
                "fair_prob": market_prob + 0.01,
                "confidence": 0.9,
                "rationale": "stub_llm1",
                "risk_tags": [],
            },
        )()


class _StubLlm2:
    def validate(self, market_prob, fair_prob, features):
        return type(
            "Out",
            (),
            {
                "fair_prob": fair_prob,
                "confidence": 0.9,
                "rationale": "stub_llm2",
                "risk_tags": [],
            },
        )()


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

    def test_llm_enter_edge_threshold_override_allows_mid_edge_signal(self):
        cfg = {
            "signal": {
                "enter_edge_bps": 250,
                "llm_enter_edge_bps": 80,
                "exit_edge_bps": 80,
                "no_trade_zone_bps": 120,
                "min_confidence": 0.62,
                "trading_fee_pct": 0.0,
                "slippage_bps": 0.0,
                "chain_cost_bps": 0.0,
                "enable_cross_market_signal": False,
                "enable_term_structure_signal": False,
            },
            "llm": {"mode": "mock"},
        }
        engine = SignalEngine(cfg)
        engine.llm1 = _StubLlm1()
        engine.llm2 = _StubLlm2()

        debug = {}
        signal = engine.generate(
            market={"market_id": "m2"},
            quote={
                "yes_mid": 0.55,
                "no_mid": 0.45,
                "spread_bps": 20,
                "depth_imbalance": 0.0,
                "sigma_5m": 0.01,
                "depth_usd": 10_000,
            },
            strategy_version="v1",
            debug=debug,
        )
        self.assertIsNotNone(signal)
        self.assertIsNone(debug.get("drop_reason"))
        self.assertAlmostEqual(signal["expected_edge_after_costs_bps"], 100.0, places=6)

    def test_llm_enter_edge_threshold_falls_back_to_enter_edge_bps(self):
        cfg = {
            "signal": {
                "enter_edge_bps": 250,
                "exit_edge_bps": 80,
                "no_trade_zone_bps": 120,
                "min_confidence": 0.62,
                "trading_fee_pct": 0.0,
                "slippage_bps": 0.0,
                "chain_cost_bps": 0.0,
                "enable_cross_market_signal": False,
                "enable_term_structure_signal": False,
            },
            "llm": {"mode": "mock"},
        }
        engine = SignalEngine(cfg)
        engine.llm1 = _StubLlm1()
        engine.llm2 = _StubLlm2()

        debug = {}
        signal = engine.generate(
            market={"market_id": "m3"},
            quote={
                "yes_mid": 0.55,
                "no_mid": 0.45,
                "spread_bps": 20,
                "depth_imbalance": 0.0,
                "sigma_5m": 0.01,
                "depth_usd": 10_000,
            },
            strategy_version="v1",
            debug=debug,
        )
        self.assertIsNone(signal)
        self.assertEqual(debug.get("drop_reason"), "EDGE_BELOW_ENTER_THRESHOLD")


if __name__ == "__main__":
    unittest.main()
