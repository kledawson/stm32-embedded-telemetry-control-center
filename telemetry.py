"""Telemetry protocol helpers shared by the serial and demo backends."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    sequence: int | None = None


# Whitespace is intentionally accepted around delimiters so older firmware and
# compact packets produced by the current firmware remain wire-compatible.
TELEMETRY_PATTERN = re.compile(
    r"AX:\s*(?P<ax>[-+0-9.eE]+)\s*\|\s*"
    r"AY:\s*(?P<ay>[-+0-9.eE]+)\s*\|\s*"
    r"AZ:\s*(?P<az>[-+0-9.eE]+)\s*\|\s*"
    r"GX:\s*(?P<gx>[-+0-9.eE]+)\s*\|\s*"
    r"GY:\s*(?P<gy>[-+0-9.eE]+)\s*\|\s*"
    r"GZ:\s*(?P<gz>[-+0-9.eE]+)\s*\|\s*"
    r"(?:SEQ:\s*(?P<sequence>\d+)\s*\|\s*)?"
    r"CHK:\s*0x(?P<checksum>[0-9A-Fa-f]{1,2})"
)


def calculate_checksum(payload: str) -> int:
    """Return the firmware-compatible 8-bit XOR checksum."""
    checksum = 0
    for character in payload:
        checksum ^= ord(character)
    return checksum & 0xFF


def parse_telemetry_line(line: str) -> TelemetrySample | None:
    """Parse and validate one telemetry line, returning ``None`` if invalid."""
    match = TELEMETRY_PATTERN.fullmatch(line.strip())
    if match is None:
        return None

    payload, separator, _ = line.strip().partition("|CHK:")
    if not separator:
        return None
    expected = int(match.group("checksum"), 16)
    if calculate_checksum(payload) != expected:
        return None

    try:
        return TelemetrySample(
            ax=float(match.group("ax")),
            ay=float(match.group("ay")),
            az=float(match.group("az")),
            gx=float(match.group("gx")),
            gy=float(match.group("gy")),
            gz=float(match.group("gz")),
            sequence=int(match.group("sequence")) if match.group("sequence") is not None else None,
        )
    except ValueError:
        return None


def encode_telemetry(sample: TelemetrySample, spaced: bool = False) -> str:
    """Encode a sample for tests, simulations, and protocol diagnostics."""
    separator = " | " if spaced else "|"
    payload = separator.join(
        (
            f"AX:{sample.ax:.2f}",
            f"AY:{sample.ay:.2f}",
            f"AZ:{sample.az:.2f}",
            f"GX:{sample.gx:.1f}",
            f"GY:{sample.gy:.1f}",
            f"GZ:{sample.gz:.1f}",
        )
    )
    if sample.sequence is not None:
        payload += f"|SEQ:{sample.sequence}"
    return f"{payload}|CHK:0x{calculate_checksum(payload):02X}"


def is_checksum_failure(line: str) -> bool:
    """Separate a syntactically valid bad checksum from a malformed frame."""
    match = TELEMETRY_PATTERN.fullmatch(line.strip())
    if match is None:
        return False
    payload, separator, _ = line.strip().partition("|CHK:")
    return bool(separator) and calculate_checksum(payload) != int(match.group("checksum"), 16)


@dataclass(frozen=True, slots=True)
class FirmwareHealth:
    reset_reason: str
    telemetry_stack_words: int
    status_stack_words: int
    device_uid: str
    command_rx: int
    command_drop: int


HEALTH_PATTERN = re.compile(
    r"\[SYS STATUS\]:.*?RX:\s*(?P<rx>\d+)\s*\|\s*Drop:\s*(?P<drop>\d+)"
    r"\s*\|\s*Reset:\s*(?P<reset>[A-Z]+)\s*\|\s*Stack:\s*"
    r"(?P<telemetry>\d+),(?P<status>\d+)\s*\|\s*UID:\s*(?P<uid>[0-9A-Fa-f]{24})"
)


def parse_firmware_health(line: str) -> FirmwareHealth | None:
    match = HEALTH_PATTERN.fullmatch(line.strip())
    if match is None:
        return None
    return FirmwareHealth(match.group("reset"), int(match.group("telemetry")),
                          int(match.group("status")), match.group("uid").upper(),
                          int(match.group("rx")), int(match.group("drop")))
