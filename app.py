import sys, time, math, serial, html
from bisect import bisect_left, bisect_right
from threading import Event, Lock
from collections import deque
import serial.tools.list_ports
import numpy as np
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QComboBox, QTextEdit, 
                             QGridLayout, QSplitter, QLabel, QFrame,
                             QFileDialog, QMessageBox, QToolButton, QSlider)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from stl import mesh as stl_mesh

from kinematics import AttitudeEstimator
from dashboard_ui import STYLE, Panel, CollapsibleSection, FocusOverlay, RailScrollArea
from motion_events import MotionEvents
from telemetry import TelemetrySample, parse_telemetry_line
from telemetry import is_checksum_failure, parse_firmware_health
from session_io import Session, SessionRecorder, SessionSample, load_session
from calibration import CalibrationWizard, load_profile, save_profile
from calibration_ui import CalibrationDialog

# --- BACKGROUND SERIAL WORKER ---
class SerialWorker(QThread):
    data_received = pyqtSignal(float, float, float, float, float, float, float)
    log_received = pyqtSignal(str)
    sequence_received = pyqtSignal(object)
    health_received = pyqtSignal(object)
    
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
        sample = parse_telemetry_line(line)
        if sample is None:
            # If an unterminated prefix is followed by a complete telemetry
            # packet, retain the final packet instead of rejecting both.
            final_packet_start = line.rfind("AX:")
            if final_packet_start > 0:
                sample = parse_telemetry_line(line[final_packet_start:])
                if sample is not None:
                    self.recovered_frames += 1
        if sample is not None:
            self.synchronized = True
            now = time.monotonic()
            dt = now - self.last_time if self.last_time else 0.03
            self.last_time = now
            self.sequence_received.emit(sample.sequence)
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
            self.invalid_frame_examples.append(line)
            if self.invalid_frames == 1 or self.invalid_frames % 50 == 0:
                self.log_received.emit(f"WARN >> REJECTED {self.invalid_frames} INVALID TELEMETRY FRAME(S)")
        else:
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

    def stop(self):
        self.running = False
        if self.ser and self.ser.is_open:
            try: self.ser.close()
            except Exception: pass


class DemoWorker(QThread):
    """Hardware-free data source used for demos, UI testing, and recording."""

    data_received = pyqtSignal(float, float, float, float, float, float, float)
    log_received = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.paused = False
        self.interval = 0.03
        self.stop_event = Event()
        self.started_at = time.monotonic()

    def run(self):
        self.log_received.emit("SYS >> DEMO SOURCE ACTIVE · SIMULATED MPU6050")
        previous = time.monotonic()
        while self.running:
            now = time.monotonic()
            if not self.paused:
                sample = self.sample_at(now - self.started_at)
                self.data_received.emit(sample.ax, sample.ay, sample.az,
                                        sample.gx, sample.gy, sample.gz, now - previous)
            previous = now
            self.stop_event.wait(self.interval)

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

# --- PROCEDURAL 3D MESH BUILDERS ---
def create_mesh(verts, faces, color):
    face_colors = np.tile(np.array(color, dtype=np.float32), (len(faces), 1))
    return gl.GLMeshItem(vertexes=np.array(verts, dtype=np.float32), faces=np.array(faces), faceColors=face_colors, shader='shaded', drawEdges=True, edgeColor=(1,1,1,1))


def create_stl_mesh(path):
    cad_mesh = stl_mesh.Mesh.from_file(path)
    triangles = np.asarray(cad_mesh.vectors, dtype=np.float32)
    vertices = triangles.reshape(-1, 3)
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    vertices = vertices - center
    faces = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3)
    return create_mesh(vertices, faces, (0.25, 0.75, 1.0, 0.95))

