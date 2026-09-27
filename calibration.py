"""Host-side MPU6050 calibration from validated, uncorrected telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

from telemetry import TelemetrySample


FACES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")


@dataclass(frozen=True)
class CalibrationProfile:
    device_key: str
    created_at: str
    gyro_bias: tuple[float, float, float]
    accel_offset: tuple[float, float, float]
    accel_scale: tuple[float, float, float]
    samples_per_face: int
    sensor_model: str = "MPU6050"
    protocol: str = "legacy"
    faces_completed: int = 6

    @property
    def id(self) -> str:
        return f"CAL-{self.created_at.replace('-', '').replace(':', '')[:15]}"

    def apply(self, sample: TelemetrySample) -> TelemetrySample:
        acceleration = tuple((value - offset) * scale for value, offset, scale in
                             zip((sample.ax, sample.ay, sample.az), self.accel_offset, self.accel_scale))
        gyro = tuple(value - bias for value, bias in
                     zip((sample.gx, sample.gy, sample.gz), self.gyro_bias))
        return TelemetrySample(*acceleration, *gyro, sequence=sample.sequence)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    blend = position - lower
    return ordered[lower] * (1 - blend) + ordered[upper] * blend


def _robust_span(samples: list[TelemetrySample], field: str) -> float:
    """Ignore brief hand-tremor spikes while still detecting sustained motion."""
    values = [getattr(sample, field) for sample in samples]
    return _percentile(values, .9) - _percentile(values, .1)


def _trimmed_mean(samples: list[TelemetrySample], field: str) -> float:
    """Average the central 80% of a window so a few bumps do not skew calibration."""
    values = sorted(getattr(sample, field) for sample in samples)
    trim = max(1, len(values) // 10)
    central = values[trim:-trim]
    return sum(central) / len(central)


def detected_face(sample: TelemetrySample) -> str | None:
    axes = (sample.ax, sample.ay, sample.az)
    dominant = max(range(3), key=lambda index: abs(axes[index]))
    if abs(axes[dominant]) < .83 or any(abs(value) > .36 for index, value in enumerate(axes)
                                       if index != dominant):
        return None
    return ("+" if axes[dominant] > 0 else "-") + "XYZ"[dominant]


class CalibrationWizard:
    """Accepts a face only after a continuous, stable live sample window."""

    def __init__(self):
        self.stage = "idle"
        self.status = "Connect the sensor to begin."
        self.gyro_bias: tuple[float, float, float] | None = None
        self.faces: dict[str, tuple[float, float, float]] = {}
        self.window: deque[tuple[float, TelemetrySample]] = deque(maxlen=90)
        self.candidate: str | None = None
        self.samples_per_face = 0

    def start(self):
        self.stage = "gyro"
        self.status = "Hold the board comfortably; pause movement briefly for the gyro check."
        self.gyro_bias = None
        self.faces.clear()
        self.window.clear()
        self.candidate = None
        self.samples_per_face = 0

    def cancel(self, message="Calibration stopped."):
        self.stage = "idle"
        self.status = message
        self.window.clear()

    def hold_fraction(self) -> float:
        if self.stage not in ("gyro", "faces") or not self.window:
            return 0.0
        required_duration = 1.5 if self.stage == "gyro" else .8
        required_count = 35 if self.stage == "gyro" else 22
        duration = self.window[-1][0] - self.window[0][0]
        return min(1.0, duration / required_duration, len(self.window) / required_count)

    def feed(self, sample: TelemetrySample, now: float):
        if self.stage not in ("gyro", "faces") or not all(math.isfinite(v) for v in
            (sample.ax, sample.ay, sample.az, sample.gx, sample.gy, sample.gz)):
            return
        magnitude = math.sqrt(sample.ax**2 + sample.ay**2 + sample.az**2)
        gyro = (sample.gx, sample.gy, sample.gz)
        # A little hand motion is normal. Large acceleration or a clear rotation
        # still breaks the window; the robust stability check below handles tremor.
        gyro_limit = 6.5 if self.stage == "gyro" else 10.0
        if not .72 <= magnitude <= 1.28 or max(abs(value) for value in gyro) > gyro_limit:
            self.window.clear()
            self.candidate = None
            self.status = "Pause the movement for a moment; small hand tremors are okay."
            return
        face = detected_face(sample)
        if self.stage == "faces" and (face is None or face in self.faces):
            self.window.clear()
            self.candidate = None
            self.status = "Turn to an unfilled face, then pause while its tile fills."
            return
        if self.stage == "faces" and face != self.candidate:
            self.window.clear()
            self.candidate = face
        if self.window and now - self.window[-1][0] > .15:
            self.window.clear()
        self.window.append((now, sample))
        duration = now - self.window[0][0]
        required_duration = 1.5 if self.stage == "gyro" else .8
        required_count = 35 if self.stage == "gyro" else 22
        if duration < required_duration or len(self.window) < required_count:
            self.status = ("Pause briefly; small hand tremors are okay…" if self.stage == "gyro" else
                           f"Hold {face} upward…")
            return
        values = [entry[1] for entry in self.window]
        accel_stable = all(_robust_span(values, axis) <= .18 for axis in ("ax", "ay", "az"))
        gyro_tolerance = 4.5 if self.stage == "gyro" else 7.0
        gyro_stable = all(_robust_span(values, axis) <= gyro_tolerance
                          for axis in ("gx", "gy", "gz"))
        if not accel_stable or not gyro_stable:
            self.window.clear()
            self.status = "Pause the movement for a moment; small hand tremors are okay."
            return
        if self.stage == "gyro":
            self.gyro_bias = tuple(_trimmed_mean(values, axis) for axis in ("gx", "gy", "gz"))
            self.stage = "faces"
            self.status = "Now hold any unfilled face upward; pause briefly to capture it."
        else:
            self.faces[face] = tuple(_trimmed_mean(values, axis) for axis in ("ax", "ay", "az"))
            self.samples_per_face = min(self.samples_per_face or len(values), len(values))
            self.status = f"{face} captured. Turn to another face."
            if len(self.faces) == 6:
                self.stage = "ready"
                self.status = "All six faces captured. Review and save."
        self.window.clear()
        self.candidate = None

    def profile(self, device_key: str) -> CalibrationProfile:
        if self.stage != "ready" or self.gyro_bias is None:
            raise ValueError("Calibration is not complete")
        offsets = []
        scales = []
        for axis in "XYZ":
            index = "XYZ".index(axis)
            high = self.faces["+" + axis][index]
            low = self.faces["-" + axis][index]
            span = high - low
            if not 1.65 <= span <= 2.35 or abs((high + low) / 2) > .22:
                raise ValueError(f"{axis} axis failed validation. Repeat calibration with the faces level.")
            offsets.append((high + low) / 2)
            scales.append(2 / span)
        return CalibrationProfile(device_key, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  self.gyro_bias, tuple(offsets), tuple(scales), self.samples_per_face,
                                  protocol="health-v1" if device_key.startswith("uid:") else "legacy")


def default_store_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "STM32TelemetryConsole" / "calibrations.json"


def load_profile(device_key: str, path: Path | None = None) -> CalibrationProfile | None:
    try:
        data = json.loads((path or default_store_path()).read_text(encoding="utf-8"))
        record = data.get("profiles", {}).get(device_key)
        if record is None or data.get("version") != 1:
            return None
        profile = CalibrationProfile(device_key, record["created_at"],
            tuple(float(v) for v in record["gyro_bias"]),
            tuple(float(v) for v in record["accel_offset"]),
            tuple(float(v) for v in record["accel_scale"]), int(record["samples_per_face"]),
            str(record.get("sensor_model", "MPU6050")), str(record.get("protocol", "legacy")),
            int(record.get("faces_completed", 6)))
        if (not isinstance(profile.created_at, str) or profile.samples_per_face < 1 or
                profile.sensor_model != "MPU6050" or profile.faces_completed != 6):
            raise ValueError("Invalid calibration metadata")
        if any(len(values) != 3 or not all(math.isfinite(v) for v in values)
               for values in (profile.gyro_bias, profile.accel_offset, profile.accel_scale)):
            raise ValueError("Invalid calibration coefficients")
        if any(not .7 < scale < 1.3 for scale in profile.accel_scale):
            raise ValueError("Invalid acceleration scale")
        return profile
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def save_profile(profile: CalibrationProfile, path: Path | None = None):
    target = path or default_store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"version": 1, "profiles": {}}
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        data = {"version": 1, "profiles": {}}
    data["version"] = 1
    data.setdefault("profiles", {})[profile.device_key] = asdict(profile)
    temporary = target.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
