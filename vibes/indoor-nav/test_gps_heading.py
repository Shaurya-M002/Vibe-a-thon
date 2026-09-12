import unittest
import numpy as np
from gps_heading import (
    circular_mean,
    circular_median,
    quaternion_mean,
    facing,
    smooth,
    gps_anchor,
    evaluate_method,
    phone_turns,
)
from refine import wrap
from reconstruct import rotate


class GPSHeadingTests(unittest.TestCase):
    def test_north_wrap_circular_statistics(self):
        angles = np.radians([179, -179, 178, -178])
        mean, r = circular_mean(angles)
        self.assertLess(abs(abs(np.degrees(mean)) - 180), 1)
        self.assertLess(abs(abs(np.degrees(circular_median(angles))) - 180), 3)
        self.assertGreater(r, 0.99)
        north, _ = circular_mean(np.radians([359, 1]))
        self.assertLess(abs(np.degrees(north)), 1e-8)

    def test_quaternion_average_sign_invariance(self):
        q = np.array([0, 0, np.sin(0.4), np.cos(0.4)])
        result = quaternion_mean(np.array([q, -q, q]))
        np.testing.assert_allclose(
            rotate(result[None, :], np.array([[0, 1, 0]])),
            rotate(q[None, :], np.array([[0, 1, 0]])),
            atol=1e-12,
        )

    def test_near_vertical_phone_axis_is_unobservable(self):
        q = np.array([[np.sqrt(0.5), 0, 0, np.sqrt(0.5)]])
        angle, projection = facing(q, [0, 1, 0])
        self.assertTrue(np.isnan(angle[0]))
        self.assertLess(projection[0], 1e-6)

    def test_median_removes_impulse_but_not_persistent_sideways_offset(self):
        t = np.arange(0, 30, 0.2)
        q = np.tile([0, 0, 0, 1], (len(t), 1))
        a = np.full(len(t), np.pi / 2)
        a[(t > 7) & (t < 7.6)] = -np.pi / 2
        result = smooth(t, a, q, [0, 1, 0], 7, "median")
        self.assertLess(abs(wrap(result[np.argmin(abs(t - 7.4))] - np.pi / 2)), 1e-8)
        self.assertAlmostEqual(result[-1], np.pi / 2)

    def test_adaptive_smoothing_retains_sustained_change(self):
        t = np.arange(0, 30, 0.2)
        a = np.where(t < 15, 0.0, np.pi / 2)
        a[(t > 4) & (t < 4.4)] = 2.0
        q = np.tile([0, 0, 0, 1], (len(t), 1))
        h = smooth(t, a, q, [0, 1, 0], 15, "adaptive")
        self.assertLess(abs(h[np.argmin(abs(t - 4.2))]), 0.1)
        self.assertLess(abs(h[np.argmin(abs(t - 20))] - np.pi / 2), 0.1)

    def test_gps_displacement_must_exceed_radius(self):
        t = np.arange(0, 61.0)
        g = np.column_stack([t, t, np.zeros(len(t)), np.full(len(t), 3.0)])
        self.assertTrue(gps_anchor(g, 30)["informative"])
        self.assertFalse(gps_anchor(g, 30, radius_scale=2)["informative"])
        g[:, 1] = 0
        self.assertFalse(gps_anchor(g, 30)["informative"])

    def test_turn_variants_must_agree_pairwise(self):
        t = np.arange(0, 60, 0.2)
        variants = [np.where(t < 30, 0.0, np.radians(delta)) for delta in [50, 31, 69]]
        self.assertEqual(phone_turns(t, variants), [])
        consistent = [
            np.where(t < 30, offset, offset + np.radians(60))
            for offset in [0.0, 2.0, -2.0]
        ]
        self.assertTrue(phone_turns(t, consistent))

    def test_offset_validation_uses_nonoverlapping_windows(self):
        t = np.arange(0, 130, 0.2)
        heading = np.full(len(t), np.radians(20))
        ss = {"t": t, "steps": np.arange(1, 129, 0.6)}
        anchors = [
            {
                "center_s": z,
                "start_s": z - 10,
                "end_s": z + 10,
                "accepted": True,
                "direction_rad": np.radians(55),
            }
            for z in [20, 60, 100]
        ]
        result = evaluate_method(ss, heading, anchors)
        self.assertAlmostEqual(result["fitted_constant_offset_deg"], 35)
        self.assertEqual(result["heldout_nonoverlap_windows"], 3)
        self.assertLess(result["heldout_median_error_deg"], 1e-8)
        for item in result["heldout_details"]:
            self.assertTrue(
                all(
                    abs(c - item["center_s"]) > 25
                    for c in item["offset_training_centers_s"]
                )
            )


if __name__ == "__main__":
    unittest.main()
