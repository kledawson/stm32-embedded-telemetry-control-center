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

        self.pitch = self.alpha * (self.pitch + gx * dt) + (1.0 - self.alpha) * accel_pitch
        self.roll = self.alpha * (self.roll + gy * dt) + (1.0 - self.alpha) * accel_roll
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


def _wrap_degrees(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0

