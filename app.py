import sys, time, math, serial, html
from threading import Event
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
from dashboard_ui import STYLE, Panel, FocusOverlay
from motion_events import MotionEvents
from telemetry import TelemetrySample, parse_telemetry_line

# --- BACKGROUND SERIAL WORKER ---
class SerialWorker(QThread):
    data_received = pyqtSignal(float, float, float, float, float, float, float)
    log_received = pyqtSignal(str)
    
    def __init__(self, port, baud=115200):
        super().__init__()
        self.port, self.baud, self.running, self.ser, self.last_time = port, baud, True, None, None
        self.invalid_frames = 0

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            self.log_received.emit(f"SYS >> UART CONNECTED · {self.port} · {self.baud} BAUD")
            self.last_time = time.monotonic()
            
            while self.running and self.ser.is_open:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='replace').strip()
                    if not line: continue
                    sample = parse_telemetry_line(line)
                    if sample is not None:
                        now = time.monotonic()
                        dt = now - self.last_time if self.last_time else 0.03
                        self.last_time = now
                        self.data_received.emit(sample.ax, sample.ay, sample.az, sample.gx, sample.gy, sample.gz, dt)
                    elif line.startswith("AX:"):
                        self.invalid_frames += 1
                        if self.invalid_frames == 1 or self.invalid_frames % 50 == 0:
                            self.log_received.emit(f"WARN >> REJECTED {self.invalid_frames} INVALID TELEMETRY FRAME(S)")
                    else:
                        self.log_received.emit(f"TEL >> FIRMWARE · {line}")
                time.sleep(0.001)
        except Exception as e:
            if self.running:
                self.log_received.emit(f"WARN >> SERIAL ERROR · {e}")
        finally:
            if self.ser and self.ser.is_open:
                self.ser.close()

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

def create_cubesat_mesh():
    verts = [[-2,-1.5,-1],[2,-1.5,-1],[2,1.5,-1],[-2,1.5,-1],[-2,-1.5,1],[2,-1.5,1],[2,1.5,1],[-2,1.5,1]]
    faces = [[0,1,2],[0,2,3],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[2,3,7],[2,7,6],[0,3,7],[0,7,4],[1,2,6],[1,6,5]]
    return create_mesh(verts, faces, (0.1, 0.6, 1.0, 0.8))

