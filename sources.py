"""Live serial, demo, and replay workers for the dashboard."""

import math
import random
import time
from collections import deque
from threading import Event, Lock

import serial
from PyQt6.QtCore import QThread, pyqtSignal

from session_io import Session
from telemetry import (TelemetrySample, encode_telemetry,
                       is_checksum_failure, parse_firmware_health, parse_telemetry_line)


# --- BACKGROUND SERIAL WORKER ---
class SerialWorker(QThread):
    data_received = pyqtSignal(float, float, float, float, float, float, float)
    log_received = pyqtSignal(str)
    sequence_received = pyqtSignal(object)
    health_received = pyqtSignal(object)
    fault_marker_received = pyqtSignal(str)
    rejected_frame_received = pyqtSignal(str)
    accepted_frame_received = pyqtSignal(str)

    def __init__(self, port, baud=115200):
        super().__init__()
        self.port, self.baud, self.running, self.ser, self.last_time = port, baud, True, None, None
        self.invalid_frames = 0
        self.checksum_failures = 0
        self.invalid_frame_examples = deque(maxlen=3)
        self.recovered_frames = 0
        self.synchronized = False

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            self.log_received.emit(f"SYS >> UART CONNECTED · {self.port} · {self.baud} BAUD")
            self.last_time = time.monotonic()
            receive_buffer = bytearray()
            while self.running and self.ser.is_open:
                if self.ser.in_waiting:
                    receive_buffer.extend(self.ser.read(self.ser.in_waiting))
                    while b'\n' in receive_buffer:
                        raw_line, _, remaining = receive_buffer.partition(b'\n')
                        receive_buffer = bytearray(remaining)
                        self._handle_line(raw_line.decode('utf-8', errors='replace').strip())
                    # Bound memory if a disconnected/noisy device never emits
                    # a terminator; keeping its tail gives a later valid frame
                    # a chance to resynchronize.
                    if len(receive_buffer) > 4096:
                        receive_buffer = receive_buffer[-512:]
                time.sleep(0.001)
        except Exception as e:
            if self.running:
                self.log_received.emit(f"WARN >> SERIAL ERROR · {e}")
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

    def _handle_line(self, line: str):
        if not line:
            return
        accepted_line = line
        sample = parse_telemetry_line(line)
        if sample is None:
            # If an unterminated prefix is followed by a complete telemetry
            # packet, retain the final packet instead of rejecting both.
            final_packet_start = line.rfind("AX:")
            if final_packet_start > 0:
                sample = parse_telemetry_line(line[final_packet_start:])
                if sample is not None:
                    accepted_line = line[final_packet_start:]
                    self.recovered_frames += 1
        if sample is not None:
            self.synchronized = True
            now = time.monotonic()
            dt = now - self.last_time if self.last_time else 0.03
            self.last_time = now
            self.sequence_received.emit(sample.sequence)
            self.accepted_frame_received.emit(accepted_line)
            self.data_received.emit(sample.ax, sample.ay, sample.az, sample.gx, sample.gy, sample.gz, dt)
        elif line.startswith("AX:"):
            # Opening a serial port can start in the middle of an
            # already-transmitted line.  Discard pre-sync fragments rather
            # than presenting a false packet-integrity fault.
            if not self.synchronized:
                return
            self.invalid_frames += 1
            if is_checksum_failure(line):
                self.checksum_failures += 1
                self.rejected_frame_received.emit(line)
            self.invalid_frame_examples.append(line)
            if self.invalid_frames == 1 or self.invalid_frames % 50 == 0:
                self.log_received.emit(f"WARN >> REJECTED {self.invalid_frames} INVALID TELEMETRY FRAME(S)")
        else:
            if line.startswith("[FAULT]:"):
                self.fault_marker_received.emit(line)
            health = parse_firmware_health(line)
            if health is not None or (line.startswith("[SYS STATUS]:") and "UID:" not in line):
                self.health_received.emit(health)
            self.log_received.emit(f"TEL >> FIRMWARE · {line}")

    def send_cmd(self, cmd: str):
        if self.ser and self.ser.is_open:
            # The firmware consumes one byte at a time and ignores line
            # endings.  Sending a terminator also works with terminal bridges
            # that buffer writes until a complete command line arrives.
            self.ser.write((cmd + "\r").encode('ascii'))
            self.ser.flush()
            if cmd == 'p':
                self.last_time = time.monotonic()
            return True
        return False

    def stop(self):
        self.running = False
        if self.ser and self.ser.is_open:
            try: self.ser.close()
            except Exception: pass


