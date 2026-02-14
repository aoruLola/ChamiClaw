import unittest

from chamiclaw.execution.executor import ExecutionEngine


class ExecutionEngineTest(unittest.TestCase):
    def setUp(self):
        self.signal = {"market_id": "m1", "side": "buy_yes"}

    def test_dry_run_place_order(self):
        engine = ExecutionEngine(
            {
                "apis": {"clob_base": "https://example.com"},
                "execution": {"dry_run": True},
            }
        )
        out = engine.place_limit_order(self.signal, 0.5, 10)
        self.assertEqual(out.status, "submitted")
        self.assertEqual(out.reason, "dry_run")

    def test_live_place_order_submit_then_fill(self):
        engine = ExecutionEngine(
            {
                "apis": {"clob_base": "https://example.com"},
                "execution": {
                    "dry_run": False,
                    "max_retries": 1,
                    "order_timeout_sec": 5,
                    "poll_interval_sec": 0.01,
                },
            }
        )
        calls = {"n": 0}

        def fake_request(method, path, payload=None):
            calls["n"] += 1
            if method == "POST" and path == "/orders":
                return {"order_id": "o1", "status": "submitted"}
            if method == "GET" and path == "/orders/o1":
                return {"status": "filled"}
            raise AssertionError(f"unexpected call: {method} {path}")

        engine._request_json = fake_request  # type: ignore[method-assign]
        out = engine.place_limit_order(self.signal, 0.5, 10)
        self.assertEqual(out.order_id, "o1")
        self.assertEqual(out.status, "filled")
        self.assertGreaterEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
