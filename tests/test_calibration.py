import tempfile
import unittest
from pathlib import Path

from calibration import CalibrationWizard, FACES, load_profile, save_profile
from telemetry import TelemetrySample


class CalibrationTests(unittest.TestCase):
    def test_six_stable_faces_produce_corrected_profile_and_round_trip(self):
        wizard = CalibrationWizard()
        wizard.start()
        timestamp = 0.0
        bias = (.3, -.2, .1)
        offset = (.03, -.02, .01)
        scale = (1.02, .98, 1.01)

        def sample(face):
            vector = [0., 0., 0.]
            index = "XYZ".index(face[1])
            vector[index] = 1. if face[0] == "+" else -1.
            raw = [(value / factor) + shift for value, factor, shift in zip(vector, scale, offset)]
            return TelemetrySample(*raw, *bias)

        for _ in range(60):
            wizard.feed(sample("+Z"), timestamp)
            timestamp += .03
        self.assertEqual(wizard.stage, "faces")
        for face in FACES:
            for _ in range(30):
                wizard.feed(sample(face), timestamp)
                timestamp += .03
            self.assertIn(face, wizard.faces)
        self.assertEqual(wizard.stage, "ready")
        profile = wizard.profile("uid:0123456789ABCDEF01234567")
        corrected = profile.apply(sample("+Z"))
        for actual, expected in zip((corrected.ax, corrected.ay, corrected.az,
                                     corrected.gx, corrected.gy, corrected.gz), (0, 0, 1, 0, 0, 0)):
            self.assertAlmostEqual(actual, expected, places=5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calibrations.json"
            save_profile(profile, path)
            self.assertEqual(load_profile(profile.device_key, path), profile)
            self.assertIsNone(load_profile("uid:other", path))

    def test_motion_and_slow_stream_cannot_complete_stillness(self):
        wizard = CalibrationWizard()
        wizard.start()
        for index in range(50):
            wizard.feed(TelemetrySample(0, 0, 1, 9, 0, 0), index * .03)
        self.assertEqual(wizard.stage, "gyro")
        for index in range(50):
            wizard.feed(TelemetrySample(0, 0, 1, 0, 0, 0), 2 + index * .5)
        self.assertEqual(wizard.stage, "gyro")

    def test_small_hand_tremor_is_tolerated_for_gyro_check(self):
        wizard = CalibrationWizard()
        wizard.start()
        for index in range(60):
            phase = index % 4
            sample = TelemetrySample(
                (.04, -.04, .02, -.02)[phase], 0, 1,
                (1.8, -.4, -1.8, .4)[phase],
                (.8, -.8, .8, -.8)[phase], 0,
            )
            wizard.feed(sample, index * .03)
        self.assertEqual(wizard.stage, "faces")
        self.assertIsNotNone(wizard.gyro_bias)

    def test_validation_rejects_bad_accelerometer_pair(self):
        wizard = CalibrationWizard()
        wizard.stage = "ready"
        wizard.gyro_bias = (0, 0, 0)
        wizard.faces = {face: tuple((1 if face[0] == "+" else -.3) if
            "XYZ"[index] == face[1] else 0 for index in range(3)) for face in FACES}
        with self.assertRaisesRegex(ValueError, "axis failed validation"):
            wizard.profile("port:COM3")


if __name__ == "__main__":
    unittest.main()
