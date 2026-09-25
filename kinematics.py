"""Attitude estimation and body-frame gravity compensation."""

from __future__ import annotations

from dataclasses import dataclass
import math

from telemetry import TelemetrySample


@dataclass(frozen=True, slots=True)
class MotionState:
    pitch: float
    roll: float
    yaw: float
    linear_ax: float
    linear_ay: float
    linear_az: float


@dataclass(frozen=True, slots=True)
class DriveState:
    """Deliberately bounded relative-motion state for the dashboard car demo.

    A six-axis IMU cannot yield reliable absolute position.  This model gives
    acceleration an intuitive, visible effect while using damping and a hard
    travel limit to keep integration drift from masquerading as navigation.
    """

    acceleration_g: float
    velocity: float
    position: float


class AttitudeEstimator:
    """Complementary attitude filter with orientation-aware gravity removal."""

    def __init__(
        self,
        alpha: float = 0.94,
        gyro_deadband: float = 1.2,
        acceleration_deadband: float = 0.05,
    ) -> None:
        self.alpha = alpha
        self.gyro_deadband = gyro_deadband
        self.acceleration_deadband = acceleration_deadband
        self.pitch = 0.0
        self.roll = 0.0
        self.yaw = 0.0

    def reset(self) -> None:
        self.pitch = self.roll = self.yaw = 0.0

    def update(self, sample: TelemetrySample, dt: float) -> MotionState:
        # Prevent a delayed serial frame or pause/resume from integrating one
        # gyro reading across an arbitrarily large time interval.
        dt = min(max(float(dt), 0.001), 0.25)
        gx = sample.gx if abs(sample.gx) > self.gyro_deadband else 0.0
        gy = sample.gy if abs(sample.gy) > self.gyro_deadband else 0.0
        gz = sample.gz if abs(sample.gz) > self.gyro_deadband else 0.0

        accel_pitch = math.degrees(
            math.atan2(sample.ay, math.sqrt(sample.ax**2 + sample.az**2))
        )
        accel_roll = math.degrees(math.atan2(-sample.ax, sample.az))

        # The accelerometer reports both gravity and real motion.  During a
        # quick push, its magnitude no longer looks like 1 g, so reduce its
        # correction influence and let the gyro preserve the fast rotation.
        acceleration_magnitude = math.sqrt(sample.ax**2 + sample.ay**2 + sample.az**2)
        gravity_trust = max(0.0, 1.0 - abs(acceleration_magnitude - 1.0) / 0.18)
        # Preserve the familiar 0.94/0.06 blend at 30 ms, while scaling the
        # correction with the actual packet interval rather than assuming a
        # fixed 33 Hz stream.
        correction_weight = (1.0 - self.alpha ** (dt / 0.03)) * gravity_trust
        predicted_pitch = self.pitch + gx * dt
        predicted_roll = self.roll + gy * dt
        self.pitch = (1.0 - correction_weight) * predicted_pitch + correction_weight * accel_pitch
        self.roll = (1.0 - correction_weight) * predicted_roll + correction_weight * accel_roll
        self.yaw = _wrap_degrees(self.yaw + gz * dt)

        pitch_rad = math.radians(self.pitch)
        roll_rad = math.radians(self.roll)

        # Expected gravity expressed in the sensor/body frame using the same
        # pitch/roll conventions as the accelerometer-angle equations above.
        gravity_x = -math.sin(roll_rad) * math.cos(pitch_rad)
        gravity_y = math.sin(pitch_rad)
        gravity_z = math.cos(roll_rad) * math.cos(pitch_rad)

        linear = (
            sample.ax - gravity_x,
            sample.ay - gravity_y,
            sample.az - gravity_z,
        )
        linear = tuple(
            value if abs(value) > self.acceleration_deadband else 0.0
            for value in linear
        )
        return MotionState(
            pitch=self.pitch,
            roll=self.roll,
            yaw=self.yaw,
            linear_ax=linear[0],
            linear_ay=linear[1],
            linear_az=linear[2],
        )


class RelativeDriveModel:
    """A responsive bounded force display, not an odometry estimator.

    Position is mapped from the currently measured linear acceleration and
    eases toward that target.  It therefore moves visibly during a push and
    returns to center at rest instead of accumulating an inaccurate integral.
    """

    def __init__(self, response_time: float = 0.16, travel_limit: float = 8.0) -> None:
        self.response_time = response_time
        self.travel_limit = travel_limit
        self.velocity = 0.0
        self.position = 0.0

    def reset(self) -> None:
        self.velocity = 0.0
        self.position = 0.0

    def update(self, acceleration_g: float, dt: float) -> DriveState:
        dt = min(max(float(dt), 0.001), 0.10)
        acceleration_g = acceleration_g if abs(acceleration_g) >= 0.06 else 0.0
        target_position = max(-self.travel_limit, min(self.travel_limit, acceleration_g * 14.0))
        previous_position = self.position
        self.position += (target_position - self.position) * (1.0 - math.exp(-dt / self.response_time))
        self.velocity = (self.position - previous_position) / dt
        return DriveState(acceleration_g, self.velocity, self.position)


def _wrap_degrees(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0
