import unittest
import numpy as np
from reconstruct import rotate, quaternions, axes, bandpass, peaks, path


class ReconstructionTests(unittest.TestCase):
    def test_arbitrary_phone_pose_invariance(self):
        rng = np.random.default_rng(42)
        t = np.arange(0, 20, 0.02)
        world = np.column_stack(
            [
                2 * np.sin(2 * np.pi * 1.7 * t),
                0.15 * np.cos(2 * np.pi * 1.7 * t),
                np.full(len(t), 9.81),
            ]
        )
        q = rng.normal(size=(len(t), 4))
        q /= np.linalg.norm(q, axis=1)[:, None]
        inv = q.copy()
        inv[:, :3] *= -1
        device = rotate(inv, world)
        np.testing.assert_allclose(rotate(q, device), world, atol=1e-12)
        g = rotate(inv, np.tile([0, 0, 9.81], (len(t), 1)))
        recovered = rotate(q, device - g)
        aa, rr = axes(t, bandpass(recovered), np.arange(4, 16), 6)
        self.assertLess(np.max(abs(np.sin(aa))), 0.03)
        self.assertGreater(np.min(rr), 0.98)

    def test_quaternion_sign_equivalence(self):
        q = np.array([[0, 0, 0, 1], [0, 0, 0, -1]], float)
        np.testing.assert_allclose(
            quaternions(np.array([0.5]), np.array([0, 1]), q), [[0, 0, 0, 1]]
        )

    def test_known_cadence_and_distance(self):
        t = np.arange(0, 20, 0.02)
        x = np.sin(2 * np.pi * 2 * t)
        p = peaks(t, x)
        self.assertEqual(len(p), 40)
        trajectory = path(p, np.array([0, 20]), np.array([0, 0]), 0.7)
        np.testing.assert_allclose(trajectory[-1], [28, 0], atol=1e-10)

    def test_axis_ambiguity_is_preserved(self):
        t = np.arange(0, 20, 0.02)
        x = np.column_stack(
            [np.sin(2 * np.pi * 1.5 * t), np.zeros(len(t)), np.zeros(len(t))]
        )
        a, _ = axes(t, x, np.arange(4, 16), 6)
        b, _ = axes(t, -x, np.arange(4, 16), 6)
        np.testing.assert_allclose(np.exp(2j * a), np.exp(2j * b))


if __name__ == "__main__":
    unittest.main()
