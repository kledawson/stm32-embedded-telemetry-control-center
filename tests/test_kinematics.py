import math
import unittest

from kinematics import AttitudeEstimator, RelativeDriveModel
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

    def test_dynamic_acceleration_does_not_override_fast_gyro_rotation(self):
        estimator = AttitudeEstimator()
        # A 2 g reading is not trustworthy as a gravity vector.  The gyro
        # still advances the estimate instead of snapping toward the accel angle.
        state = estimator.update(TelemetrySample(0, 2, 0, 100, 0, 0), 0.03)
        self.assertAlmostEqual(state.pitch, 3.0, delta=0.01)


class RelativeDriveModelTests(unittest.TestCase):
    def test_stationary_input_does_not_move_the_car(self):
        drive = RelativeDriveModel()
        state = drive.update(0.02, 0.03)
        self.assertEqual((state.velocity, state.position), (0.0, 0.0))

    def test_motion_is_bounded_and_can_be_recentered(self):
        drive = RelativeDriveModel(travel_limit=1.0)
        for _ in range(200):
            state = drive.update(1.0, 0.1)
        self.assertLessEqual(state.position, 1.0)
        drive.reset()
        self.assertEqual(drive.update(0.0, 0.03).position, 0.0)


if __name__ == "__main__":
    unittest.main()
