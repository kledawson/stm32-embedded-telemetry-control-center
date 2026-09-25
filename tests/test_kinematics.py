import math
import unittest

from kinematics import AttitudeEstimator
from telemetry import TelemetrySample


class AttitudeEstimatorTests(unittest.TestCase):
    def test_flat_stationary_sensor_has_zero_linear_acceleration(self):
        estimator = AttitudeEstimator()
        state = estimator.update(TelemetrySample(0, 0, 1, 0, 0, 0), 0.03)
        self.assertEqual((state.linear_ax, state.linear_ay, state.linear_az), (0.0, 0.0, 0.0))

    def test_tilted_stationary_sensor_converges_without_gravity_leakage(self):
        pitch = math.radians(20)
        roll = math.radians(-15)
        sample = TelemetrySample(
            -math.sin(roll) * math.cos(pitch),
            math.sin(pitch),
            math.cos(roll) * math.cos(pitch),
            0,
            0,
            0,
        )
        estimator = AttitudeEstimator()
        for _ in range(200):
            state = estimator.update(sample, 0.03)
        self.assertAlmostEqual(state.pitch, 20.0, delta=0.1)
        self.assertAlmostEqual(state.roll, -15.0, delta=0.1)
        self.assertAlmostEqual(state.linear_ax, 0.0, delta=0.05)
        self.assertAlmostEqual(state.linear_ay, 0.0, delta=0.05)
        self.assertAlmostEqual(state.linear_az, 0.0, delta=0.05)

    def test_large_dt_is_clamped(self):
        estimator = AttitudeEstimator(alpha=1.0)
        state = estimator.update(TelemetrySample(0, 0, 1, 0, 0, 10), 10.0)
        self.assertAlmostEqual(state.yaw, 2.5)


if __name__ == "__main__":
    unittest.main()

