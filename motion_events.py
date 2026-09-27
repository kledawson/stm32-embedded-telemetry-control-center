"""Small, sample-based motion heuristics; no integration or position tracking."""

from collections import deque
from dataclasses import dataclass
import math

from telemetry import TelemetrySample


@dataclass(frozen=True)
class MotionEvent:
    kind: str
    timestamp: float
    peak_g: float
    axis: str


class MotionEvents:
    """Detect impacts with hysteresis, and summarize recent measured activity.

    Thresholds are demonstration heuristics, not calibrated safety limits.
    The axis is the largest sample-to-sample acceleration change. It is only
    reported for impacts/shaking, not inferred from the gravity-bearing Z axis.
    """

    def __init__(self):
        self.state = "Waiting for data"
        self.latest = None
        self.magnitude = 0.0
        self.rotation = 0.0
        self.history = deque(maxlen=80)
        self.previous = None
        self.impact_active = False
        self.impact_until = -math.inf
        self.cooldown_until = -math.inf
        self.quiet_since = None
        self.candidate = "Stationary"
        self.candidate_since = 0.0

    def update(self, sample: TelemetrySample, now: float):
        axes = (sample.ax, sample.ay, sample.az)
        self.magnitude = math.sqrt(sum(v * v for v in axes))
        self.rotation = math.sqrt(sample.gx**2 + sample.gy**2 + sample.gz**2)
        # A pause or low-rate stream must not masquerade as a continuous shake.
        if self.history and now - self.history[-1][0] > 0.3:
            self.history.clear()
            self.previous = None
        delta = [abs(a - b) for a, b in zip(axes, self.previous or axes)]
        axis = "XYZ"[max(range(3), key=delta.__getitem__)] if max(delta) > 0.05 else "—"
        self.previous = axes
        self.history.append((now, self.magnitude))
        while self.history and now - self.history[0][0] > 0.8:
            self.history.popleft()

        emitted = None
        if self.impact_active:
            if self.magnitude > self.latest.peak_g:
                self.latest = MotionEvent("Impact", self.latest.timestamp, self.magnitude,
                                          axis if axis != "—" else self.latest.axis)
            if self.magnitude < 1.5:
                if self.quiet_since is None:
                    self.quiet_since = now
                if now - self.quiet_since >= 0.15:
                    self.impact_active = False
                    self.cooldown_until = now + 0.5
            else:
                self.quiet_since = None
        elif self.magnitude >= 2.0 and now >= self.cooldown_until:
            self.impact_active = True
            self.impact_until = now + 0.7
            self.quiet_since = None
            self.latest = emitted = MotionEvent("Impact", now, self.magnitude, axis)

        if self.impact_active or now < self.impact_until:
            self.state = "Impact"
            return emitted

        values = [value for _, value in self.history]
        slopes = [b - a for a, b in zip(values, values[1:]) if abs(b - a) > 0.025]
        turns = sum(a * b < 0 for a, b in zip(slopes, slopes[1:]))
        shaking = len(values) >= 8 and max(values) - min(values) > 0.25 and turns >= 3
        if shaking:
            candidate = "Shaking"
        elif self.rotation > (8 if self.state == "Rotating" else 12):
            candidate = "Rotating"
        elif abs(self.magnitude - 1.0) > 0.12:
            candidate = "Active motion"
        else:
            candidate = "Stationary"
        if candidate != self.candidate:
            self.candidate, self.candidate_since = candidate, now
        # Debounce labels; one burst adds one event, not a row per sample.
        if now - self.candidate_since >= 0.18 and candidate != self.state:
            self.state = candidate
            if candidate in ("Shaking", "Rotating"):
                self.latest = emitted = MotionEvent(candidate, now, max(values),
                                                    axis if candidate == "Shaking" else "—")
        return emitted