class DemoWorker(QThread):
    """Hardware-free data source used for demos, UI testing, and recording."""

    data_received = pyqtSignal(float, float, float, float, float, float, float)
    log_received = pyqtSignal(str)
    fault_event = pyqtSignal(str)
    rejected_frame_received = pyqtSignal(str)
    accepted_frame_received = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.paused = False
        self.interval = 0.03
        self.stop_event = Event()
        self.started_at = time.monotonic()
        self.invalid_frames = 0
        self.checksum_failures = 0
        self._fault_lock = Lock()
        self._fault_kind = None
        self._fault_phase = None
        self._fault_started_at = 0.0
        self._fault_duration = 0.0
        self._random = random.Random()
        self._sequence = 0

    @property
    def fault_state(self):
        with self._fault_lock:
            return self._fault_kind, self._fault_phase

    def inject_fault(self, kind):
        if kind not in ("checksum", "mutex", "watchdog"):
            return False
        with self._fault_lock:
            if self._fault_kind is not None or not self.running:
                return False
            self._fault_kind = kind
            self._fault_phase = "pending"
            self._fault_started_at = time.monotonic()
            self._fault_duration = self._random.uniform(.28, .42) if kind == "mutex" else 0.0
        self.stop_event.set()
        return True

    def run(self):
        self.log_received.emit("SYS >> DEMO SOURCE ACTIVE · SIMULATED MPU6050")
        previous = time.monotonic()
        while self.running:
            now = time.monotonic()
            with self._fault_lock:
                kind, phase = self._fault_kind, self._fault_phase
                elapsed = now - self._fault_started_at
                if kind == "mutex" and phase == "visible" and elapsed >= self._fault_duration:
                    self._fault_kind = self._fault_phase = None
                    phase = "released"
                    self.fault_event.emit("mutex_released")
                    self.log_received.emit("SYS >> DEMO UART MUTEX RELEASED · TELEMETRY RESUMED")
                if kind == "watchdog" and phase == "stalled" and elapsed >= 1.8:
                    self._fault_phase = "rebooting"
                    phase = "rebooting"
                    self.fault_event.emit("watchdog_reset")
                    self.log_received.emit("WARN >> DEMO WATCHDOG EXPIRED · TELEMETRY TASK RESTARTING")
                elif kind == "watchdog" and phase == "rebooting" and elapsed >= 3.0:
                    self._fault_kind = self._fault_phase = None
                    self.started_at = now
                    kind = phase = None
                    self.fault_event.emit("watchdog_recovered")
                    self.log_received.emit("SYS >> DEMO BOOT COMPLETE · RESET REASON IWDG · TELEMETRY RESUMED")
                elif phase == "pending":
                    self._fault_phase = "stalled" if kind == "watchdog" else "visible"
                    phase = self._fault_phase
                    self.fault_event.emit(f"{kind}_detected")
                    if kind == "mutex":
                        self.log_received.emit("WARN >> DEMO UART MUTEX HELD · TELEMETRY WAITS")
                    elif kind == "watchdog":
                        self.log_received.emit("WARN >> DEMO TELEMETRY TASK STALLED · WATCHDOG NOT REFRESHED")
            if not self.paused and kind == "checksum" and phase == "visible":
                # Exercise the same checksum validator as the serial source.
                sample = self._next_sample(now - self.started_at)
                original = encode_telemetry(sample)
                payload, _, checksum = original.partition("|CHK:")
                digit = next(index for index in range(3, len(payload)) if payload[index].isdigit())
                damaged = f"{payload[:digit]}{chr(ord(payload[digit]) ^ 1)}{payload[digit + 1:]}|CHK:{checksum}"
                if parse_telemetry_line(damaged) is None and is_checksum_failure(damaged):
                    self.invalid_frames += 1
                    self.checksum_failures += 1
                    self.rejected_frame_received.emit(damaged)
                    self.fault_event.emit("checksum_rejected")
                    self.log_received.emit("WARN >> DEMO CHECKSUM MISMATCH · FRAME REJECTED BEFORE PLOTTING")
                with self._fault_lock:
                    if self._fault_kind == "checksum":
                        self._fault_phase = "shown"
            elif not self.paused and kind not in ("mutex", "watchdog"):
                sample = self._next_sample(now - self.started_at)
                valid_line = encode_telemetry(sample)
                accepted = parse_telemetry_line(valid_line)
                if accepted is not None:
                    if kind == "checksum" and phase == "shown":
                        self.accepted_frame_received.emit(valid_line)
                        with self._fault_lock:
                            self._fault_kind = self._fault_phase = None
                    self.data_received.emit(accepted.ax, accepted.ay, accepted.az,
                                            accepted.gx, accepted.gy, accepted.gz, now - previous)
            previous = now
            self.stop_event.wait(min(self.interval, 0.05) if kind == "watchdog" else self.interval)
            self.stop_event.clear()

    def _next_sample(self, t):
        base = self.sample_at(t)
        self._sequence += 1
        return TelemetrySample(
            base.ax + self._random.uniform(-.018, .018),
            base.ay + self._random.uniform(-.018, .018),
            base.az + self._random.uniform(-.018, .018),
            base.gx + self._random.uniform(-.25, .25),
            base.gy + self._random.uniform(-.25, .25),
            base.gz + self._random.uniform(-.25, .25),
            sequence=self._sequence,
        )

    @staticmethod
    def sample_at(t):
        """The prior smooth, continuous IMU demonstration source."""
        pitch = math.radians(15.0 * math.sin(0.60 * t))
        roll = math.radians(20.0 * math.sin(0.45 * t))
        pulse = 0.28 * math.sin(2.4 * t) if int(t) % 6 < 2 else 0.0
        return TelemetrySample(
            -math.sin(roll) * math.cos(pitch) + pulse,
            math.sin(pitch) + 0.12 * math.cos(1.8 * t),
            math.cos(roll) * math.cos(pitch),
            9.0 * math.cos(0.60 * t),
            9.0 * math.cos(0.45 * t),
            18.0,
        )

    def send_cmd(self, cmd: str):
        rates = {'v': 0.03, 'f': 0.5, 'n': 1.0, 's': 2.0}
        if cmd in rates:
            self.interval = rates[cmd]
            self.log_received.emit(f"SYS >> DEMO STREAM INTERVAL · {int(self.interval * 1000)} MS")
        elif cmd == 'p':
            self.paused = not self.paused
            self.log_received.emit(f"SYS >> DEMO TELEMETRY {'PAUSED' if self.paused else 'RESUMED'}")
        elif cmd in ('c', 'd'):
            self.log_received.emit(f"SYS >> DEMO MEMORY COMMAND · {cmd.upper()} · SIMULATED")

    def stop(self):
        self.running = False
        self.stop_event.set()


