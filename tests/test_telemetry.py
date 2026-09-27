import unittest

from telemetry import (TelemetrySample, calculate_checksum, encode_telemetry,
                       is_checksum_failure, parse_firmware_health, parse_telemetry_line)


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

    def test_sequence_and_health_extensions_are_backward_compatible(self):
        packet = TelemetrySample(0, 0, 1, 0, 0, 0, sequence=42)
        self.assertEqual(parse_telemetry_line(encode_telemetry(packet)), packet)
        health = parse_firmware_health(
            "[SYS STATUS]: RTOS Nominal | Watchdog Active | Rate: 30ms | RX: 4 | Drop: 1 "
            "| Reset: IWDG | Stack: 112,184 | UID: 0123456789ABCDEF01234567"
        )
        self.assertEqual((health.reset_reason, health.telemetry_stack_words,
                          health.status_stack_words, health.command_drop), ("IWDG", 112, 184, 1))
        self.assertIsNone(parse_firmware_health("[SYS STATUS]: RTOS Nominal | RX: 4 | Drop: 1"))

    def test_checksum_failure_is_distinct_from_malformed_packet(self):
        packet = encode_telemetry(self.sample)
        corrupt = packet[:-2] + ("00" if packet[-2:] != "00" else "FF")
        self.assertTrue(is_checksum_failure(corrupt))
        self.assertFalse(is_checksum_failure("AX:malformed|CHK:0x00"))


if __name__ == "__main__":
    unittest.main()
