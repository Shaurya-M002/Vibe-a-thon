import unittest
import numpy as np
from path_variations import integrate, sample, position_at


class StepPaths(unittest.TestCase):
    def test_no_steps_no_drift(self):
        rows, _, miss = integrate(np.array([]), np.array([]), 0.7, [10, 20])
        np.testing.assert_allclose(rows[-1, 1:3], [10, 20])
        self.assertEqual(miss, 0)

    def test_turn_and_missing_heading(self):
        rows, _, miss = integrate(
            np.array([1, 2, 3]), np.array([0, np.pi / 2, np.nan]), 1, [0, 0]
        )
        np.testing.assert_allclose(rows[-1, 1:3], [1, 1])
        self.assertEqual(miss, 1)

    def test_correction_cap_and_holdout(self):
        update = {"t": 2, "xy": [100, 0], "source_start": 0, "source_end": 2}
        rows, corr, _ = integrate(np.array([1.0]), np.array([0.0]), 1, [0, 0], [update])
        self.assertEqual(corr[0]["shift_m"], 5)
        np.testing.assert_allclose(rows[-1, 1:3], [6, 0])
        held, corr, _ = integrate(
            np.array([1.0]), np.array([0.0]), 1, [0, 0], [update], (1, 3)
        )
        self.assertEqual(corr, [])
        np.testing.assert_allclose(held[-1, 1:3], [1, 0])

    def test_preceding_heading_and_position(self):
        vals = sample(
            np.array([1.0, 2.0, 3.0]),
            np.array([0.0, 1.0, 2.0]),
            np.array([0.5, 1.5, 4.0]),
        )
        self.assertTrue(np.isnan(vals[0]))
        self.assertEqual(vals[1], 0)
        self.assertTrue(np.isnan(vals[2]))
        np.testing.assert_allclose(
            position_at(np.array([[0, 0, 0], [2, 1, 1]]), 1), [0, 0]
        )


if __name__ == "__main__":
    unittest.main()
