"""Hardware check: python tests/live_serial_smoke.py COM3 [--request-visual]."""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial

from telemetry import is_checksum_failure, parse_firmware_health, parse_telemetry_line
from calibration import CalibrationWizard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    parser.add_argument("--seconds", type=float, default=7.0)
    parser.add_argument("--gyro-check", action="store_true", help="Try the live stillness step without saving")
    parser.add_argument("--request-visual", action="store_true", help="Set the normal 30 ms visual stream rate")
    parser.add_argument("--expect-extended", action="store_true", help="Require sequence and health fields")
    args = parser.parse_args()
    valid = invalid = checksums = health_count = sequenced = presync = 0
    example = None
    last_health = None
    wizard = CalibrationWizard() if args.gyro_check else None
    if wizard:
        wizard.start()
    with serial.Serial(args.port, 115200, timeout=.25) as connection:
        if args.request_visual:
            connection.write(b"v\r")
            connection.flush()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            line = connection.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("AX:"):
                sample = parse_telemetry_line(line)
                if sample is None:
                    if not valid:
                        presync += 1
                        continue
                    invalid += 1
                    checksums += is_checksum_failure(line)
                    print("Invalid frame:", line[:120])
                else:
                    valid += 1
                    sequenced += sample.sequence is not None
                    example = example or line[:120]
                    if wizard:
                        wizard.feed(sample, time.monotonic())
            elif line.startswith("[SYS STATUS]:"):
                health_count += 1
                last_health = parse_firmware_health(line)
                print("Firmware status:", line)
    print(f"Validated frames: {valid}; ignored pre-sync fragments: {presync}; "
          f"invalid after sync: {invalid}; checksum failures: {checksums}; "
          f"frames with sequence: {sequenced}; health reports: {health_count}")
    print("First frame:", example or "none")
    print("Extended health parsed:", last_health is not None)
    if wizard:
        print("Live gyro check:", wizard.stage, wizard.status)
    if valid < 10:
        raise SystemExit("FAIL: fewer than ten validated frames")
    if invalid:
        raise SystemExit("FAIL: invalid live frames detected")
    if args.expect_extended and (sequenced != valid or last_health is None):
        raise SystemExit("FAIL: board is not sending the extended firmware protocol")
    print("PASS: live serial telemetry is valid")


if __name__ == "__main__":
    main()
