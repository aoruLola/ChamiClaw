import unittest
from unittest.mock import patch

from chamiclaw.scanner.gamma_client import GammaClient


class _Resp:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class GammaClientTest(unittest.TestCase):
    def test_parse_outcomes_handles_bad_json(self):
        raw = {"outcomes": "[bad", "outcomePrices": "[0.5, 0.5]"}
        outcomes, prices = GammaClient.parse_outcomes(raw)
        self.assertEqual(outcomes, [])
        self.assertEqual(prices, [0.5, 0.5])

    def test_list_active_markets_retry_then_success(self):
        client = GammaClient("https://example.com")
        calls = {"n": 0}

        def _fake_urlopen(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("first call timeout")
            return _Resp(b'[{"id":"m1"}]')

        with patch("chamiclaw.scanner.gamma_client.urlopen", side_effect=_fake_urlopen):
            rows = client.list_active_markets(limit=1, retries=2, timeout=1)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "m1")


if __name__ == "__main__":
    unittest.main()