class ReplayWorker(QThread):
    """Emit recorded samples through the same signal contract as live sources."""

    data_received = pyqtSignal(float, float, float, float, float, float, float)
    log_received = pyqtSignal(str)
    progress_received = pyqtSignal(float, float)
    step_started = pyqtSignal()
    step_completed = pyqtSignal()

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.running = True
        self.paused = False
        self.speed = 1.0
        self.next_index = 0
        self._seek_generation = 0
        self.step_requested = False
        self._lock = Lock()
        self._wake_event = Event()

    @property
    def duration_s(self):
        return self.session.duration_s

    def send_cmd(self, cmd: str):
        if cmd == 'p':
            with self._lock:
                self.paused = not self.paused
            self.log_received.emit(f"SYS >> REPLAY {'PAUSED' if self.paused else 'RESUMED'}")
        self._wake_event.set()

    def set_speed(self, speed: float):
        with self._lock:
            self.speed = float(speed)
        self.log_received.emit(f"SYS >> REPLAY SPEED · {self.speed:g}×")
        self._wake_event.set()

    def set_next_index(self, index: int):
        with self._lock:
            self.next_index = max(0, min(index, len(self.session.samples)))
            self._seek_generation += 1
        self._wake_event.set()

    def set_paused(self, paused: bool):
        with self._lock:
            self.paused = paused
        self._wake_event.set()

    def request_step(self):
        with self._lock:
            self.paused = True
            self.step_requested = True
        self._wake_event.set()

    def run(self):
        self.log_received.emit(
            f"SYS >> REPLAY SOURCE ACTIVE · {len(self.session.samples)} SAMPLES · {self.duration_s:.1f} S"
        )
        previous_timestamp = None
        observed_seek_generation = 0
        while self.running:
            with self._lock:
                index = self.next_index
                paused = self.paused
                stepped = self.step_requested
                if observed_seek_generation != self._seek_generation:
                    previous_timestamp = None
                    observed_seek_generation = self._seek_generation
                if stepped:
                    self.step_requested = False
            if index >= len(self.session.samples):
                self.log_received.emit("SYS >> REPLAY COMPLETE")
                return
            if paused and not stepped:
                self._wake_event.wait(.05)
                self._wake_event.clear()
                continue

            sample = self.session.samples[index]
            if paused and stepped:
                self.step_started.emit()
            if not paused and previous_timestamp is not None:
                with self._lock:
                    speed = self.speed
                delay = max(0.0, sample.timestamp_s - previous_timestamp) / max(speed, .1)
                interrupted = self._wake_event.wait(delay)
                self._wake_event.clear()
                if not self.running:
                    return
                if interrupted:
                    continue
            with self._lock:
                # A seek can arrive between observing the index and emitting;
                # discard this stale frame so the UI never jumps backward.
                if index != self.next_index:
                    continue
                self.next_index += 1
            self.progress_received.emit(sample.timestamp_s, self.duration_s)
            self.data_received.emit(sample.ax_g, sample.ay_g, sample.az_g,
                                    sample.gx_dps, sample.gy_dps, sample.gz_dps,
                                    sample.received_dt_s)

            if paused:
                if stepped:
                    self.step_completed.emit()
                continue
            previous_timestamp = sample.timestamp_s

    def stop(self):
        self.running = False
        self._wake_event.set()