class ColoredMesh:
    """Small triangle builder so model components have recognizable colors."""

    def __init__(self):
        self.triangles = []
        self.colors = []

    def quad(self, a, b, c, d, color):
        self.triangles.extend(((a, b, c), (a, c, d)))
        self.colors.extend((color, color))

    def box(self, x0, x1, y0, y1, z0, z1, color):
        points = ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
        for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                     (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            self.quad(*(points[index] for index in face), color)

    def disc(self, x, y, z, radius, color, segments=12):
        for index in range(segments):
            first = 2 * math.pi * index / segments
            second = 2 * math.pi * (index + 1) / segments
            self.triangles.append(((x, y, z),
                                   (x + radius * math.cos(first), y + radius * math.sin(first), z),
                                   (x + radius * math.cos(second), y + radius * math.sin(second), z)))
            self.colors.append(color)

    def item(self):
        vertices = np.asarray(self.triangles, dtype=np.float32).reshape(-1, 3)
        faces = np.arange(len(vertices), dtype=np.int32).reshape(-1, 3)
        return gl.GLMeshItem(vertexes=vertices, faces=faces,
                             faceColors=np.asarray(self.colors, dtype=np.float32),
                             shader='shaded', drawEdges=True, edgeColor=(.14, .23, .32, .55))

def create_satellite_mesh():
    model = ColoredMesh()
    silver = (.68, .75, .79, 1)
    gold = (.82, .62, .24, 1)
    blue = (.09, .27, .58, 1)
    model.box(-1.4, 1.4, -1.1, 1.1, -1.15, 1.15, gold)  # equipment bus
    model.box(-1.1, 1.1, -1.14, -1.1, -.85, .85, silver)
    model.box(-1.65, -1.4, -.32, .32, -.2, .2, silver)
    model.box(1.4, 1.65, -.32, .32, -.2, .2, silver)
    for side in (-1, 1):
        for panel in range(2):
            inner = 1.65 + panel * 2.9
            outer = inner + 2.75
            x0, x1 = (inner, outer) if side > 0 else (-outer, -inner)
            model.box(x0, x1, -1.2, 1.2, -.12, .12, blue)
            # Pale cell grid makes the wings read as photovoltaic panels.
            for step in range(1, 4):
                x = x0 + (x1 - x0) * step / 4
                model.box(x - .025, x + .025, -1.2, 1.2, .13, .15, silver)
            model.box(x0, x1, -.025, .025, .13, .15, silver)
    model.box(-.08, .08, -.08, .08, 1.15, 2.6, silver)  # antenna mast
    model.disc(0, 0, 2.65, .55, silver)
    model.box(-.16, .16, 1.1, 2.35, -.16, .16, silver)  # forward sensor boom
    model.disc(0, 2.35, .17, .48, (.78, .84, .88, 1))
    return model.item()


def create_drone_mesh():
    model = ColoredMesh()
    shell = (.22, .34, .41, 1)
    arm = (.52, .62, .68, 1)
    rotor = (.08, .72, .83, .88)
    model.box(-1.3, 1.3, -1.05, 1.05, -.55, .55, shell)
    model.box(-.8, .8, .55, 1.15, .55, .68, (.96, .45, .20, 1))  # forward marker
    for x in (-3.3, 3.3):
        for y in (-3.0, 3.0):
            x0, x1 = sorted((0, x))
            y0, y1 = sorted((0, y))
            model.box(x0, x1, y - .14, y + .14, -.12, .14, arm)
            model.box(x - .14, x + .14, y0, y1, -.12, .14, arm)
            model.box(x - .45, x + .45, y - .45, y + .45, .05, .4, shell)
            model.disc(x, y, .48, 1.12, rotor)
            model.disc(x, y, .5, .18, shell)
    for x in (-.9, .9):
        model.box(x - .08, x + .08, -1.4, 1.4, -1.55, -.5, arm)
        model.box(x - .22, x + .22, -1.5, 1.5, -1.6, -1.48, arm)
    return model.item()

def create_aircraft_mesh():
    verts = [[0.0,5.0,0.0],[-1.0,-2.0,-0.5],[1.0,-2.0,-0.5],[0.0,-2.0,1.0],[-7.0,-2.0,0.0],[7.0,-2.0,0.0],[0.0,-5.0,3.0],[0.0,-5.0,0.0]]
    faces = [[0,1,2],[0,2,3],[0,3,1],[0,1,4],[0,2,5],[3,6,7]]
    return create_mesh(verts, faces, (0.9, 0.2, 0.2, 0.95))



class TelemetryDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Telemetry Console")
        self.resize(1440, 900)
        self.setMinimumSize(1060, 730)
        self.setStyleSheet(STYLE)
        self.worker = None
        self.max_points = 300
        self.button_map = {}
        self.estimator = AttitudeEstimator()
        self.events = MotionEvents()
        self.active_mesh = None
        self.last_model_index = 0
        self.data = {axis: deque(maxlen=self.max_points) for axis in ('ax', 'ay', 'az', 'gx', 'gy', 'gz')}
        self.plot_times = deque(maxlen=self.max_points)
        self.sample_timestamps = deque(maxlen=120)
        self.serial_interval_samples = deque(maxlen=120)
        self.frame_timestamps = deque(maxlen=120)
        self.latest_motion = None
        self.latest_net_g = self.peak_g = 0.0
        self.pitch = self.roll = self.yaw = 0.0
        self.is_demo_mode = self.is_replay_mode = self.paused = False
        self.session_recorder = None
        self.loaded_session = None
        self.replay_position_s = 0.0
        self.replay_seeking = False
        self.replay_step_in_flight = False
        self.requested_interval = 0.03
        self.session_started = self.last_sample_at = None
        self.last_plot_render = self.last_stats_render = 0.0
        self.recent_events = deque(maxlen=3)
        self.terminal_records = deque(maxlen=1000)
        self.terminal_filter_buttons = {}
        self.terminal_visible_categories = {'SYS', 'TEL', 'EVT', 'WARN'}
        self.last_terminal_telemetry_at = 0.0
        self.terminal_rate_options = ((1.0, "1000 ms"), (2.0, "500 ms"), (4.0, "250 ms"),
                                      (10.0, "100 ms"), (33.0, "30 ms"))
        self.terminal_rate_interval = 1.0
        self.terminal_following = True
        self._terminal_rendering = False
        self.calibration_dialog = None
        self.calibration_wizard = CalibrationWizard()
        self.current_profile = None
        self.firmware_health = None
        self.serial_packets_seen = 0
        self.sequenced_frames = 0
        self.missing_packets = 0
        self.last_sequence = None
        self.init_ui()
        self.shortcuts = []
        for key, button in self.button_map.items():
            shortcut = QShortcut(QKeySequence(key), self)
            if key == 'p':
                shortcut.activated.connect(self.toggle_pause_shortcut)
            else:
                shortcut.activated.connect(button.click)
            self.shortcuts.append(shortcut)
        escape = QShortcut(QKeySequence("Escape"), self)
        escape.activated.connect(self.focus_overlay.restore)
        self.shortcuts.append(escape)
        self.render_timer = QTimer(self)
        self.render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.render_timer.setInterval(16)
        self.render_timer.timeout.connect(self.render_frame)
        self.render_timer.start()

    @staticmethod
    def label(text, name=None):
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        return label

    def init_ui(self):
        root = QWidget()
        root.setObjectName("console")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(14, 10, 14, 10)
        bar.addWidget(self.label("STM32  Telemetry Console", "brand"))
        bar.addStretch()
        self.btn_calibrate = QPushButton("Calibrate")
        self.btn_calibrate.setToolTip("Guided sensor calibration and live device diagnostics")
        self.btn_calibrate.clicked.connect(self.open_calibration)
        bar.addWidget(self.btn_calibrate)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(200)
        self.refresh_ports()
        bar.addWidget(self.port_combo)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.clicked.connect(self.toggle_connection)
        bar.addWidget(self.btn_connect)
        self.lbl_status = self.label("OFFLINE", "section")
        self.lbl_status.setMinimumWidth(130)
        bar.addWidget(self.lbl_status)
        layout.addWidget(toolbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(8)
        self.rail_scroll = RailScrollArea()
        rail_scroll = self.rail_scroll
        rail_scroll.setObjectName("railScroll")
        rail_scroll.setWidgetResizable(True)
        rail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail_scroll.setMinimumWidth(210)
        rail_scroll.setMaximumWidth(300)
        rail = QFrame()
        rail.setObjectName("rail")
        rail_scroll.setWidget(rail)
        controls = QVBoxLayout(rail)
        controls.setContentsMargins(10, 10, 9, 10)
        controls.setSpacing(4)
        self.rail_sections = {}

        def section_layout(title, expanded=False):
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(2, 1, 2, 4)
            content_layout.setSpacing(5)
            pane = CollapsibleSection(title, content, expanded)
            self.rail_sections[title] = pane
            controls.addWidget(pane)
            return content_layout

        stream_controls = section_layout("STREAM CONTROL", True)
        rates = QGridLayout()
        self.rate_buttons = {}
        definitions = [
            ('v', "Live · 33 Hz", "30 ms interval"),
            ('f', "Fast · 2 Hz", "500 ms interval"),
            ('n', "Normal · 1 Hz", "1000 ms interval"),
            ('s', "Slow · 0.5 Hz", "2000 ms interval"),
        ]
        for i, (key, title, tooltip) in enumerate(definitions):
            button = QPushButton(title + "  [" + key.upper() + "]")
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda checked=False, cmd=key: self.send_command(cmd))
            rates.addWidget(button, i, 0)
            self.rate_buttons[key] = button
            self.button_map[key] = button
        self.btn_vis, self.btn_fast, self.btn_norm, self.btn_slow = [self.rate_buttons[k] for k in 'vfns']
        self.btn_vis.setChecked(True)
        stream_controls.addLayout(rates)
        self.btn_pause = QPushButton("Pause telemetry  [P]")
        self.btn_pause.clicked.connect(lambda: self.send_command('p'))
        self.button_map['p'] = self.btn_pause
        stream_controls.addWidget(self.btn_pause)
        session_controls = section_layout("SESSION CAPTURE + REPLAY", True)
        self.btn_record = QPushButton("Start recording")
        self.btn_record.setObjectName("primary")
        self.btn_record.setToolTip("Record validated live or demo telemetry to a CSV/JSON session pair.")
        self.btn_record.clicked.connect(self.toggle_recording)
        session_controls.addWidget(self.btn_record)
        self.btn_open_session = QPushButton("Open session for replay…")
        self.btn_open_session.setToolTip("Load a saved CSV or JSON session without connecting hardware.")
        self.btn_open_session.clicked.connect(self.open_session)
        session_controls.addWidget(self.btn_open_session)
        playback_row = QHBoxLayout()
        playback_row.setSpacing(5)
        self.btn_play_replay = QPushButton("Play  ▶")
        self.btn_play_replay.setObjectName("primary")
        self.btn_play_replay.setEnabled(False)
        self.btn_play_replay.setToolTip("Play or pause this recording (P).")
        self.btn_play_replay.clicked.connect(self.toggle_replay_playback)
        playback_row.addWidget(self.btn_play_replay, 1)
        self.btn_restart_replay = QPushButton("Restart  ↺")
        self.btn_restart_replay.setEnabled(False)
        self.btn_restart_replay.clicked.connect(self.restart_replay)
        playback_row.addWidget(self.btn_restart_replay)
        session_controls.addLayout(playback_row)
        self.lbl_replay_time = self.label("No replay loaded", "muted")
        self.lbl_replay_time.setWordWrap(True)
        session_controls.addWidget(self.lbl_replay_time)
        self.replay_slider = QSlider(Qt.Orientation.Horizontal)
        self.replay_slider.setRange(0, 1000)
        self.replay_slider.setEnabled(False)
        self.replay_slider.setToolTip("Drag and release to seek. Replay processing is rebuilt through the selected point.")
        self.replay_slider.sliderPressed.connect(self.begin_replay_seek)
        self.replay_slider.sliderMoved.connect(self.preview_replay_seek)
        self.replay_slider.sliderReleased.connect(self.commit_replay_seek)
        session_controls.addWidget(self.replay_slider)
        speed_row = QHBoxLayout()
        speed_row.addWidget(self.label("PLAYBACK SPEED", "muted"))
        speed_row.addStretch()
        self.lbl_replay_speed = self.label("1×", "healthValue")
        speed_row.addWidget(self.lbl_replay_speed)
        session_controls.addLayout(speed_row)
        self.replay_speeds = (.25, .5, 1., 2., 4.)
        self.replay_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.replay_speed_slider.setRange(0, len(self.replay_speeds) - 1)
        self.replay_speed_slider.setValue(2)
        self.replay_speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.replay_speed_slider.setTickInterval(1)
        self.replay_speed_slider.setPageStep(1)
        self.replay_speed_slider.setEnabled(False)
        self.replay_speed_slider.setToolTip("Snap between 0.25×, 0.5×, 1×, 2×, and 4× playback.")
        self.replay_speed_slider.valueChanged.connect(self.set_replay_speed)
        session_controls.addWidget(self.replay_speed_slider)
        speed_labels = QHBoxLayout()
        speed_labels.addWidget(self.label("0.25×", "muted"))
        speed_labels.addStretch()
        speed_labels.addWidget(self.label("1×", "muted"))
        speed_labels.addStretch()
        speed_labels.addWidget(self.label("4×", "muted"))
        session_controls.addLayout(speed_labels)
        self.btn_step = QPushButton("Step one frame  ▸|")
        self.btn_step.setEnabled(False)
        self.btn_step.setToolTip("Pause replay and advance one recorded sample.")
        self.btn_step.clicked.connect(self.step_replay)
        session_controls.addWidget(self.btn_step)
        attitude_controls = section_layout("3D ATTITUDE")
        self.model_combo = QComboBox()
        self.model_combo.addItems(["Jet Aircraft", "Orbital Satellite", "Quadcopter Drone", "Load Custom STL…"])
        self.model_combo.setCurrentIndex(0)
        self.model_combo.currentIndexChanged.connect(self.change_3d_model)
        attitude_controls.addWidget(self.model_combo)
        self.btn_reset_yaw = QPushButton("Reset attitude  [R]")
        self.btn_reset_yaw.clicked.connect(self.reset_yaw)
        self.button_map['r'] = self.btn_reset_yaw
        attitude_controls.addWidget(self.btn_reset_yaw)
        memory_controls = section_layout("MEMORY TOOLS")
        self.btn_dump = QPushButton("Export memory snapshot  [D]")
        self.btn_dump.clicked.connect(lambda: self.send_command('d'))
        self.btn_clear = QPushButton("Erase flash sector 5  [C]")
        self.btn_clear.setObjectName("danger")
        self.btn_clear.clicked.connect(lambda: self.send_command('c'))
        self.button_map.update({'d': self.btn_dump, 'c': self.btn_clear})
        memory_controls.addWidget(self.btn_dump)
        memory_controls.addWidget(self.btn_clear)
        terminal_controls = section_layout("FIRMWARE TERMINAL")
        terminal_rate_row = QHBoxLayout()
        terminal_rate_row.addWidget(self.label("OUTPUT RATE", "muted"))
        self.lbl_terminal_rate = self.label("1000 ms", "healthValue")
        terminal_rate_row.addStretch()
        terminal_rate_row.addWidget(self.lbl_terminal_rate)
        terminal_controls.addLayout(terminal_rate_row)
        self.terminal_rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.terminal_rate_slider.setRange(0, len(self.terminal_rate_options) - 1)
        self.terminal_rate_slider.setValue(0)
        self.terminal_rate_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.terminal_rate_slider.setTickInterval(1)
        self.terminal_rate_slider.setToolTip("Choose how often TEL summaries are printed. Sensor capture remains full rate.")
        self.terminal_rate_slider.valueChanged.connect(self.set_terminal_rate)
        terminal_controls.addWidget(self.terminal_rate_slider)
        self.btn_timestamps = QPushButton("Show timestamps")
        self.btn_timestamps.setObjectName("terminalFilter")
        self.btn_timestamps.setCheckable(True)
        self.btn_timestamps.setChecked(True)
        self.btn_timestamps.setToolTip("Show the local receive time before each terminal prefix.")
        self.btn_timestamps.toggled.connect(lambda checked: self.render_terminal(force=True))
        terminal_controls.addWidget(self.btn_timestamps)
        terminal_filters = [
            ('SYS', 'SYS · System & commands', 'Connection state and commands sent to the device.'),
            ('TEL', 'TEL · Sensor frames', 'Periodic sensor frames and firmware telemetry.'),
            ('EVT', 'EVT · Motion events', 'Detected motion and impact events.'),
            ('WARN', 'WARN · Issues', 'Rejected frames and communication problems.'),
        ]
        for category, title, tooltip in terminal_filters:
            button = QPushButton(title)
            button.setObjectName("terminalFilter")
            button.setCheckable(True)
            button.setChecked(True)
            button.setToolTip(tooltip)
            button.toggled.connect(lambda checked, kind=category: self.set_terminal_filter(kind, checked))
            self.terminal_filter_buttons[category] = button
            terminal_controls.addWidget(button)
        controls.addStretch()
        rail_scroll.enable_wheel_navigation()
        body.addWidget(rail_scroll)

        workspace = QWidget()
        work = QVBoxLayout(workspace)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(10)
        metrics = QHBoxLayout()
        self.lbl_connection = self.label("Offline", "value")
        self.lbl_stream_rate = self.label("— / — Hz", "value")
        self.lbl_current_g = self.label("— g", "value")
        self.lbl_max_g = self.label("— g", "value")
        for title, value in [("CONNECTION", self.lbl_connection), ("STREAM / RENDER", self.lbl_stream_rate),
                             ("MEASURED |a|", self.lbl_current_g), ("SESSION PEAK", self.lbl_max_g)]:
            frame = QFrame()
            frame.setObjectName("metric")
            box = QVBoxLayout(frame)
            box.setContentsMargins(12, 9, 12, 9)
            box.setSpacing(4)
            box.addWidget(self.label(title, "section"))
            box.addWidget(value)
            metrics.addWidget(frame)
        self.lbl_current_g.setToolTip("Magnitude of raw accelerometer data, including gravity. At rest: approximately 1 g.")
        work.addLayout(metrics)

        # Both plots and the attitude view stay live on the same screen.
        stack = QSplitter(Qt.Orientation.Vertical)
        stack.setChildrenCollapsible(False)
        stack.setHandleWidth(8)
        hero = QSplitter(Qt.Orientation.Horizontal)
        hero.setChildrenCollapsible(False)
        hero.setHandleWidth(8)
        model_body = QWidget()
        model_layout = QVBoxLayout(model_body)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(5)
        self.view_3d = gl.GLViewWidget()
        self.view_3d.setMinimumSize(280, 160)
        self.view_3d.setCameraPosition(distance=25, elevation=30, azimuth=45)
        self.view_3d.setBackgroundColor('#101923')
        grid = gl.GLGridItem()
        grid.setSize(x=50, y=50, z=0)
        grid.setSpacing(x=5, y=5, z=0)
        grid.setColor((65, 92, 111, 120))
        grid.translate(0, 0, -4)
        self.view_3d.addItem(grid)
        self.triad = gl.GLAxisItem()
        self.triad.setSize(x=6, y=6, z=6)
        self.view_3d.addItem(self.triad)
        self.force_vector = gl.GLLinePlotItem(pos=np.zeros((2, 3)), color=(0.0, 1.0, 1.0, 1.0), width=3, antialias=True)
        self.view_3d.addItem(self.force_vector)
        model_layout.addWidget(self.view_3d, 1)
        self.lbl_angles = self.label("Pitch  0.0°     Roll  0.0°     Yaw  0.0°", "muted")
        self.lbl_angles.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_angles.setToolTip("Drag to orbit the camera; wheel to zoom. Cyan vector: estimated linear acceleration.")
        model_layout.addWidget(self.lbl_angles)
        self.model_panel = Panel("3D Attitude", model_body, self.expand_panel)
        hero.addWidget(self.model_panel)

        event_body = QWidget()
        event_layout = QVBoxLayout(event_body)
        event_layout.setContentsMargins(2, 2, 2, 2)
        event_layout.setSpacing(9)
        self.lbl_motion_state = self.label("Waiting for data", "motionState")
        self.lbl_motion_state.setWordWrap(True)
        event_layout.addWidget(self.lbl_motion_state)
        self.lbl_event_peak = self.label("—")
        self.lbl_event_axis = self.label("—")
        self.lbl_rotation = self.label("— °/s")
        for name, value in [("Latest event peak", self.lbl_event_peak),
                             ("Transient axis", self.lbl_event_axis), ("Rotation", self.lbl_rotation)]:
            row = QHBoxLayout()
            row.addWidget(self.label(name, "muted"))
            row.addStretch()
            row.addWidget(value)
            event_layout.addLayout(row)
        event_layout.addWidget(self.label("RECENT EVENTS", "section"))
        self.lbl_recent = self.label("No events recorded", "muted")
        self.lbl_recent.setWordWrap(True)
        event_layout.addWidget(self.lbl_recent)
        event_layout.addStretch()
        self.lbl_event_note = self.label("Motion labels use measured sensor activity", "muted")
        self.lbl_event_note.setWordWrap(True)
        self.lbl_event_note.setToolTip("Impact: ≥2 g. Motion labels are simple heuristics. Slow stream rates can miss brief events.")
        event_layout.addWidget(self.lbl_event_note)
        self.event_panel = Panel("Motion Events", event_body)
        self.event_panel.setMinimumWidth(250)
        hero.addWidget(self.event_panel)
        hero.setStretchFactor(0, 3)
        hero.setStretchFactor(1, 1)
        hero.setSizes([710, 300])
        stack.addWidget(hero)

        plots = QSplitter(Qt.Orientation.Horizontal)
        plots.setChildrenCollapsible(False)
        plots.setHandleWidth(8)
        pg.setConfigOptions(antialias=False)
        self.accel_graph = self.make_graph("Acceleration", "g", (-3, 3))
        self.gyro_graph = self.make_graph("Angular velocity", "°/s", (-300, 300))
        colors = ('#00E5FF', '#E040FB', '#FFEA00')
        self.accel_curves = [self.accel_graph.plot(pen=pg.mkPen(c, width=2), name=n) for c, n in zip(colors, "XYZ")]
        self.gyro_curves = [self.gyro_graph.plot(pen=pg.mkPen(c, width=2), name=n) for c, n in zip(colors, "XYZ")]
        self.curve_ax, self.curve_ay, self.curve_az = self.accel_curves
        self.curve_gx, self.curve_gy, self.curve_gz = self.gyro_curves
        self.accel_panel = Panel("Acceleration", self.accel_graph, self.expand_panel)
        self.gyro_panel = Panel("Angular Velocity", self.gyro_graph, self.expand_panel)
        plots.addWidget(self.accel_panel)
        plots.addWidget(self.gyro_panel)
        plots.setSizes([500, 500])
        stack.addWidget(plots)

        terminal_body = QWidget()
        terminal_layout = QHBoxLayout(terminal_body)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.setSpacing(8)
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.document().setMaximumBlockCount(1000)
        self.terminal.setMinimumHeight(45)
        self.terminal.setToolTip("Firmware messages are collected continuously. Use the category controls in the left rail to filter this view.")
        terminal_layout.addWidget(self.terminal, 1)
        self.terminal.verticalScrollBar().valueChanged.connect(self.handle_terminal_scroll)
        self.terminal.viewport().installEventFilter(self)
        self.btn_resume_terminal = QPushButton("Resume live  ↓", self.terminal.viewport())
        self.btn_resume_terminal.setObjectName("resumeLive")
        self.btn_resume_terminal.setToolTip("Return to the newest firmware output")
        self.btn_resume_terminal.clicked.connect(self.resume_terminal_live)
        self.btn_resume_terminal.hide()

        self.health_panel = QFrame()
        self.health_panel.setObjectName("healthPanel")
        self.health_panel.setMinimumWidth(190)
        self.health_panel.setMaximumWidth(240)
        health_layout = QVBoxLayout(self.health_panel)
        health_layout.setContentsMargins(10, 8, 10, 8)
        health_layout.setSpacing(5)
        self.btn_health = QToolButton()
        self.btn_health.setObjectName("healthToggle")
        self.btn_health.setText("Session Health  ▾")
        self.btn_health.setToolTip("Show or hide the Session Health panel")
        self.btn_health.setCheckable(True)
        self.btn_health.setChecked(True)
        self.btn_health.toggled.connect(self.toggle_session_health)
        self.health_details = QWidget()
        health_details_layout = QVBoxLayout(self.health_details)
        health_details_layout.setContentsMargins(0, 2, 0, 0)
        health_details_layout.setSpacing(2)
        self.lbl_health_source = self.label("—", "healthValue")
        self.lbl_health_last_frame = self.label("—", "healthValue")
        self.lbl_health_invalid = self.label("0", "healthValue")
        self.lbl_health_status = self.label("OFFLINE", "healthValue")
        for name, value in [("SOURCE", self.lbl_health_source), ("LAST FRAME", self.lbl_health_last_frame),
                            ("INVALID FRAMES", self.lbl_health_invalid), ("FIRMWARE", self.lbl_health_status)]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 3, 0, 3)
            row.addWidget(self.label(name, "muted"))
            row.addStretch()
            row.addWidget(value)
            health_details_layout.addLayout(row)
        health_layout.addWidget(self.health_details)
        health_layout.addStretch()
        terminal_layout.addWidget(self.health_panel)
        self.terminal_panel = Panel("Firmware Terminal", terminal_body, self.expand_panel, header_action=self.btn_health)
        stack.addWidget(self.terminal_panel)
        stack.setSizes([320, 270, 95])
        stack.setStretchFactor(0, 4)
        stack.setStretchFactor(1, 3)
        stack.setStretchFactor(2, 1)
        work.addWidget(stack, 1)
        body.addWidget(workspace)
        body.setStretchFactor(1, 1)
        body.setSizes([245, 1170])
        layout.addWidget(body, 1)
        self.focus_overlay = FocusOverlay(root)
        self.change_3d_model(0)
        self.set_stream_controls(False)
        self.set_session_controls()
        self.log_message("SYS >> READY · SELECT A SERIAL PORT OR DEMO, THEN CONNECT")

    def make_graph(self, label, units, yrange):
        graph = pg.PlotWidget(background='#101720')
        graph.setMinimumSize(240, 170)
        graph.addLegend(offset=(12, 5))
        graph.setLabel('left', label, units=units)
        graph.setLabel('bottom', 'Session time', units='s')
        graph.showGrid(x=True, y=True, alpha=0.12)
        graph.setYRange(*yrange)
        graph.enableAutoRange(x=True, y=False)
        for axis in ('left', 'bottom'):
            graph.getAxis(axis).setPen('#536779')
            graph.getAxis(axis).setTextPen('#a9bbcb')
        return graph

    def expand_panel(self, panel):
        self.focus_overlay.expand(panel)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'focus_overlay'):
            self.focus_overlay.setGeometry(self.centralWidget().rect())
        if hasattr(self, 'btn_resume_terminal'):
            self.position_resume_terminal_button()

    def eventFilter(self, watched, event):
        if hasattr(self, 'terminal') and watched is self.terminal.viewport() and event.type() == QEvent.Type.Resize:
            self.position_resume_terminal_button()
        return super().eventFilter(watched, event)

    def position_resume_terminal_button(self):
        button = self.btn_resume_terminal
        viewport = self.terminal.viewport()
        button.adjustSize()
        button.move((viewport.width() - button.width()) // 2, max(8, viewport.height() - button.height() - 12))
        button.raise_()

    def set_stream_controls(self, enabled):
        for key in 'vfnspdc':
            self.button_map[key].setEnabled(enabled and not self.is_replay_mode)
        self.port_combo.setEnabled(not enabled)

    def set_session_controls(self):
        active = self.worker is not None
        live_source = active and not self.is_replay_mode
        recording = self.session_recorder is not None
        self.btn_record.setEnabled(live_source)
        self.btn_record.setText("Stop & save recording" if recording else "Start recording")
        self.btn_open_session.setEnabled(not active)
        self.btn_restart_replay.setEnabled(self.loaded_session is not None and (not active or self.is_replay_mode))
        replay_active = active and self.is_replay_mode
        self.btn_play_replay.setEnabled(replay_active)
        self.btn_play_replay.setText("Play  ▶" if self.paused or not replay_active else "Pause  ❚❚")
        self.replay_slider.setEnabled(replay_active)
        self.replay_speed_slider.setEnabled(replay_active)
        self.btn_step.setEnabled(replay_active)

    def change_3d_model(self, index):
        factories = [create_aircraft_mesh, create_satellite_mesh, create_drone_mesh]
        if index < 3:
            new_mesh = factories[index]()
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Load 3D Model", "", "STL Models (*.stl)")
            if not path:
                self.restore_model_selection()
                return
            try:
                new_mesh = create_stl_mesh(path)
            except Exception as error:
                QMessageBox.warning(self, "STL Load Failed", str(error))
                self.restore_model_selection()
                return
        if self.active_mesh is not None:
            self.view_3d.removeItem(self.active_mesh)
        self.active_mesh = new_mesh
        self.last_model_index = index
        self.view_3d.addItem(new_mesh)
        if self.latest_motion is not None:
            self.render_attitude_twin()
        self.view_3d.update()

    def restore_model_selection(self):
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(self.last_model_index)
        self.model_combo.blockSignals(False)

    def reset_yaw(self):
        self.estimator.reset()
        self.pitch = self.roll = self.yaw = 0.0
        # Reflect R immediately, even while paused or disconnected.
        self.latest_motion = None
        if self.active_mesh is not None:
            self.active_mesh.resetTransform()
        self.triad.resetTransform()
        self.force_vector.setData(pos=np.zeros((2, 3)))
        self.lbl_angles.setText("Pitch  0.0°     Roll  0.0°     Yaw  0.0°")
        self.log_message("SYS >> ORIENTATION ZEROED")

    def refresh_ports(self):
        self.port_combo.clear()
        self.port_combo.addItem("DEMO — No Hardware")
        self.port_combo.addItems([port.device for port in serial.tools.list_ports.comports()])

    def is_live_serial(self):
        return isinstance(self.worker, SerialWorker) and self.worker.isRunning()

    def open_calibration(self):
        if self.calibration_dialog is None:
            self.calibration_dialog = CalibrationDialog(self)
        self.calibration_dialog.refresh()
        self.calibration_dialog.show()
        self.calibration_dialog.raise_()
        self.calibration_dialog.activateWindow()

    def start_calibration(self):
        if not self.is_live_serial() or self.paused or self.session_recorder is not None:
            self.calibration_wizard.status = "Connect a live sensor, resume it, and stop recording first."
        else:
            if self.requested_interval > .05:
                self.send_command('v')
            self.calibration_wizard.start()
        if self.calibration_dialog is not None:
            self.calibration_dialog.refresh()

    def restart_calibration(self):
        self.calibration_wizard.cancel()
        self.start_calibration()

    def save_calibration(self):
        if not self.is_live_serial() or self.paused or self.session_recorder is not None:
            return
        key = (f"uid:{self.firmware_health.device_uid}" if self.firmware_health else
               f"port:{self.port_combo.currentText()}")
        try:
            profile = self.calibration_wizard.profile(key)
            save_profile(profile)
        except (ValueError, OSError) as error:
            self.calibration_wizard.status = str(error)
            if self.calibration_dialog is not None:
                self.calibration_dialog.refresh()
            return
        self.current_profile = profile
        self.calibration_wizard.cancel("Calibration saved and applied to live data.")
        self.reset_visual_session()
        self.log_message(f"SYS >> CALIBRATION APPLIED · {profile.id}")
        if self.calibration_dialog is not None:
            self.calibration_dialog.refresh()

    def observe_sequence(self, sequence):
        if sequence is None:
            return
        self.sequenced_frames += 1
        if self.last_sequence is not None:
            gap = (sequence - self.last_sequence) & 0xFFFFFFFF
            if 1 < gap < 10000:
                self.missing_packets += gap - 1
        self.last_sequence = sequence

    def receive_firmware_health(self, source, health):
        if self.worker is not source:
            return
        self.firmware_health = health
        key = f"uid:{health.device_uid}" if health else f"port:{self.port_combo.currentText()}"
        profile = load_profile(key)
        if profile != self.current_profile:
            self.current_profile = profile
            self.reset_visual_session()
            self.log_message(f"SYS >> CALIBRATION {'LOADED · ' + profile.id if profile else 'NOT SAVED FOR THIS DEVICE'}")

    def device_health_snapshot(self):
        live = self.is_live_serial()
        now = time.monotonic()
        worker = self.worker if live else None
        timestamps = [stamp for stamp in self.sample_timestamps if now - stamp <= 5.0] if live else []
        intervals = [b - a for a, b in zip(timestamps, timestamps[1:])]
        rate = (len(intervals) / (timestamps[-1] - timestamps[0])) if len(timestamps) > 1 else 0.0
        jitter = sorted(abs(interval - self.requested_interval) * 1000
                        for interval in self.serial_interval_samples)
        p95 = jitter[min(len(jitter) - 1, math.ceil(.95 * len(jitter)) - 1)] if jitter else None
        expected = self.sequenced_frames + self.missing_packets
        loss = self.missing_packets / expected * 100 if expected else None
        firmware = self.firmware_health if live else None
        checksums = worker.checksum_failures if worker else 0
        fresh = bool(timestamps) and now - timestamps[-1] <= max(1.5, 3 * self.requested_interval)
        poor_link = bool(checksums or (loss is not None and loss > 1.0) or
                         (p95 is not None and p95 > max(15, self.requested_interval * 500)))
        low_stack = bool(firmware and min(firmware.telemetry_stack_words,
                                          firmware.status_stack_words) < 32)
        watchdog_reset = bool(firmware and firmware.reset_reason in ("IWDG", "WWDG"))
        state = ("Replay" if self.is_replay_mode and self.worker is not None else
                 "Demo" if self.is_demo_mode and self.worker is not None else
                 "Connecting" if self.worker is not None and not live else
                 "Offline" if not live else "Paused" if self.paused else
                 "Waiting for data" if not fresh else "Needs attention" if poor_link or low_stack or watchdog_reset else "Good")
        note = ("Connect a serial device for live diagnostics." if self.worker is None else
                "Diagnostics require a live serial sensor." if not live else
                "This firmware does not report sequence, reset, stack, or device ID yet." if firmware is None else
                "Link counters reset on reconnect. Stack figures are minimum free words since boot.")
        return {
            "state": state, "checksum": str(checksums) if live else "—",
            "loss": f"{loss:.2f}%" if loss is not None else "—",
            "jitter": f"{p95:.1f} ms" if p95 is not None else "—",
            "rate": f"{rate:.1f} frames/s" if live and fresh and not self.paused and rate else "—",
            "frames": str(self.serial_packets_seen) if live else "—",
            "missing": str(self.missing_packets) if live and self.sequenced_frames else "—",
            "malformed": str(worker.invalid_frames - worker.checksum_failures) if worker else "—",
            "reset": firmware.reset_reason if firmware else "—",
            "telemetry_stack": f"{firmware.telemetry_stack_words} words" if firmware else "—",
            "status_stack": f"{firmware.status_stack_words} words" if firmware else "—",
            "command_drop": str(firmware.command_drop) if firmware else "—",
            "identity": firmware.device_uid if firmware else "—",
            "note": note,
        }

    def reset_visual_session(self):
        """Reset derived dashboard state before switching data sources or seeking."""
        self.paused = False
        self.events = MotionEvents()
        self.recent_events.clear()
        self.latest_motion = None
        self.latest_net_g = self.peak_g = 0.0
        self.sample_timestamps.clear()
        self.serial_interval_samples.clear()
        self.plot_times.clear()
        self.frame_timestamps.clear()
        self.last_terminal_telemetry_at = 0.0
        for values in self.data.values():
            values.clear()
        for curve in self.accel_curves + self.gyro_curves:
            curve.clear()
        self.reset_yaw()
        self.session_started = time.monotonic()
        self.last_sample_at = None
        self.lbl_recent.setText("No events recorded")
        self.lbl_event_axis.setText("—")
        self.lbl_event_peak.setText("—")
        self.replay_position_s = 0.0

    def toggle_connection(self):
        if self.worker is not None:
            self.disconnect_source()
            return
        port = self.port_combo.currentText()
        self.is_demo_mode = port.startswith("DEMO")
        self.is_replay_mode = False
        self.requested_interval = 0.03
        self.reset_visual_session()
        self.calibration_wizard.cancel("Place the board on a stable surface to begin.")
        self.firmware_health = None
        self.serial_packets_seen = 0
        self.sequenced_frames = 0
        self.missing_packets = 0
        self.last_sequence = None
        self.current_profile = None
        self.btn_pause.setText("Pause telemetry  [P]")
        for key, button in self.rate_buttons.items():
            button.setChecked(key == 'v')
        worker = DemoWorker() if self.is_demo_mode else SerialWorker(port)
        self.worker = worker
        worker.data_received.connect(self.ingest_telemetry)
        worker.log_received.connect(self.log_message)
        if isinstance(worker, SerialWorker):
            worker.sequence_received.connect(self.observe_sequence)
            worker.health_received.connect(lambda health, source=worker: self.receive_firmware_health(source, health))
        worker.finished.connect(lambda source=worker: self.source_finished(source))
        self.set_stream_controls(True)
        self.set_session_controls()
        self.btn_connect.setText("Disconnect")
        self.lbl_status.setText("CONNECTING")
        worker.start()
        QTimer.singleShot(500, lambda source=worker: self.send_command(
            next(key for key, button in self.rate_buttons.items() if button.isChecked()))
            if self.worker is source and not self.paused else None)

    def toggle_recording(self):
        if self.session_recorder is not None:
            self.stop_recording()
            return
        if self.worker is None or self.is_replay_mode:
            return
        default_name = "telemetry_session.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save telemetry session", default_name, "Telemetry session (*.csv)"
        )
        if not path:
            return
        self.start_recording_at_path(path)

    def start_recording_at_path(self, path):
        """Start recording at a supplied path; shared by the dialog and desktop smoke test."""
        if self.session_recorder is not None or self.worker is None or self.is_replay_mode:
            return False
        if self.calibration_wizard.stage in ("gyro", "faces", "ready"):
            self.calibration_wizard.cancel("Recording started. Start calibration again afterward.")
        try:
            self.session_recorder = SessionRecorder(path, {
                "application_version": "telemetry-console-prototype",
                "source": "demo" if self.is_demo_mode else "serial",
                "serial_port": None if self.is_demo_mode else self.port_combo.currentText(),
                "requested_interval_ms": int(self.requested_interval * 1000),
                "firmware_version": "unknown",
                "calibration_id": self.current_profile.id if self.current_profile else "not-calibrated",
                "calibration_device_key": self.current_profile.device_key if self.current_profile else None,
                "processing_baseline": "attitude and motion-event state reset at recording start",
                "recording_policy": "validated samples; derived attitude and events included",
            })
        except OSError as error:
            QMessageBox.warning(self, "Recording Failed", str(error))
            return False
        # A recording is a self-contained replay session.  Starting it from a
        # known estimator/event state means reconstructed playback produces the
        # same state evolution rather than inheriting an unseen live history.
        self.reset_visual_session()
        self.log_message(f"SYS >> RECORDING STARTED · {self.session_recorder.csv_path.name}")
        self.set_session_controls()
        return True

    def stop_recording(self):
        recorder, self.session_recorder = self.session_recorder, None
        if recorder is None:
            return
        try:
            manifest = recorder.close({"source_end_reason": "user stopped recording"})
            self.log_message(f"SYS >> RECORDING SAVED · {recorder.sample_count} SAMPLES · {manifest.name}")
        except OSError as error:
            self.log_message(f"WARN >> RECORDING FINALIZE FAILED · {error}")
        self.set_session_controls()

    def open_session(self):
        if self.worker is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open telemetry session", "", "Telemetry session (*.csv *.json)"
        )
        if not path:
            return
        try:
            self.loaded_session = load_session(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Session Load Failed", str(error))
            return
        self.start_replay(self.loaded_session)

    def restart_replay(self):
        if isinstance(self.worker, ReplayWorker):
            self.worker.set_paused(True)
            self.paused = True
            self.rebuild_replay_to(0)
            self.worker.set_next_index(1)
            self.update_replay_progress(0.0, self.loaded_session.duration_s, force=True)
            self.worker.set_paused(False)
            self.paused = False
            self.set_session_controls()
        elif self.worker is None and self.loaded_session is not None:
            self.start_replay(self.loaded_session)

    def start_replay(self, session: Session):
        if self.worker is not None:
            return
        self.loaded_session = session
        self.is_demo_mode = False
        self.is_replay_mode = True
        self.requested_interval = max(.001, session.samples[0].received_dt_s)
        self.reset_visual_session()
        self.btn_pause.setText("Pause telemetry  [P]")
        self.replay_slider.setValue(0)
        self.lbl_replay_time.setText(f"Replay 0.00 / {session.duration_s:.2f} s · {len(session.samples)} samples")
        worker = ReplayWorker(session)
        self.worker = worker
        worker.data_received.connect(self.ingest_telemetry)
        worker.log_received.connect(self.log_message)
        worker.progress_received.connect(self.update_replay_progress)
        worker.step_started.connect(self.begin_replay_step)
        worker.step_completed.connect(self.end_replay_step)
        worker.finished.connect(lambda source=worker: self.source_finished(source))
        self.set_stream_controls(True)
        self.set_session_controls()
        self.btn_connect.setText("Stop replay")
        self.lbl_status.setText("REPLAY")
        worker.set_speed(self.replay_speeds[self.replay_speed_slider.value()])
        worker.start()

    def set_replay_speed(self, index):
        speed = self.replay_speeds[index]
        self.lbl_replay_speed.setText(f"{speed:g}×")
        if isinstance(self.worker, ReplayWorker):
            self.worker.set_speed(speed)
            self.update_replay_progress(self.replay_position_s, self.loaded_session.duration_s, force=True)

    def toggle_replay_playback(self):
        if not isinstance(self.worker, ReplayWorker):
            return
        if self.paused:
            # A worker may have queued a frame just before Pause was clicked.
            # Continue after the last frame actually shown in the dashboard.
            timestamps = [sample.timestamp_s for sample in self.loaded_session.samples]
            self.worker.set_next_index(bisect_right(timestamps, self.replay_position_s))
            self.paused = False
        else:
            self.paused = True
        self.worker.set_paused(self.paused)
        self.btn_play_replay.setText("Play  ▶" if self.paused else "Pause  ❚❚")
        self.log_message(f"SYS >> REPLAY {'PAUSED' if self.paused else 'PLAYING'}")

    def toggle_pause_shortcut(self):
        if self.is_replay_mode:
            self.btn_play_replay.click()
        else:
            self.btn_pause.click()

    def step_replay(self):
        if isinstance(self.worker, ReplayWorker):
            self.worker.request_step()
            self.paused = True
            self.btn_play_replay.setText("Play  ▶")

    def begin_replay_step(self):
        self.replay_step_in_flight = True

    def end_replay_step(self):
        self.replay_step_in_flight = False

    def begin_replay_seek(self):
        self.replay_seeking = True

    def preview_replay_seek(self, value):
        if self.loaded_session is not None:
            target = self.loaded_session.duration_s * value / self.replay_slider.maximum()
            self.lbl_replay_time.setText(f"Seek to {target:.2f} / {self.loaded_session.duration_s:.2f} s")

    def commit_replay_seek(self):
        self.replay_seeking = False
        if not isinstance(self.worker, ReplayWorker) or self.loaded_session is None:
            return
        target = self.loaded_session.duration_s * self.replay_slider.value() / self.replay_slider.maximum()
        timestamps = [sample.timestamp_s for sample in self.loaded_session.samples]
        index = min(bisect_left(timestamps, target), len(timestamps) - 1)
        # The attitude filter and event detector are stateful.  Replaying the
        # prior samples locally gives a seek the same result as a linear run.
        self.rebuild_replay_to(index)
        self.worker.set_next_index(index + 1)
        self.update_replay_progress(timestamps[index], self.loaded_session.duration_s, force=True)
        self.log_message(f"SYS >> REPLAY SEEK · {timestamps[index]:.2f} S")

    def rebuild_replay_to(self, index):
        if self.loaded_session is None:
            return
        was_paused = self.paused
        self.reset_visual_session()
        for sample in self.loaded_session.samples[:index + 1]:
            self.ingest_telemetry(
                sample.ax_g, sample.ay_g, sample.az_g, sample.gx_dps, sample.gy_dps, sample.gz_dps,
                sample.received_dt_s, source_time_s=sample.timestamp_s, record=False, report=False,
                allow_paused=True,
            )
        self.paused = was_paused
        self.render_frame(force=True)

    def update_replay_progress(self, position_s, duration_s, force=False):
        if self.paused and not (force or self.replay_step_in_flight):
            return
        self.replay_position_s = position_s
        if not self.replay_seeking and duration_s:
            self.replay_slider.setValue(round(position_s / duration_s * self.replay_slider.maximum()))
        self.lbl_replay_time.setText(
            f"Replay {position_s:.2f} / {duration_s:.2f} s · {self.replay_speeds[self.replay_speed_slider.value()]:g}×"
        )

    def disconnect_source(self):
        worker = self.worker
        if worker is None:
            return
        worker.stop()
        if not worker.wait(1500):
            self.log_message("SYS >> WAITING FOR SOURCE TO STOP")
            return
        self.source_finished(worker)
        self.log_message("SYS >> DISCONNECTED")

    def source_finished(self, source):
        if self.worker is not source:
            return
        was_replay = self.is_replay_mode
        replay_completed = was_replay and source.next_index >= len(source.session.samples)
        self.worker = None
        self.calibration_wizard.cancel("Connection ended. Reconnect to calibrate.")
        self.current_profile = None
        self.firmware_health = None
        source.deleteLater()
        self.paused = False
        self.is_replay_mode = False
        self.is_demo_mode = False
        if self.session_recorder is not None:
            self.stop_recording()
        self.set_stream_controls(False)
        self.set_session_controls()
        self.btn_connect.setText("Connect")
        self.btn_pause.setText("Pause telemetry  [P]")
        if was_replay and self.loaded_session is not None:
            self.lbl_replay_time.setText(
                (f"Replay complete · {self.loaded_session.duration_s:.2f} s · "
                 f"{len(self.loaded_session.samples)} samples") if replay_completed else
                f"Replay stopped at {self.replay_position_s:.2f} / {self.loaded_session.duration_s:.2f} s"
            )
        self.update_stats(time.monotonic())

    def send_command(self, cmd):
        if self.is_replay_mode and cmd == 'p':
            self.toggle_replay_playback()
            return
        if self.worker is None or not self.worker.isRunning():
            return
        if cmd in ('p', 'f', 'n', 's') and self.calibration_wizard.stage in ("gyro", "faces", "ready"):
            self.calibration_wizard.cancel("Stream changed. Start calibration again when live data resumes.")
        try:
            self.worker.send_cmd(cmd)
        except Exception as error:
            self.log_message(f"WARN >> COMMAND FAILED · {error}")
            return
        intervals = {'v': .03, 'f': .5, 'n': 1., 's': 2.}
        if cmd in intervals:
            self.requested_interval = intervals[cmd]
            self.sample_timestamps.clear()
            self.serial_interval_samples.clear()
            for key, button in self.rate_buttons.items():
                button.setChecked(key == cmd)
        elif cmd == 'p':
            self.paused = not self.paused
            subject = "replay" if self.is_replay_mode else "telemetry"
            self.btn_pause.setText(("Resume" if self.paused else "Pause") + f" {subject}  [P]")
            if not self.paused:
                self.last_sample_at = time.monotonic()
                self.sample_timestamps.clear()
                self.serial_interval_samples.clear()
        self.log_message(f"SYS >> COMMAND SENT · {cmd.upper()}")

    def ingest_telemetry(self, ax, ay, az, gx, gy, gz, dt, source_time_s=None, record=True,
                         report=True, allow_paused=False):
        if self.worker is None or (self.paused and not allow_paused and not self.replay_step_in_flight):
            return
        now = time.monotonic()
        if source_time_s is None:
            source_time_s = self.replay_position_s if self.is_replay_mode else now - self.session_started
        if self.is_live_serial():
            self.serial_packets_seen += 1
            if self.serial_packets_seen > 1:
                self.serial_interval_samples.append(dt)
            raw_sample = TelemetrySample(ax, ay, az, gx, gy, gz)
            self.calibration_wizard.feed(raw_sample, now)
            if self.current_profile is not None:
                corrected = self.current_profile.apply(raw_sample)
                ax, ay, az, gx, gy, gz = (corrected.ax, corrected.ay, corrected.az,
                                           corrected.gx, corrected.gy, corrected.gz)
        for key, value in zip(('ax', 'ay', 'az', 'gx', 'gy', 'gz'), (ax, ay, az, gx, gy, gz)):
            self.data[key].append(value)
        self.plot_times.append(source_time_s)
        sample = TelemetrySample(ax, ay, az, gx, gy, gz)
        self.latest_motion = self.estimator.update(sample, dt)
        self.pitch, self.roll, self.yaw = self.latest_motion.pitch, self.latest_motion.roll, self.latest_motion.yaw
        event = self.events.update(sample, source_time_s)
        if event is not None:
            stamp = time.strftime("%H:%M:%S")
            self.recent_events.appendleft(f"{stamp}   {event.kind}")
            if report:
                self.log_message(f"EVT >> {event.kind} · {event.peak_g:.2f} G MEASURED PEAK")
        self.latest_net_g = self.events.magnitude
        self.peak_g = max(self.peak_g, self.latest_net_g)
        self.sample_timestamps.append(now)
        self.last_sample_at = now
        if self.session_recorder is not None and record:
            try:
                self.session_recorder.write(SessionSample(
                    source_time_s, dt, ax, ay, az, gx, gy, gz,
                    self.latest_motion.pitch, self.latest_motion.roll, self.latest_motion.yaw,
                    self.latest_motion.linear_ax, self.latest_motion.linear_ay, self.latest_motion.linear_az,
                    self.events.state,
                    event.kind if event is not None else "",
                    event.peak_g if event is not None else None,
                    event.axis if event is not None else "",
                ))
            except OSError as error:
                self.log_message(f"WARN >> RECORDING WRITE FAILED · {error}")
                self.stop_recording()
        if report and now - self.last_terminal_telemetry_at >= self.terminal_rate_interval:
            self.log_message(
                f"TEL >> AX:{ax:+.2f} AY:{ay:+.2f} AZ:{az:+.2f} · "
                f"GX:{gx:+.1f} GY:{gy:+.1f} GZ:{gz:+.1f}"
            )
            self.last_terminal_telemetry_at = now

    def render_frame(self, force=False):
        now = time.monotonic()
        active = self.worker is not None and self.worker.isRunning() and not self.paused
        if self.latest_motion is not None and active:
            self.render_attitude_twin()
            self.frame_timestamps.append(now)
        if force or now - self.last_plot_render >= 1 / 20:
            times = list(self.plot_times)
            for curve, key in zip(self.accel_curves + self.gyro_curves, ('ax', 'ay', 'az', 'gx', 'gy', 'gz')):
                curve.setData(times, list(self.data[key]))
            self.last_plot_render = now
        if force or now - self.last_stats_render >= .25:
            self.update_stats(now)
            if self.calibration_dialog is not None and self.calibration_dialog.isVisible():
                self.calibration_dialog.refresh()
            self.last_stats_render = now

    def render_attitude_twin(self):
        motion = self.latest_motion
        transform = pg.Transform3D()
        transform.rotate(motion.yaw, 0, 0, 1)
        transform.rotate(motion.pitch, 1, 0, 0)
        transform.rotate(motion.roll, 0, 1, 0)
        if self.active_mesh is not None:
            self.active_mesh.setTransform(transform)
        self.triad.setTransform(transform)
        self.force_vector.setData(pos=np.array([[0, 0, 0],
            [-motion.linear_ax * 10, motion.linear_ay * 10, motion.linear_az * 10]]))
        self.lbl_angles.setText(f"Pitch  {motion.pitch:+.1f}°     Roll  {motion.roll:+.1f}°     Yaw  {motion.yaw:+.1f}°")

    def update_stats(self, now):
        while self.frame_timestamps and now - self.frame_timestamps[0] > 1:
            self.frame_timestamps.popleft()
        running = self.worker is not None and self.worker.isRunning()
        fresh = self.last_sample_at is not None and now - self.last_sample_at < max(1.5, self.requested_interval * 3)
        if not running:
            status, color = "OFFLINE", '#a9bbcb'
        elif self.paused:
            status, color = "PAUSED", '#ebc66d'
        elif self.last_sample_at is None:
            waiting = self.session_started is not None and now - self.session_started > max(1.5, self.requested_interval * 3)
            status, color = ("NO RECENT DATA" if waiting else "CONNECTING"), '#ebc66d'
        elif not fresh:
            status, color = "NO RECENT DATA", '#ebc66d'
        else:
            status, color = ("REPLAYING" if self.is_replay_mode else
                             ("DEMO CONNECTED" if self.is_demo_mode else "CONNECTED")), '#79d7a6'
        self.lbl_status.setText(status)
        self.lbl_status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.lbl_connection.setText(
            ("Replay" if self.is_replay_mode else ("Simulation" if self.is_demo_mode else "Serial"))
            if running else "Offline"
        )
        stamps = list(self.sample_timestamps)
        hz = ((len(stamps) - 1) / (stamps[-1] - stamps[0])) if len(stamps) > 1 and stamps[-1] > stamps[0] else 0
        if not running or self.paused or not fresh:
            hz = 0
        self.lbl_stream_rate.setText(f"{hz:.1f} / {len(self.frame_timestamps)} Hz")
        has_samples = bool(self.plot_times)
        self.lbl_current_g.setText(f"{self.latest_net_g:.2f} g" if has_samples else "— g")
        self.lbl_max_g.setText(f"{self.peak_g:.2f} g" if has_samples else "— g")
        self.lbl_motion_state.setText(self.events.state if running and fresh and not self.paused else
                                      ("Paused" if self.paused else "Waiting for data"))
        self.lbl_rotation.setText(f"{self.events.rotation:.1f} °/s" if has_samples else "— °/s")
        if self.events.latest:
            self.lbl_event_peak.setText(f"{self.events.latest.peak_g:.2f} g")
            axis = self.events.latest.axis
            self.lbl_event_axis.setText(f"IMU {axis}" if axis != "—" else "—")
        self.lbl_recent.setText("\n".join(self.recent_events) or "No events recorded")
        self.lbl_event_note.setText(
            "Brief events may be missed at this stream rate."
            if self.requested_interval > .1
            else "Motion labels use measured sensor activity"
        )

        self.lbl_health_source.setText("REPLAY" if self.is_replay_mode and running else
                                       ("DEMO" if self.is_demo_mode and running else
                                        (self.port_combo.currentText() if running else "—")))
        age = now - self.last_sample_at if self.last_sample_at is not None else None
        self.lbl_health_last_frame.setText(f"{age * 1000:.0f} MS" if age is not None and running else "—")
        self.lbl_health_invalid.setText(str(getattr(self.worker, 'invalid_frames', 0)) if running else "0")
        self.lbl_health_status.setText("NOMINAL" if running and fresh and not self.paused else status)

    def set_terminal_filter(self, category, visible):
        if visible:
            self.terminal_visible_categories.add(category)
        else:
            self.terminal_visible_categories.discard(category)
        self.render_terminal(force=True)

    def set_terminal_rate(self, index):
        interval, label = self.terminal_rate_options[index]
        self.terminal_rate_interval = 1.0 / interval
        self.lbl_terminal_rate.setText(label)
        # Make a rate change visible on the next incoming sample.
        self.last_terminal_telemetry_at = 0.0

    def toggle_session_health(self, expanded):
        self.health_panel.setVisible(expanded)
        self.btn_health.setText("Session Health  ▾" if expanded else "Session Health  ▸")

    @staticmethod
    def normalize_terminal_message(message):
        """Return one of the terminal's four visible categories and a uniform message."""
        raw_prefix, separator, payload = message.partition('>>')
        aliases = {
            'SYS': 'SYS', 'TEL': 'TEL', 'EVT': 'EVT', 'EVENT': 'EVT',
            'WARN': 'WARN', 'ERR': 'WARN', 'DEMO': 'SYS', 'TX': 'SYS',
        }
        category = aliases.get(raw_prefix.strip().upper(), 'TEL') if separator else 'TEL'
        text = (payload if separator else message).strip().upper()
        timestamp = time.strftime('%H:%M:%S') + f".{int(time.time() * 1000) % 1000:03d}"
        return timestamp, category, text or 'NO MESSAGE'

    def handle_terminal_scroll(self, value):
        if self._terminal_rendering:
            return
        scrollbar = self.terminal.verticalScrollBar()
        at_live_edge = value >= scrollbar.maximum() - 2
        if at_live_edge and not self.terminal_following:
            self.resume_terminal_live()
            return
        self.terminal_following = at_live_edge
        self.position_resume_terminal_button()
        self.btn_resume_terminal.setVisible(not self.terminal_following and bool(self.terminal_records))

    def resume_terminal_live(self):
        self.terminal_following = True
        self.render_terminal(force=True)
        self.btn_resume_terminal.hide()

    def render_terminal(self, force=False):
        # While someone is reading history, retain the visible QTextDocument.
        # New records still enter the bounded deque, but no reflow/culling can
        # shift the reader's current line until live-follow is resumed.
        if not self.terminal_following and not force:
            self.position_resume_terminal_button()
            self.btn_resume_terminal.setVisible(bool(self.terminal_records))
            return
        scrollbar = self.terminal.verticalScrollBar()
        visible = [(timestamp, category, text) for timestamp, category, text in self.terminal_records
                   if category in self.terminal_visible_categories]
        lines = [self.terminal_line_html(timestamp, category, text) for timestamp, category, text in visible]
        self._terminal_rendering = True
        self.terminal.setHtml('<div style="font-family:Consolas,monospace; font-size:11px; line-height:1.45;">' + ''.join(lines) + '</div>')
        if self.terminal_following:
            scrollbar.setValue(scrollbar.maximum())
        self._terminal_rendering = False
        self.btn_resume_terminal.setVisible(not self.terminal_following and bool(self.terminal_records))
        self.position_resume_terminal_button()

    def terminal_line_html(self, timestamp, category, text):
        colors = {'SYS': '#a6bcff', 'TEL': '#77d9df', 'EVT': '#f2c56b', 'WARN': '#ef9d7a'}
        include_timestamps = self.btn_timestamps.isChecked() if hasattr(self, 'btn_timestamps') else True
        stamp = f'<span style="color:#64778c;">{timestamp}</span>&nbsp;&nbsp;' if include_timestamps else ''
        prefix = f'<span style="color:{colors[category]}; font-weight:700;">{category}</span>'
        return f'<div>{stamp}{prefix}<span style="color:#edf3fa;">&nbsp;&nbsp;{html.escape(text)}</span></div>'

    def log_message(self, msg):
        record = self.normalize_terminal_message(msg)
        self.terminal_records.append(record)
        if not self.terminal_following:
            self.btn_resume_terminal.setVisible(bool(self.terminal_records))
            self.position_resume_terminal_button()
            return
        if record[1] not in self.terminal_visible_categories:
            return
        self._terminal_rendering = True
        self.terminal.append(self.terminal_line_html(*record))
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())
        self._terminal_rendering = False

    def closeEvent(self, event):
        self.focus_overlay.restore()
        if self.worker is not None:
            self.worker.stop()
            if not self.worker.wait(1500):
                event.ignore()
                return
        self.render_timer.stop()
        event.accept()


if __name__ == '__main__':
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    window = TelemetryDashboard()
    window.show()
    sys.exit(app.exec())
