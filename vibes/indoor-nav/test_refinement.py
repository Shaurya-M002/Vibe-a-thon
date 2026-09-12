import unittest
import numpy as np
from refine import (
    cycle_vectors,
    summarize_vectors,
    robust_cycle_axes,
    wrap,
    plateau_turns,
)
from joint import dtw, similarity, elevation_segments
from reconstruct import rotate, axes, bandpass


class RefinementTests(unittest.TestCase):
    def test_phase_heading_survives_phone_pose_and_slow_hand_drift(self):
        t = np.arange(0, 30, 0.02)
        phase = 2 * np.pi * 1.7 * t
        theta = 0.6
        world = np.column_stack(
            [
                np.sin(phase) * np.cos(theta) + 0.05 * t,
                np.sin(phase) * np.sin(theta),
                np.cos(phase),
            ]
        )
        q = np.column_stack(
            [np.zeros(len(t)), np.zeros(len(t)), np.sin(0.7 * t), np.cos(0.7 * t)]
        )
        inv = q.copy()
        inv[:, :3] *= -1
        recovered = rotate(q, rotate(inv, world))
        st = np.arange(0, 30, 1 / 1.7)
        ct, cv, _ = cycle_vectors(t, recovered, st)
        s = summarize_vectors(ct, cv, np.array([10.0, 20.0]), 8)
        # Model sign can reverse by convention; fixed expected quadrature is -forward.
        self.assertLess(np.max(abs(np.degrees(wrap(s[:, 0] - (theta - np.pi))))), 2)
        self.assertGreater(s[:, 1].min(), 0.99)

    def test_alternating_cycle_direction_fails_consensus(self):
        times = np.arange(20.0)
        v = np.column_stack([(-1.0) ** np.arange(20), np.zeros(20)])
        s = summarize_vectors(times, v, np.array([10.0]), 8)
        self.assertLess(s[0, 1], 0.2)
        self.assertGreater(s[0, 3], 170)

    def test_plateau_turn_with_noisy_transition(self):
        c = np.arange(0.0, 60.0)
        a = np.where(c < 30, 0.0, np.pi / 2)
        good = abs(c - 30) > 3
        turns = plateau_turns(c, a, good, [a, a, a, a])
        self.assertEqual(len(turns), 1)
        self.assertLess(abs(turns[0]["time_s"] - 30), 3)
        self.assertAlmostEqual(turns[0]["change_deg_model_sign"], 90)

    def test_no_turn_from_transient_hand_impulse(self):
        c = np.arange(0.0, 60.0)
        a = np.zeros(len(c))
        a[28:32] = np.pi / 2
        good = abs(c - 30) > 4
        self.assertEqual(plateau_turns(c, a, good, [a, a, a, a]), [])

    def test_reciprocal_dtw_and_exact_identity(self):
        a = np.column_stack([np.linspace(0, 3, 40), np.sin(np.linspace(0, 5, 40))])
        b = a[::-1]
        dist = lambda x, y: np.linalg.norm(x[:, None] - y[None, :], axis=2)
        sf, p = dtw(dist(a, b))
        sr, q = dtw(dist(a, b[::-1]))
        self.assertEqual(sr, 0)
        self.assertGreater(sf, 0.5)
        np.testing.assert_array_equal(
            q, np.column_stack([np.arange(40), np.arange(40)])
        )

    def test_cycle_consensus_rejects_one_large_hand_impulse(self):
        t = np.arange(0, 20, 0.02)
        phase = 2 * np.pi * 2 * t
        world = np.column_stack([np.sin(phase), 0.1 * np.cos(phase), np.cos(phase)])
        world[:, 1] += 30 * np.sin(phase) * ((t >= 9) & (t < 9.5))
        baseline, _ = axes(t, bandpass(world), np.array([10.0]), 8)
        robust = robust_cycle_axes(t, world, np.arange(0, 20, 0.5), np.array([10.0]), 8)
        self.assertGreater(abs(np.sin(baseline[0])), 0.9)
        # FFT ringing spreads this impulse into neighboring cycles; require recovery
        # within 10 degrees, rather than exact recovery of the uncontaminated axis.
        self.assertLess(abs(np.degrees(np.arcsin(np.sin(robust[0, 0])))), 10)

    def test_piecewise_elevation_transition(self):
        t = np.arange(0, 100, 2.0)
        h = np.maximum(t - 60, 0) * 0.2
        result = elevation_segments(t, h)
        rising = [z for z in result["segments"] if z["slope_mps"] > 0.05]
        self.assertEqual(len(rising), 1)
        self.assertLessEqual(abs(rising[0]["start_s"] - 60), 4)
        self.assertAlmostEqual(rising[0]["slope_mps"], 0.2, places=5)

    def test_radio_similarity(self):
        self.assertAlmostEqual(similarity({"a": -60}, {"a": -60}), 1)
        self.assertEqual(similarity({"a": -60}, {"b": -60}), 0)


if __name__ == "__main__":
    unittest.main()
