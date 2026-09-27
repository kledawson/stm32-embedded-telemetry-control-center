import math
import unittest

from motion_events import MotionEvents
from telemetry import TelemetrySample


class MotionEventsTests(unittest.TestCase):
    def test_stationary_tilt_does_not_create_motion(self):
        events = MotionEvents()
        sample = TelemetrySample(0.6, 0, 0.8, 0, 0, 0)
        for i in range(100):
            self.assertIsNone(events.update(sample, i * .03))
        self.assertEqual(events.state, "Stationary")

    def test_impact_hysteresis_tracks_peak_and_rearms(self):
        events = MotionEvents()
        rest = TelemetrySample(0, 0, 1, 0, 0, 0)
        events.update(rest, 0)
        event = events.update(TelemetrySample(2.4, 0, 1, 0, 0, 0), .03)
        self.assertEqual((event.kind, event.axis), ("Impact", "X"))
        for i in range(1, 10):
            self.assertIsNone(events.update(TelemetrySample(3, 0, 1, 0, 0, 0), .03 + i * .03))
        self.assertAlmostEqual(events.latest.peak_g, math.sqrt(10))
        for i in range(12, 60):
            events.update(rest, i * .03)
        self.assertEqual(events.state, "Stationary")
        self.assertIsNotNone(events.update(TelemetrySample(3, 0, 1, 0, 0, 0), 2))

    def test_rotation_is_debounced_into_one_event(self):
        events = MotionEvents()
        sample = TelemetrySample(0, 0, 1, 0, 0, 90)
        recorded = [events.update(sample, i * .03) for i in range(50)]
        self.assertEqual(events.state, "Rotating")
        self.assertEqual(sum(event is not None for event in recorded), 1)
        self.assertEqual(events.latest.axis, "—")

    def test_shake_requires_repeated_changes_and_respects_gaps(self):
        events = MotionEvents()
        for i in range(50):
            x = .95 * math.sin(2 * math.pi * 4 * i * .03)
            events.update(TelemetrySample(x, 0, 1, 0, 0, 0), i * .03)
        self.assertEqual(events.state, "Shaking")
        for i in range(4):
            events.update(TelemetrySample(0, 0, 1, 0, 0, 0), 3 + i * 2)
        self.assertEqual(events.state, "Stationary")


if __name__ == '__main__':
    unittest.main()
