import tempfile
import unittest
from pathlib import Path

from session_io import SessionFormatError, SessionRecorder, SessionSample, load_session


def sample(timestamp=0.0):
    return SessionSample(timestamp, .03, 0, 0, 1, 1, 2, 3, 4, 5, 6, 0, 0, 0, "Stationary")


class SessionIoTests(unittest.TestCase):
    def test_round_trip_preserves_samples_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(Path(directory) / "run.csv", {"source": "demo"})
            recorder.write(sample(0.0))
            recorder.write(sample(.03))
            recorder.close({"application_version": "test"})
            session = load_session(Path(directory) / "run.json")
        self.assertEqual(len(session.samples), 2)
        self.assertEqual(session.metadata["source"], "demo")
        self.assertAlmostEqual(session.duration_s, .03)

    def test_recorder_rejects_non_monotonic_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            recorder = SessionRecorder(path, {})
            recorder.write(sample(.03))
            with self.assertRaises(ValueError):
                recorder.write(sample(0.0))
            recorder.close()

    def test_normalizes_the_recording_start_timestamp_to_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            recorder = SessionRecorder(path, {})
            recorder.write(sample(17.25))
            recorder.write(sample(17.28))
            recorder.close()
            session = load_session(path)
        self.assertEqual(session.samples[0].timestamp_s, 0.0)
        self.assertAlmostEqual(session.samples[-1].timestamp_s, .03)

    def test_rejects_incomplete_recording_without_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(Path(directory) / "run.csv", {})
            recorder.write(sample())
            recorder._file.close()
            with self.assertRaises(SessionFormatError):
                load_session(Path(directory) / "run.csv")


if __name__ == "__main__":
    unittest.main()
