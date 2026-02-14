import unittest

from chamiclaw.signal.structural import detect_pair_cost_signal


class StructuralTest(unittest.TestCase):
    def test_detect_pair_cost(self):
        sig = detect_pair_cost_signal(0.45, 0.45, 0.01)
        self.assertIsNotNone(sig)
        self.assertGreater(sig.edge_bps, 0)


if __name__ == "__main__":
    unittest.main()
