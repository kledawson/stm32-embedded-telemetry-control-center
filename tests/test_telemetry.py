import unittest

from telemetry import TelemetrySample, calculate_checksum, encode_telemetry, parse_telemetry_line


class TelemetryProtocolTests(unittest.TestCase):
    def setUp(self):
        self.sample = TelemetrySample(0.01, -0.02, 1.0, 0.1, 0.2, -0.3)

    def test_parses_compact_current_firmware_packet(self):
        line = encode_telemetry(self.sample)
        self.assertEqual(parse_telemetry_line(line), self.sample)

    def test_parses_legacy_spaced_packet(self):
        line = encode_telemetry(self.sample, spaced=True)
        self.assertEqual(parse_telemetry_line(line), self.sample)

    def test_rejects_bad_checksum(self):
        line = encode_telemetry(self.sample)
        self.assertIsNone(parse_telemetry_line(line[:-2] + "00"))

    def test_checksum_matches_known_xor(self):
        payload = "AX:0.00|AY:0.00"
        expected = 0
        for character in payload:
            expected ^= ord(character)
        self.assertEqual(calculate_checksum(payload), expected)


if __name__ == "__main__":
    unittest.main()
