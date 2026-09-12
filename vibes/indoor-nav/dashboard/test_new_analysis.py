import unittest
import numpy as np
from dashboard.analyze_new import vertical_candidates, fingerprint_score


class AnalysisTests(unittest.TestCase):
    def test_level_walk_is_not_vertical(self):
        t = np.arange(60.0)
        self.assertEqual(vertical_candidates(t, np.zeros(60), np.arange(60)), [])

    def test_height_change_without_steps(self):
        t = np.arange(60.0)
        h = np.clip((t - 20) / 5, 0, 1) * 3.5
        e = vertical_candidates(t, h, np.array([]))
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["kind"], "lift-like")

    def test_stairs_candidate_is_not_lift(self):
        t = np.arange(60.0)
        h = np.clip((t - 20) / 5, 0, 1) * 3.5
        self.assertEqual(
            vertical_candidates(t, h, np.arange(0, 60, 0.6))[0]["kind"],
            "walking + height change",
        )

    def test_disjoint_radio_sets(self):
        self.assertEqual(fingerprint_score({"a": -40}, {"b": -40}), (0, None, 0))

    def test_shared_radios_compare_strength(self):
        self.assertEqual(
            fingerprint_score({"a": -40, "b": -50}, {"a": -50, "b": -60}), (1, 10, 2)
        )


if __name__ == "__main__":
    unittest.main()
