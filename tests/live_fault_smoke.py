"""Exercise the one-shot fault commands against flashed hardware: python tests/live_fault_smoke.py COM3."""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial

from telemetry import is_checksum_failure, parse_firmware_health, parse_telemetry_line


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="COM3")
    args = parser.parse_args()
    connection = serial.Serial(args.port, 115200, timeout=.2)

    def line_until(predicate, timeout=10):
        nonlocal connection
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = connection.readline().decode("utf-8", errors="replace").strip()
            except serial.SerialException:
                connection.close()
                time.sleep(.3)
                try:
                    connection = serial.Serial(args.port, 115200, timeout=.2)
                except serial.SerialException:
                    continue
                continue
            if line and predicate(line):
                return line
        raise AssertionError(f"Timed out waiting for board evidence on {args.port}")

    try:
        connection.write(b"v\r")
        baseline = line_until(lambda line: parse_telemetry_line(line) is not None)
        print("Baseline valid:", baseline)

        connection.write(b"x\r")
        print("Checksum armed:", line_until(lambda line: line == "[FAULT]: CHECKSUM ARMED"))
        corrupt = line_until(is_checksum_failure, 5)
        print("Rejected real frame:", corrupt)
        print("Next valid:", line_until(lambda line: parse_telemetry_line(line) is not None, 5))

        connection.write(b"m\r")
        print("Mutex start:", line_until(lambda line: line == "[FAULT]: MUTEX HOLD"))
        print("Mutex end:", line_until(lambda line: line == "[FAULT]: MUTEX RELEASED"))
        print("Stream resumed:", line_until(lambda line: parse_telemetry_line(line) is not None, 5))

        connection.write(b"w\r")
        print("Watchdog starved:", line_until(lambda line: line == "[FAULT]: WATCHDOG STARVE"))
        health_line = line_until(
            lambda line: (health := parse_firmware_health(line)) is not None and health.reset_reason == "IWDG",
            25,
        )
        print("Verified reset:", health_line)
        print("Recovered data:", line_until(lambda line: parse_telemetry_line(line) is not None, 5))
        print("PASS: live checksum rejection, bounded mutex contention, hardware IWDG reset and recovery")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
