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


# Whitespace is intentionally accepted around delimiters so older firmware and
# compact packets produced by the current firmware remain wire-compatible.
TELEMETRY_PATTERN = re.compile(
    r"AX:\s*(?P<ax>[-+0-9.eE]+)\s*\|\s*"
    r"AY:\s*(?P<ay>[-+0-9.eE]+)\s*\|\s*"
    r"AZ:\s*(?P<az>[-+0-9.eE]+)\s*\|\s*"
    r"GX:\s*(?P<gx>[-+0-9.eE]+)\s*\|\s*"
    r"GY:\s*(?P<gy>[-+0-9.eE]+)\s*\|\s*"
    r"GZ:\s*(?P<gz>[-+0-9.eE]+)\s*\|\s*"
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
    return f"{payload}|CHK:0x{calculate_checksum(payload):02X}"