def create_satellite_mesh():
    verts = [[-1.5,-1.5,-1.5],[1.5,-1.5,-1.5],[1.5,1.5,-1.5],[-1.5,1.5,-1.5],[-1.5,-1.5,1.5],[1.5,-1.5,1.5],[1.5,1.5,1.5],[-1.5,1.5,1.5],
             [-8.0,-1.0,0.0],[-1.5,-1.0,0.0],[-1.5,1.0,0.0],[-8.0,1.0,0.0],[1.5,-1.0,0.0],[8.0,-1.0,0.0],[8.0,1.0,0.0],[1.5,1.0,0.0]]
    faces = [[0,1,2],[0,2,3],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[2,3,7],[2,7,6],[0,3,7],[0,7,4],[1,2,6],[1,6,5],[8,9,10],[8,10,11],[12,13,14],[12,14,15]]
    return create_mesh(verts, faces, (1.0, 0.6, 0.0, 0.95))

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
        self.last_model_index = 1
        self.data = {axis: deque(maxlen=self.max_points) for axis in ('ax', 'ay', 'az', 'gx', 'gy', 'gz')}
        self.plot_times = deque(maxlen=self.max_points)
        self.sample_timestamps = deque(maxlen=120)
        self.frame_timestamps = deque(maxlen=120)
        self.latest_motion = None
        self.latest_net_g = self.peak_g = 0.0
        self.pitch = self.roll = self.yaw = 0.0
        self.is_demo_mode = self.paused = False
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
        self.init_ui()
        self.shortcuts = []
        for key, button in self.button_map.items():
            shortcut = QShortcut(QKeySequence(key), self)
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
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setMinimumWidth(210)
        rail.setMaximumWidth(300)
        controls = QVBoxLayout(rail)
        controls.setContentsMargins(14, 16, 14, 14)
        controls.setSpacing(10)
        controls.addWidget(self.label("STREAM CONTROL", "section"))
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
        controls.addLayout(rates)
        self.btn_pause = QPushButton("Pause telemetry  [P]")
        self.btn_pause.clicked.connect(lambda: self.send_command('p'))
        self.button_map['p'] = self.btn_pause
        controls.addWidget(self.btn_pause)
        controls.addSpacing(12)
        controls.addWidget(self.label("3D ATTITUDE", "section"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(["Satellite / Spacecraft", "Jet Aircraft", "CubeSat Enclosure", "Load Custom STL…"])
        self.model_combo.setCurrentIndex(1)
        self.model_combo.currentIndexChanged.connect(self.change_3d_model)
        controls.addWidget(self.model_combo)
        self.btn_reset_yaw = QPushButton("Reset attitude  [R]")
        self.btn_reset_yaw.clicked.connect(self.reset_yaw)
        self.button_map['r'] = self.btn_reset_yaw
        controls.addWidget(self.btn_reset_yaw)
        controls.addSpacing(12)
        controls.addWidget(self.label("MEMORY TOOLS", "section"))
        self.btn_dump = QPushButton("Export memory snapshot  [D]")
        self.btn_dump.clicked.connect(lambda: self.send_command('d'))
        self.btn_clear = QPushButton("Erase flash sector 5  [C]")
        self.btn_clear.setObjectName("danger")
        self.btn_clear.clicked.connect(lambda: self.send_command('c'))
        self.button_map.update({'d': self.btn_dump, 'c': self.btn_clear})
        controls.addWidget(self.btn_dump)
        controls.addWidget(self.btn_clear)
        controls.addSpacing(12)
        controls.addWidget(self.label("FIRMWARE TERMINAL", "section"))
        terminal_rate_row = QHBoxLayout()
        terminal_rate_row.addWidget(self.label("OUTPUT RATE", "muted"))
        self.lbl_terminal_rate = self.label("1000 ms", "healthValue")
        terminal_rate_row.addStretch()
        terminal_rate_row.addWidget(self.lbl_terminal_rate)
        controls.addLayout(terminal_rate_row)
        self.terminal_rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.terminal_rate_slider.setRange(0, len(self.terminal_rate_options) - 1)
        self.terminal_rate_slider.setValue(0)
        self.terminal_rate_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.terminal_rate_slider.setTickInterval(1)
        self.terminal_rate_slider.setToolTip("Choose how often TEL summaries are printed. Sensor capture remains full rate.")
        self.terminal_rate_slider.valueChanged.connect(self.set_terminal_rate)
        controls.addWidget(self.terminal_rate_slider)
        self.btn_timestamps = QPushButton("Show timestamps")
        self.btn_timestamps.setObjectName("terminalFilter")
        self.btn_timestamps.setCheckable(True)
        self.btn_timestamps.setChecked(True)
        self.btn_timestamps.setToolTip("Show the local receive time before each terminal prefix.")
        self.btn_timestamps.toggled.connect(lambda checked: self.render_terminal(force=True))
        controls.addWidget(self.btn_timestamps)
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
            controls.addWidget(button)
        controls.addStretch()
        body.addWidget(rail)

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
        self.change_3d_model(1)
        self.set_stream_controls(False)
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
            self.button_map[key].setEnabled(enabled)
        self.port_combo.setEnabled(not enabled)

    def change_3d_model(self, index):
        factories = [create_satellite_mesh, create_aircraft_mesh, create_cubesat_mesh]
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

    def toggle_connection(self):
        if self.worker is not None:
            self.disconnect_source()
            return
        port = self.port_combo.currentText()
        self.is_demo_mode = port.startswith("DEMO")
        self.paused = False
        self.requested_interval = 0.03
        self.events = MotionEvents()
        self.recent_events.clear()
        self.latest_net_g = self.peak_g = 0.0
        self.sample_timestamps.clear()
        self.plot_times.clear()
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
        self.btn_pause.setText("Pause telemetry  [P]")
        for key, button in self.rate_buttons.items():
            button.setChecked(key == 'v')
        worker = DemoWorker() if self.is_demo_mode else SerialWorker(port)
        self.worker = worker
        worker.data_received.connect(self.ingest_telemetry)
        worker.log_received.connect(self.log_message)
        worker.finished.connect(lambda source=worker: self.source_finished(source))
        self.set_stream_controls(True)
        self.btn_connect.setText("Disconnect")
        self.lbl_status.setText("CONNECTING")
        worker.start()
        QTimer.singleShot(500, lambda source=worker: self.send_command(
            next(key for key, button in self.rate_buttons.items() if button.isChecked()))
            if self.worker is source and not self.paused else None)

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
        self.worker = None
        source.deleteLater()
        self.paused = False
        self.set_stream_controls(False)
        self.btn_connect.setText("Connect")
        self.btn_pause.setText("Pause telemetry  [P]")
        self.update_stats(time.monotonic())

    def send_command(self, cmd):
        if self.worker is None or not self.worker.isRunning():
            return
        try:
            self.worker.send_cmd(cmd)
        except Exception as error:
            self.log_message(f"WARN >> COMMAND FAILED · {error}")
            return
        intervals = {'v': .03, 'f': .5, 'n': 1., 's': 2.}
        if cmd in intervals:
            self.requested_interval = intervals[cmd]
            self.sample_timestamps.clear()
            for key, button in self.rate_buttons.items():
                button.setChecked(key == cmd)
        elif cmd == 'p':
            self.paused = not self.paused
            self.btn_pause.setText(("Resume" if self.paused else "Pause") + " telemetry  [P]")
            if not self.paused:
                self.last_sample_at = time.monotonic()
                self.sample_timestamps.clear()
        self.log_message(f"SYS >> COMMAND SENT · {cmd.upper()}")

    def ingest_telemetry(self, ax, ay, az, gx, gy, gz, dt):
        if self.worker is None or self.paused:
            return
        now = time.monotonic()
        for key, value in zip(('ax', 'ay', 'az', 'gx', 'gy', 'gz'), (ax, ay, az, gx, gy, gz)):
            self.data[key].append(value)
        self.plot_times.append(now - self.session_started)
        sample = TelemetrySample(ax, ay, az, gx, gy, gz)
        self.latest_motion = self.estimator.update(sample, dt)
        self.pitch, self.roll, self.yaw = self.latest_motion.pitch, self.latest_motion.roll, self.latest_motion.yaw
        event = self.events.update(sample, now)
        if event is not None:
            stamp = time.strftime("%H:%M:%S")
            self.recent_events.appendleft(f"{stamp}   {event.kind}")
            self.log_message(f"EVT >> {event.kind} · {event.peak_g:.2f} G MEASURED PEAK")
        self.latest_net_g = self.events.magnitude
        self.peak_g = max(self.peak_g, self.latest_net_g)
        self.sample_timestamps.append(now)
        self.last_sample_at = now
        if now - self.last_terminal_telemetry_at >= self.terminal_rate_interval:
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
            status, color = ("DEMO CONNECTED" if self.is_demo_mode else "CONNECTED"), '#79d7a6'
        self.lbl_status.setText(status)
        self.lbl_status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.lbl_connection.setText(("Simulation" if self.is_demo_mode else "Serial") if running else "Offline")
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

        self.lbl_health_source.setText("DEMO" if self.is_demo_mode and running else
                                       (self.port_combo.currentText() if running else "—"))
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
