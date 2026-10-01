"""Durable session recording and validation for the telemetry console.

The module deliberately has no Qt dependency.  It is used by the UI, but can
also be exercised by tests and future command-line diagnostics tools.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import csv
import json
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable


FORMAT_VERSION = 1
CSV_COLUMNS = (
    "timestamp_s", "received_dt_s",
    "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps",
    "pitch_deg", "roll_deg", "yaw_deg",
    "linear_ax_g", "linear_ay_g", "linear_az_g",
    "motion_state", "event_kind", "event_peak_g", "event_axis",
)


class SessionFormatError(ValueError):
    """A session file is incomplete, incompatible, or internally invalid."""


@dataclass(frozen=True, slots=True)
class SessionSample:
    timestamp_s: float
    received_dt_s: float
    ax_g: float
    ay_g: float
    az_g: float
    gx_dps: float
    gy_dps: float
    gz_dps: float
    pitch_deg: float
    roll_deg: float
    yaw_deg: float
    linear_ax_g: float
    linear_ay_g: float
    linear_az_g: float
    motion_state: str
    event_kind: str = ""
    event_peak_g: float | None = None
    event_axis: str = ""


@dataclass(frozen=True, slots=True)
class Session:
    path: Path
    metadata: dict[str, Any]
    samples: tuple[SessionSample, ...]

    @property
    def duration_s(self) -> float:
        return self.samples[-1].timestamp_s if self.samples else 0.0


def session_paths(path: str | Path) -> tuple[Path, Path]:
    """Return a matching CSV/JSON pair for either member of a session pair."""
    selected = Path(path)
    if selected.suffix.lower() == ".json":
        return selected.with_suffix(".csv"), selected
    if selected.suffix.lower() != ".csv":
        selected = selected.with_suffix(".csv")
    return selected, selected.with_suffix(".json")


class SessionRecorder:
    """Append samples to a line-buffered CSV and finalize its JSON manifest."""

    def __init__(self, path: str | Path, metadata: dict[str, Any]) -> None:
        selected = Path(path)
        self.archive_path = selected if selected.suffix.lower() == '.zip' else None
        self._staging = None
        if self.archive_path is not None:
            selected.parent.mkdir(parents=True, exist_ok=True)
            # Keep a flushed CSV on disk during recording, including on failure.
            self._staging = Path(tempfile.mkdtemp(prefix='.telemetry-recording-', dir=selected.parent))
            selected = self._staging / selected.with_suffix('.csv').name
        self.csv_path, self.metadata_path = session_paths(selected)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._metadata = dict(metadata)
        self._metadata.update({
            "format_version": FORMAT_VERSION,
            "csv_file": self.csv_path.name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        self._file = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS)
        self._writer.writeheader()
        self.sample_count = 0
        self._first_timestamp_s = None
        self._last_timestamp_s = 0.0
        self._closed = False

    @property
    def duration_s(self) -> float:
        return self._last_timestamp_s

    def write(self, sample: SessionSample) -> None:
        if self._closed:
            raise RuntimeError("Cannot write a closed session recorder")
        if self._first_timestamp_s is None:
            self._first_timestamp_s = sample.timestamp_s
        if sample.timestamp_s < self._first_timestamp_s:
            raise ValueError("Session sample timestamp predates the recording start")
        normalized_timestamp_s = sample.timestamp_s - self._first_timestamp_s
        self._last_timestamp_s = normalized_timestamp_s
        row = asdict(replace(sample, timestamp_s=normalized_timestamp_s))
        self._writer.writerow({column: row[column] for column in CSV_COLUMNS})
        # A sudden USB disconnect or app crash should leave a useful, readable
        # recording rather than only an in-memory buffer.
        self._file.flush()
        self.sample_count += 1

    def close(self, extra_metadata: dict[str, Any] | None = None) -> Path:
        if self._closed:
            return self.archive_path or self.metadata_path
        self._file.close()
        self._closed = True
        manifest = dict(self._metadata)
        manifest.update(extra_metadata or {})
        manifest["sample_count"] = self.sample_count
        manifest["duration_s"] = self._last_timestamp_s
        manifest["closed_at_utc"] = datetime.now(timezone.utc).isoformat()
        self.metadata_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if self.archive_path is not None:
            pending = self._staging / 'session.pending.zip'
            # Stored entries also open directly in the dependency-free browser demo.
            with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.write(self.csv_path, self.csv_path.name)
                archive.write(self.metadata_path, self.metadata_path.name)
            pending.replace(self.archive_path)
            self.csv_path.unlink()
            self.metadata_path.unlink()
            self._staging.rmdir()
            return self.archive_path
        return self.metadata_path


def load_session(path: str | Path, metadata_path: str | Path | None = None) -> Session:
    selected = Path(path)
    if selected.suffix.lower() == '.zip':
        try:
            with zipfile.ZipFile(selected) as archive:
                entries = archive.infolist()
                if len(entries) != 2 or any(entry.is_dir() or entry.file_size > 64 * 1024 * 1024 for entry in entries):
                    raise SessionFormatError('Choose a session ZIP containing one CSV and one JSON (maximum 64 MB each).')
                csv_entries = [entry for entry in entries if entry.filename.lower().endswith('.csv')]
                json_entries = [entry for entry in entries if entry.filename.lower().endswith('.json')]
                if len(csv_entries) != 1 or len(json_entries) != 1:
                    raise SessionFormatError('Session ZIP must contain exactly one CSV and one JSON.')
                # Read in memory; archive paths are never extracted to disk.
                csv_text = archive.read(csv_entries[0]).decode('utf-8-sig')
                metadata_text = archive.read(json_entries[0]).decode('utf-8-sig')
                return _load_session_text(csv_text, metadata_text, Path(csv_entries[0].filename).name, selected)
        except (zipfile.BadZipFile, UnicodeError, RuntimeError, NotImplementedError) as error:
            raise SessionFormatError(f'Cannot read session ZIP: {error}') from error
    csv_path, default_metadata = session_paths(selected)
    metadata_path = Path(metadata_path) if metadata_path is not None else default_metadata
    if not csv_path.is_file():
        raise SessionFormatError(f"Session data file not found: {csv_path}")
    if not metadata_path.is_file():
        raise SessionFormatError(f"Session metadata file not found: {metadata_path}")
    return _load_session_text(csv_path.read_text(encoding='utf-8-sig'), metadata_path.read_text(encoding='utf-8-sig'), csv_path.name, csv_path)


def _load_session_text(csv_text, metadata_text, csv_name, session_path):
    try:
        metadata = json.loads(metadata_text)
    except (OSError, json.JSONDecodeError) as error:
        raise SessionFormatError(f"Cannot read session metadata: {error}") from error
    if not isinstance(metadata, dict):
        raise SessionFormatError('Session metadata must be a JSON object')
    if metadata.get("format_version") != FORMAT_VERSION:
        raise SessionFormatError(
            f"Unsupported session format {metadata.get('format_version')!r}; expected {FORMAT_VERSION}"
        )
    if metadata.get("csv_file") not in (None, csv_name):
        raise SessionFormatError("Metadata points to a different CSV file")

    try:
        with io.StringIO(csv_text, newline='') as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None or set(CSV_COLUMNS) - set(reader.fieldnames):
                raise SessionFormatError("Session CSV is missing required columns")
            samples = tuple(_parse_row(row, row_number) for row_number, row in enumerate(reader, start=2))
    except OSError as error:
        raise SessionFormatError(f"Cannot read session data: {error}") from error

    if not samples:
        raise SessionFormatError("Session contains no samples")
    previous = -1.0
    for sample in samples:
        if sample.timestamp_s < previous:
            raise SessionFormatError("Session timestamps must be monotonic")
        previous = sample.timestamp_s
    declared_count = metadata.get("sample_count")
    if declared_count is not None and declared_count != len(samples):
        raise SessionFormatError("Session metadata sample count does not match CSV")
    return Session(session_path, metadata, samples)


def _parse_row(row: dict[str, str], row_number: int) -> SessionSample:
    floats = {}
    for name in CSV_COLUMNS[:14]:
        try:
            floats[name] = float(row[name])
        except (KeyError, TypeError, ValueError) as error:
            raise SessionFormatError(f"Invalid {name!r} at CSV row {row_number}") from error
    raw_peak = row.get("event_peak_g", "")
    try:
        peak = float(raw_peak) if raw_peak not in (None, "") else None
    except ValueError as error:
        raise SessionFormatError(f"Invalid 'event_peak_g' at CSV row {row_number}") from error
    return SessionSample(
        **floats,
        motion_state=row.get("motion_state", ""),
        event_kind=row.get("event_kind", ""),
        event_peak_g=peak,
        event_axis=row.get("event_axis", ""),
    )
