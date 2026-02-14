import unittest

from chamiclaw.evaluate.calibration import bucket_calibration


class CalibrationTest(unittest.TestCase):
    def test_bucket(self):
        rows = [
            {"fair_prob": 0.61, "outcome": 1},
            {"fair_prob": 0.63, "outcome": 0},
            {"fair_prob": 0.23, "outcome": 0},
        ]
        out = bucket_calibration(rows, 0.05)
        self.assertTrue(len(out) >= 2)


if __name__ == "__main__":
    unittest.main()
