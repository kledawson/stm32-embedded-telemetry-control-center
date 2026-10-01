import sys, time, math, html
from bisect import bisect_left, bisect_right
from collections import deque
import serial.tools.list_ports
import numpy as np
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QComboBox, QTextEdit, 
                             QGridLayout, QSplitter, QLabel, QFrame,
                             QFileDialog, QMessageBox, QToolButton, QSlider)
from PyQt6.QtCore import Qt, QTimer, QEvent, QSize
from PyQt6.QtGui import QKeySequence, QShortcut, QIcon, QPixmap, QPainter, QColor
import pyqtgraph as pg
import pyqtgraph.opengl as gl

from kinematics import AttitudeEstimator
from dashboard_ui import STYLE, Panel, CollapsibleSection, FocusOverlay, FloatingChartControls, RailScrollArea, fit_window_to_screen
from motion_events import MotionEvents
from telemetry import TelemetrySample, calculate_checksum, parse_telemetry_line
from session_io import Session, SessionRecorder, SessionSample, load_session
from session_ui import SessionImportDialog
from calibration import CalibrationWizard, load_profile, save_profile
from calibration_ui import CalibrationDialog
from fault_ui import FaultLabDialog
from sources import SerialWorker, DemoWorker, ReplayWorker
from models import create_aircraft_mesh, create_satellite_mesh, create_drone_mesh, create_stl_mesh


class SteadyGLViewWidget(gl.GLViewWidget):
    """Keep camera orbit and pan proportional, but less twitchy during drags."""

    drag_sensitivity = 0.25

    def mouseMoveEvent(self, event):
        buttons = event.buttons()
        if buttons not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            return super().mouseMoveEvent(event)
        position = event.position()
        if not hasattr(self, "mousePos"):
            self.mousePos = position
        delta = (position - self.mousePos) * self.drag_sensitivity
        self.mousePos = position
        if buttons == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.pan(delta.x(), delta.y(), 0, relative="view")
            else:
                self.orbit(-delta.x(), delta.y())
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.pan(delta.x(), 0, delta.y(), relative="view-upright")
        else:
            self.pan(delta.x(), delta.y(), 0, relative="view-upright")
        event.accept()


class TelemetryDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Telemetry Console")
        fit_window_to_screen(self, (1440, 900), (1060, 730))
        self.setStyleSheet(STYLE)
        self.worker = None
        self.plot_window_s = 30.0
        self.button_map = {}
        self.estimator = AttitudeEstimator()
        self.events = MotionEvents()
        self.active_mesh = None
        self.last_model_index = 0
        self.data = {axis: deque() for axis in ('ax', 'ay', 'az', 'gx', 'gy', 'gz')}
        self.plot_times = deque()
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
        self.fault_dialog = None
        self.demo_fault_kind = None
        self.demo_fault_phase = "idle"
        self.demo_rejected_frames = 0
        self.demo_reset_reason = None
        self.demo_fault_detail = ""
        self.demo_fault_started_at = 0.0
        self.demo_fault_gap_start = None
        self.demo_fault_gap_ms = None
        self.demo_fault_packet = None
        self.demo_fault_next_frame = None
        self.demo_fault_hold_at = None
        self.demo_fault_release_at = None
        self.demo_fault_stale_at = None
        self.demo_fault_reboot_at = None
        self.demo_fault_first_return_at = None
        self.demo_fault_recovered_at = None
        self.demo_fault_frame_times = []
        self.live_fault_kind = None
        self.live_fault_phase = "idle"
        self.live_fault_detail = ""
        self.live_fault_started_at = 0.0
        self.live_fault_gap_start = None
        self.live_fault_gap_ms = None
        self.live_fault_packet = None
        self.live_fault_next_frame = None
        self.live_fault_hold_at = None
        self.live_fault_release_at = None
        self.live_fault_stale_at = None
        self.live_fault_reboot_at = None
        self.live_fault_first_return_at = None
        self.live_fault_recovered_at = None
        self.live_fault_frame_times = []
        self.live_fault_reconnect_port = None
        self.live_fault_reconnecting = False
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
        self.btn_fault_lab = QPushButton("Fault Injection")
        self.btn_fault_lab.setToolTip("Show deliberate faults using demo or live firmware data")
        self.btn_fault_lab.clicked.connect(self.open_fault_lab)
        bar.addWidget(self.btn_fault_lab)
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
        self.btn_record.setToolTip("Record validated telemetry to one ZIP containing its CSV and JSON manifest.")
        self.btn_record.clicked.connect(self.toggle_recording)
        session_controls.addWidget(self.btn_record)
        self.lbl_recording_status = self.label('', 'muted')
        self.lbl_recording_status.setStyleSheet('color: #ebc66d; font-size: 11px;')
        self.lbl_recording_status.setWordWrap(True)
        self.lbl_recording_status.hide()
        session_controls.addWidget(self.lbl_recording_status)
        self.btn_open_session = QPushButton("Open session for replay…")
        self.btn_open_session.setToolTip("Open a session ZIP, or select its CSV and JSON together, without hardware.")
        self.btn_open_session.clicked.connect(self.open_session)
        session_controls.addWidget(self.btn_open_session)
        playback_row = QHBoxLayout()
        playback_row.setSpacing(5)
        self.btn_play_replay = QPushButton("Play  ▶")
        self.btn_play_replay.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        pause_pixmap = QPixmap(10, 10)
        pause_pixmap.fill(Qt.GlobalColor.transparent)
        pause_painter = QPainter(pause_pixmap)
        pause_painter.fillRect(2, 3, 2, 6, QColor('#edf3fa'))
        pause_painter.fillRect(6, 3, 2, 6, QColor('#edf3fa'))
        pause_painter.end()
        self.replay_pause_icon = QIcon(pause_pixmap)
        self.btn_play_replay.setIconSize(QSize(10, 10))
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
        attitude_controls = section_layout("3D SIMULATION")
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
        self.view_3d = SteadyGLViewWidget()
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
        self.lbl_angles.setToolTip("Drag gently to orbit · Ctrl+drag to pan · wheel to zoom. Cyan vector: estimated linear acceleration.")
        model_layout.addWidget(self.lbl_angles)
        self.model_panel = Panel("3D Simulation", model_body, self.expand_panel)
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
        self.focus_overlay.expand(panel, self.make_focus_controls(panel))
        self.sync_focus_controls()

    def linked_focus_button(self, text, source, name):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setCheckable(source.isCheckable())
        if source.isCheckable():
            button.setChecked(source.isChecked())
            source.toggled.connect(button.setChecked)
        button.setEnabled(source.isEnabled())
        button.setIcon(source.icon())
        button.setIconSize(source.iconSize())
        button.setLayoutDirection(source.layoutDirection())
        button.clicked.connect(source.click)
        return button

    def make_focus_controls(self, panel):
        if panel is self.model_panel:
            return self.make_simulation_controls()
        if panel in (self.accel_panel, self.gyro_panel):
            return self.make_chart_controls()
        if panel is self.terminal_panel:
            return self.make_terminal_controls()
        return None

    def make_terminal_controls(self):
        controls = FloatingChartControls('Terminal controls')
        controls.anchor_widget = self.terminal.viewport()
        box = controls.body_layout
        box.addWidget(self.label('SHOW MESSAGES', 'section'))
        filters = QGridLayout()
        filters.setSpacing(6)
        for index, category in enumerate(('SYS', 'TEL', 'EVT', 'WARN')):
            filters.addWidget(self.linked_focus_button(category, self.terminal_filter_buttons[category],
                                                       f'focusTerminal_{category}'), 0, index)
        box.addLayout(filters)
        toggles = QHBoxLayout()
        toggles.setSpacing(6)
        toggles.addWidget(self.linked_focus_button('Timestamps', self.btn_timestamps, 'focusTimestamps'), 1)
        toggles.addWidget(self.linked_focus_button('Health', self.btn_health, 'focusHealth'), 1)
        box.addLayout(toggles)
        rate_row = QHBoxLayout()
        rate_row.addWidget(self.label('OUTPUT RATE', 'section'))
        rate_row.addStretch()
        output_picker = QComboBox()
        output_picker.setObjectName('focusTerminalRate')
        output_picker.setAccessibleName('Terminal output rate')
        output_picker.addItems(label for _, label in self.terminal_rate_options)
        output_picker.setCurrentIndex(self.terminal_rate_slider.value())
        output_picker.currentIndexChanged.connect(self.terminal_rate_slider.setValue)
        self.terminal_rate_slider.valueChanged.connect(output_picker.setCurrentIndex)
        rate_row.addWidget(output_picker)
        box.addLayout(rate_row)
        box.addWidget(self.linked_focus_button('Follow live', self.btn_resume_terminal, 'focusFollowLive'))
        return controls

    def make_simulation_controls(self):
        controls = FloatingChartControls('Simulation controls')
        controls.anchor_widget = self.view_3d
        box = controls.body_layout
        box.addWidget(self.label('3D MODEL', 'section'))
        picker = QComboBox()
        picker.setObjectName('focusModelPicker')
        picker.setAccessibleName('Simulation model')
        picker.addItems(self.model_combo.itemText(index) for index in range(self.model_combo.count()))
        picker.setCurrentIndex(self.model_combo.currentIndex())
        picker.currentIndexChanged.connect(self.model_combo.setCurrentIndex)
        self.model_combo.currentIndexChanged.connect(picker.setCurrentIndex)
        box.addWidget(picker)
        box.addWidget(self.linked_focus_button('Reset attitude  [R]', self.btn_reset_yaw, 'focusReset'))
        return controls

    def make_chart_controls(self):
        controls = FloatingChartControls()
        box = controls.body_layout
        if not self.is_replay_mode:
            box.addWidget(self.label('STREAM RATE', 'section'))
            rates = QGridLayout()
            rates.setSpacing(8)
            for index, (key, title) in enumerate((('v', '33 Hz'), ('f', '2 Hz'), ('n', '1 Hz'), ('s', '0.5 Hz'))):
                rates.addWidget(self.linked_focus_button(title, self.rate_buttons[key], f'focusRate_{key}'), index // 2, index % 2)
            box.addLayout(rates)
            return controls
        box.addWidget(self.label('SESSION REPLAY', 'section'))
        name = self.label(self.loaded_session.path.name if self.loaded_session else 'Recorded session', 'muted')
        name.setWordWrap(True)
        box.addWidget(name)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        for title, source, object_name in (
                (self.btn_play_replay.text(), self.btn_play_replay, 'focusReplayPlay'),
                ('Restart ↺', self.btn_restart_replay, 'focusReplayRestart'),
                ('Step ▸|', self.btn_step, 'focusReplayStep')):
            button = self.linked_focus_button(title, source, object_name)
            button.setMinimumWidth(0)
            button.setToolTip(source.toolTip() or title)
            actions.addWidget(button, 1)
        box.addLayout(actions)
        speed_row = QHBoxLayout()
        speed_row.addWidget(self.label('Playback speed', 'muted'))
        speed_row.addStretch()
        speed = QComboBox()
        speed.setObjectName('focusReplaySpeedPicker')
        speed.setAccessibleName('Chart replay speed')
        speed.addItems(f'{value:g}×' for value in self.replay_speeds)
        speed.setCurrentIndex(self.replay_speed_slider.value())
        speed.currentIndexChanged.connect(self.replay_speed_slider.setValue)
        self.replay_speed_slider.valueChanged.connect(speed.setCurrentIndex)
        speed_row.addWidget(speed)
        box.addLayout(speed_row)
        divider = QFrame()
        divider.setObjectName('chartControlDivider')
        divider.setFixedHeight(1)
        box.addWidget(divider)
        timeline = QHBoxLayout()
        status = self.label('Playing', 'healthValue')
        status.setObjectName('focusReplayState')
        timeline.addWidget(status)
        timeline.addStretch()
        time_label = self.label('', 'muted')
        time_label.setObjectName('focusReplayTime')
        timeline.addWidget(time_label)
        box.addLayout(timeline)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName('focusReplayPosition')
        slider.setAccessibleName('Chart replay position')
        slider.setRange(self.replay_slider.minimum(), self.replay_slider.maximum())
        slider.setValue(self.replay_slider.value())
        slider.valueChanged.connect(self.replay_slider.setValue)
        self.replay_slider.valueChanged.connect(slider.setValue)
        slider.sliderPressed.connect(self.begin_replay_seek)
        slider.sliderMoved.connect(self.preview_replay_seek)
        slider.sliderReleased.connect(self.commit_replay_seek)
        slider.installEventFilter(self)
        box.addWidget(slider)
        return controls

    def sync_focus_controls(self):
        if not hasattr(self, "focus_overlay") or self.focus_overlay.controls is None:
            return
        controls = self.focus_overlay.controls
        for key, source in self.rate_buttons.items():
            button = controls.findChild(QPushButton, f"focusRate_{key}")
            if button is not None:
                button.setEnabled(source.isEnabled())
                button.setChecked(source.isChecked())
        play = controls.findChild(QPushButton, "focusReplayPlay")
        if play is not None:
            play.setText(self.btn_play_replay.text())
            play.setIcon(self.btn_play_replay.icon())
            play.setEnabled(self.btn_play_replay.isEnabled())
        speed = controls.findChild(QSlider, "focusReplaySpeedSlider")
        if speed is not None:
            speed.setEnabled(self.replay_speed_slider.isEnabled())
        for name, source in (('focusReplayRestart', self.btn_restart_replay), ('focusReplayStep', self.btn_step)):
            button = controls.findChild(QPushButton, name)
            if button is not None:
                button.setEnabled(source.isEnabled())
        picker = controls.findChild(QComboBox, 'focusReplaySpeedPicker')
        if picker is not None:
            picker.setEnabled(self.replay_speed_slider.isEnabled())
        position = controls.findChild(QSlider, 'focusReplayPosition')
        if position is not None:
            position.setEnabled(self.replay_slider.isEnabled())
        status = controls.findChild(QLabel, 'focusReplayState')
        if status is not None:
            status.setText('Paused' if self.paused else 'Playing' if self.is_replay_mode else 'Offline')
            status.setStyleSheet('color: #ebc66d;' if self.paused else 'color: #86d8a6;')
        self.sync_chart_replay_time()

    def sync_chart_replay_time(self, preview=None):
        if not hasattr(self, 'focus_overlay') or self.focus_overlay.controls is None:
            return
        label = self.focus_overlay.controls.findChild(QLabel, 'focusReplayTime')
        if label is not None and self.loaded_session is not None:
            position = self.replay_position_s if preview is None else preview
            label.setText(f'{position:.2f} s / {self.loaded_session.duration_s:.2f} s')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'focus_overlay'):
            self.focus_overlay.setGeometry(self.centralWidget().rect())
        if hasattr(self, 'btn_resume_terminal'):
            self.position_resume_terminal_button()

    def eventFilter(self, watched, event):
        if watched.objectName() == 'focusReplayPosition' and watched.isEnabled():
            actions = {Qt.Key.Key_Left: QSlider.SliderAction.SliderSingleStepSub,
                       Qt.Key.Key_Down: QSlider.SliderAction.SliderSingleStepSub,
                       Qt.Key.Key_Right: QSlider.SliderAction.SliderSingleStepAdd,
                       Qt.Key.Key_Up: QSlider.SliderAction.SliderSingleStepAdd,
                       Qt.Key.Key_PageDown: QSlider.SliderAction.SliderPageStepSub,
                       Qt.Key.Key_PageUp: QSlider.SliderAction.SliderPageStepAdd,
                       Qt.Key.Key_Home: QSlider.SliderAction.SliderToMinimum,
                       Qt.Key.Key_End: QSlider.SliderAction.SliderToMaximum}
            action = actions.get(event.key()) if event.type() == QEvent.Type.KeyPress else None
            if event.type() == QEvent.Type.Wheel:
                action = QSlider.SliderAction.SliderSingleStepAdd if event.angleDelta().y() > 0 else QSlider.SliderAction.SliderSingleStepSub
            if action is not None:
                self.begin_replay_seek()
                watched.triggerAction(action)
                self.preview_replay_seek(watched.value())
                self.commit_replay_seek()
                return True
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
        self.sync_focus_controls()

    def set_session_controls(self):
        active = self.worker is not None
        live_source = active and not self.is_replay_mode
        recording = self.session_recorder is not None
        self.btn_record.setEnabled(live_source)
        self.btn_record.setText("Stop && Save Recording" if recording else "Start Recording")
        self.btn_open_session.setEnabled(not recording)
        self.btn_restart_replay.setEnabled(self.loaded_session is not None and (not active or self.is_replay_mode))
        replay_active = active and self.is_replay_mode
        self.btn_play_replay.setEnabled(replay_active)
        self.set_replay_button_state(replay_active and not self.paused)
        self.replay_slider.setEnabled(replay_active)
        self.replay_speed_slider.setEnabled(replay_active)
        self.btn_step.setEnabled(replay_active)
        self.update_recording_status()
        self.sync_focus_controls()

    def update_recording_status(self):
        recorder = self.session_recorder
        self.lbl_recording_status.setVisible(recorder is not None)
        if recorder is not None:
            self.lbl_recording_status.setText(f'● Recording · {recorder.sample_count:,} samples · {recorder.duration_s:.1f} s')

    def set_replay_button_state(self, playing):
        self.btn_play_replay.setText('Pause' if playing else 'Play  ▶')
        self.btn_play_replay.setIcon(self.replay_pause_icon if playing else QIcon())

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
        if self.focus_overlay.controls is not None:
            picker = self.focus_overlay.controls.findChild(QComboBox, "focusModelPicker")
            if picker is not None:
                picker.blockSignals(True)
                picker.setCurrentIndex(self.last_model_index)
                picker.blockSignals(False)

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

    def open_fault_lab(self):
        if self.fault_dialog is None:
            self.fault_dialog = FaultLabDialog(self)
            self.fault_dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.fault_dialog.refresh()
        self.fault_dialog.show()
        self.fault_dialog.raise_()
        self.fault_dialog.activateWindow()

    def start_demo_for_fault_lab(self):
        if self.worker is not None:
            return
        self.port_combo.setCurrentIndex(0)
        self.toggle_connection()
        if self.fault_dialog is not None:
            self.fault_dialog.record_event("Demo source connected · ready to inject")
            self.fault_dialog.refresh()

    def inject_live_fault(self, kind):
        commands = {"checksum": "x", "watchdog": "w", "mutex": "m"}
        worker = self.worker
        if (kind not in commands or not isinstance(worker, SerialWorker) or
                not worker.isRunning() or self.paused or self.last_sample_at is None or
                time.monotonic() - self.last_sample_at > 1.5 or
                self.live_fault_phase in ("sent", "armed", "stalled", "contending", "recovering")):
            return False
        try:
            if not worker.send_cmd(commands[kind]):
                return False
        except Exception as error:
            self.log_message(f"WARN >> FAULT COMMAND FAILED · {error}")
            return False
        self.live_fault_kind = kind
        self.live_fault_phase = "sent"
        self.live_fault_started_at = time.monotonic()
        self.live_fault_detail = "Command sent; waiting for the board to acknowledge it."
        self.live_fault_gap_start = None
        self.live_fault_gap_ms = None
        self.live_fault_packet = None
        self.live_fault_next_frame = None
        self.live_fault_hold_at = None
        self.live_fault_release_at = None
        self.live_fault_stale_at = None
        self.live_fault_reboot_at = None
        self.live_fault_first_return_at = None
        self.live_fault_recovered_at = None
        self.live_fault_frame_times = list(self.sample_timestamps)[-12:] if kind in ("mutex", "watchdog") else []
        if kind == "watchdog":
            self.live_fault_gap_start = self.last_sample_at
        if kind == "watchdog" and self.session_recorder is not None:
            self.stop_recording("watchdog test started")
        self.log_message(f"SYS >> LIVE FAULT TEST SENT · {kind.upper()}")
        if self.fault_dialog is not None:
            self.fault_dialog.record_event(f"COM3 command sent · {kind} · awaiting firmware marker")
            self.fault_dialog.refresh()
        return True

    def on_live_fault_marker(self, source, marker):
        if self.worker is not source or self.live_fault_phase not in ("sent", "armed", "contending"):
            return
        expected = {"checksum": "CHECKSUM ARMED", "watchdog": "WATCHDOG STARVE",
                    "mutex": "MUTEX HOLD"}
        if marker == f"[FAULT]: {expected.get(self.live_fault_kind)}":
            self.live_fault_phase = "contending" if self.live_fault_kind == "mutex" else "armed"
            self.live_fault_detail = ("StatusTask owns the UART mutex; telemetry is waiting." if self.live_fault_kind == "mutex" else
                                      "TelemetryTask is no longer feeding IWDG." if self.live_fault_kind == "watchdog" else
                                      "The next real sensor packet will have one payload bit flipped on the board.")
            if self.live_fault_kind == "mutex":
                self.live_fault_gap_start = self.last_sample_at
                self.live_fault_hold_at = time.monotonic()
            if self.fault_dialog is not None:
                self.fault_dialog.record_event(marker.removeprefix("[FAULT]: ").capitalize() + " · board acknowledged")
        elif marker == "[FAULT]: MUTEX RELEASED" and self.live_fault_kind == "mutex" and self.live_fault_phase == "contending":
            self.live_fault_release_at = time.monotonic()
            self.live_fault_phase = "recovering"
            self.live_fault_detail = "Mutex released; waiting for the next valid frame."
            if self.fault_dialog is not None:
                self.fault_dialog.record_event("UART mutex released · waiting for telemetry")
        if self.fault_dialog is not None:
            self.fault_dialog.refresh()

    def on_live_rejected_frame(self, source, line):
        if (self.worker is not source or self.live_fault_kind != "checksum" or
                self.live_fault_phase != "armed"):
            return
        packet = self._bit_flip_evidence(line)
        if packet is None:
            return
        self.live_fault_packet = packet
        self.live_fault_phase = "recovering"
        self.live_fault_detail = (f"Board sent CHK {packet['sent_checksum']}; received payload calculates to "
                                  f"{packet['calculated_checksum']}. Parser rejected this frame.")
        self.log_message("WARN >> LIVE CHECKSUM FAULT · FRAME REJECTED BEFORE GRAPH UPDATE")
        if self.fault_dialog is not None:
            self.fault_dialog.record_event(self.live_fault_detail)
            self.fault_dialog.refresh()

    @staticmethod
    def _bit_flip_evidence(line):
        payload, separator, suffix = line.partition("|CHK:0x")
        if not separator:
            return None
        # The firmware flips bit 0 of the first numeric AX character. Undoing
        # that bit gives a byte-for-byte before/after view of this real frame.
        flipped_index = next((index for index in range(3, len(payload)) if payload[index].isdigit()), None)
        original_payload = None
        if flipped_index is not None:
            original_payload = (payload[:flipped_index] + chr(ord(payload[flipped_index]) ^ 1) +
                                payload[flipped_index + 1:])
        try:
            sent_checksum = int(suffix, 16)
        except ValueError:
            return None
        reconstructed = original_payload is not None and calculate_checksum(original_payload) == sent_checksum
        return {
            "before_ax": original_payload.partition("|")[0] if reconstructed else "Original unavailable",
            "after_ax": payload.partition("|")[0],
            "before_byte": f"0x{ord(original_payload[flipped_index]):02X}" if reconstructed else "—",
            "after_byte": f"0x{ord(payload[flipped_index]):02X}" if flipped_index is not None else "—",
            "sent_checksum": f"0x{sent_checksum:02X}",
            "calculated_checksum": f"0x{calculate_checksum(payload):02X}",
        }

    def on_live_accepted_frame(self, source, line):
        if (self.worker is not source or self.live_fault_kind != "checksum" or
                self.live_fault_phase != "recovering" or self.live_fault_next_frame is not None):
            return
        sample = parse_telemetry_line(line)
        if sample is not None:
            self.live_fault_next_frame = {
                "ax": line.partition("|")[0],
                "sequence": sample.sequence,
                "checksum": line.rpartition("|CHK:")[2],
            }

    def update_live_fault(self, now):
        phase, kind = self.live_fault_phase, self.live_fault_kind
        if phase not in ("sent", "armed", "stalled", "contending", "recovering"):
            return
        if kind == "watchdog" and phase == "armed" and self.last_sample_at is not None and now - self.last_sample_at > .55:
            self.live_fault_phase = "stalled"
            self.live_fault_stale_at = now
            self.live_fault_detail = "Telemetry stopped; waiting for the hardware watchdog to reset the MCU."
            if self.fault_dialog is not None:
                self.fault_dialog.record_event("Real COM3 telemetry became stale · watchdog countdown in hardware")
        timeout = 25.0 if kind == "watchdog" else 8.0
        if now - self.live_fault_started_at > timeout:
            self.live_fault_phase = "failed"
            self.live_fault_detail = "Could not verify the expected board response. Check that the updated firmware is flashed."
            if self.fault_dialog is not None:
                self.fault_dialog.record_event("Test unconfirmed · expected firmware evidence did not arrive")

    def inject_demo_fault(self, kind):
        worker = self.worker
        if (kind not in ("checksum", "mutex", "watchdog") or
                not isinstance(worker, DemoWorker) or not worker.isRunning() or self.paused):
            return False
        if self.demo_fault_phase in ("active", "rebooting", "recovering"):
            return False
        if not worker.inject_fault(kind):
            return False
        now = time.monotonic()
        self.demo_fault_kind, self.demo_fault_phase = kind, "active"
        self.demo_fault_detail = "Simulated command sent; waiting for the fault event."
        self.demo_fault_started_at = now
        self.demo_fault_gap_start = self.last_sample_at if kind == "watchdog" else None
        self.demo_fault_gap_ms = None
        self.demo_fault_packet = self.demo_fault_next_frame = None
        self.demo_fault_hold_at = self.demo_fault_release_at = None
        self.demo_fault_stale_at = self.demo_fault_reboot_at = None
        self.demo_fault_first_return_at = self.demo_fault_recovered_at = None
        self.demo_fault_frame_times = list(self.sample_timestamps)[-12:] if kind in ("mutex", "watchdog") else []
        if kind == "watchdog" and self.session_recorder is not None:
            self.stop_recording("simulated watchdog test started")
        if self.fault_dialog is not None:
            self.fault_dialog.record_event(f"Demo {kind} fault triggered · waiting for simulated evidence")
        self.btn_pause.setEnabled(False)
        if self.fault_dialog is not None:
            self.fault_dialog.refresh()
        self.update_stats(time.monotonic())
        return True

    def on_demo_fault_event(self, source, event):
        if self.worker is not source:
            return
        details = {
            "checksum_detected": "Next simulated packet selected for a one-bit change",
            "checksum_rejected": "Checksum validator rejected the changed packet · graph did not ingest it",
            "mutex_detected": "Simulated status task holds UART mutex · packets pause",
            "mutex_released": "UART mutex released · waiting for next valid packet",
            "watchdog_detected": "Telemetry task stalled · watchdog refresh stopped",
            "watchdog_reset": "Watchdog expired · simulated MCU reboot cleared plots and attitude",
            "watchdog_recovered": "Boot complete · reset reason IWDG · telemetry resumed",
        }
        now = time.monotonic()
        if event == "mutex_detected":
            self.demo_fault_hold_at = now
            self.demo_fault_gap_start = self.last_sample_at
        elif event == "mutex_released":
            self.demo_fault_release_at = now
            self.demo_fault_phase = "recovering"
        elif event == "checksum_rejected":
            self.demo_rejected_frames += 1
            self.demo_fault_phase = "recovering"
        elif event == "watchdog_reset":
            if self.session_recorder is not None:
                self.stop_recording("simulated watchdog reboot")
                self.log_message("SYS >> RECORDING SAVED BEFORE SIMULATED WATCHDOG REBOOT")
            self.demo_fault_phase = "rebooting"
            self.demo_fault_reboot_at = now
            self.demo_fault_stale_at = now
            self.reset_visual_session()
            self.render_frame(force=True)
        elif event == "watchdog_recovered":
            self.demo_fault_phase = "recovering"
            self.demo_reset_reason = "IWDG (demo)"
            self.session_started = time.monotonic()
        if event in details:
            self.demo_fault_detail = details[event]
        if self.fault_dialog is not None and event in details:
            self.fault_dialog.record_event(details[event])
            self.fault_dialog.refresh()
        self.update_stats(time.monotonic())

    def on_demo_rejected_frame(self, source, line):
        if self.worker is source and self.demo_fault_kind == "checksum":
            self.demo_fault_packet = self._bit_flip_evidence(line)
            if self.demo_fault_packet is not None and self.fault_dialog is not None:
                self.fault_dialog.refresh()

    def on_demo_accepted_frame(self, source, line):
        if self.worker is source and self.demo_fault_kind == "checksum":
            sample = parse_telemetry_line(line)
            if sample is not None:
                self.demo_fault_next_frame = {
                    "ax": line.partition("|")[0],
                    "sequence": sample.sequence,
                    "checksum": line.rpartition("|CHK:")[2],
                }

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
        if (health is not None and self.live_fault_kind == "watchdog" and
                self.live_fault_phase in ("armed", "stalled") and
                health.reset_reason == "IWDG"):
            self.reset_visual_session()
            self.live_fault_phase = "recovering"
            self.live_fault_detail = "Boot report confirms IWDG reset. Waiting for a fresh sensor frame."
            self.log_message("SYS >> LIVE WATCHDOG RECOVERY · RESET REASON IWDG CONFIRMED")
            if self.fault_dialog is not None:
                self.fault_dialog.record_event("Firmware rebooted · boot report says Reset: IWDG")
                self.fault_dialog.refresh()
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
        if not self.live_fault_reconnecting:
            self.live_fault_kind = None
            self.live_fault_phase = "idle"
            self.live_fault_detail = ""
            self.live_fault_reconnect_port = None
        self.live_fault_reconnecting = False
        self.demo_fault_kind, self.demo_fault_phase = None, "idle"
        self.demo_rejected_frames = 0
        self.demo_reset_reason = None
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
            worker.fault_marker_received.connect(lambda marker, source=worker: self.on_live_fault_marker(source, marker))
            worker.rejected_frame_received.connect(lambda line, source=worker: self.on_live_rejected_frame(source, line))
            worker.accepted_frame_received.connect(lambda line, source=worker: self.on_live_accepted_frame(source, line))
        elif isinstance(worker, DemoWorker):
            worker.fault_event.connect(lambda event, source=worker: self.on_demo_fault_event(source, event))
            worker.rejected_frame_received.connect(lambda line, source=worker: self.on_demo_rejected_frame(source, line))
            worker.accepted_frame_received.connect(lambda line, source=worker: self.on_demo_accepted_frame(source, line))
        worker.finished.connect(lambda source=worker: self.source_finished(source))
        self.set_stream_controls(True)
        self.set_session_controls()
        self.btn_connect.setText("Disconnect")
        self.lbl_status.setText("CONNECTING")
        worker.start()
        if self.fault_dialog is not None:
            self.fault_dialog.refresh()
        QTimer.singleShot(500, lambda source=worker: self.send_command(
            next(key for key, button in self.rate_buttons.items() if button.isChecked()))
            if self.worker is source and not self.paused else None)

    def toggle_recording(self):
        if self.session_recorder is not None:
            self.stop_recording()
            return
        if self.worker is None or self.is_replay_mode:
            return
        default_name = "telemetry_session.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save telemetry session", default_name, "Telemetry session ZIP (*.zip)"
        )
        if not path:
            return
        if not path.lower().endswith('.zip'):
            path += '.zip'
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
        self.log_message(f"SYS >> RECORDING STARTED · {(self.session_recorder.archive_path or self.session_recorder.csv_path).name}")
        self.set_session_controls()
        return True

    def stop_recording(self, reason="user stopped recording"):
        recorder, self.session_recorder = self.session_recorder, None
        if recorder is None:
            return
        try:
            manifest = recorder.close({"source_end_reason": reason})
            self.log_message(f"SYS >> RECORDING SAVED · {recorder.sample_count} SAMPLES · {manifest.name}")
        except OSError as error:
            self.log_message(f"WARN >> RECORDING FINALIZE FAILED · {error} · RECOVERY FILES: {recorder.csv_path.parent}")
        self.set_session_controls()

    def open_session(self):
        if self.session_recorder is not None:
            return
        picker = SessionImportDialog(self)
        if not picker.exec() or picker.session is None:
            return
        session = picker.session
        self.focus_overlay.restore()
        if self.worker is not None:
            # A slow serial shutdown can finish after the normal wait expires.
            # Defer replay until the old worker's cleanup has completed.
            self.worker.finished.connect(lambda selected=session: QTimer.singleShot(0, lambda: self.start_replay(selected)))
            self.disconnect_source()
        if self.worker is None:
            self.start_replay(session)

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
        if self.focus_overlay.controls is not None:
            label = self.focus_overlay.controls.findChild(QLabel, "focusReplaySpeed")
            if label is not None:
                label.setText(self.lbl_replay_speed.text())
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
        self.set_replay_button_state(not self.paused)
        self.sync_focus_controls()
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
            self.set_replay_button_state(False)
            self.sync_focus_controls()

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
            self.sync_chart_replay_time(preview=target)

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
        if not self.replay_seeking:
            self.sync_chart_replay_time()

    def disconnect_source(self):
        self.live_fault_kind = None
        self.live_fault_phase = "idle"
        self.live_fault_reconnect_port = None
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
        reconnect_watchdog = (isinstance(source, SerialWorker) and source.running and
                              self.live_fault_kind == "watchdog" and
                              self.live_fault_phase in ("sent", "armed", "stalled", "recovering") and
                              time.monotonic() - self.live_fault_started_at < 25.0)
        if reconnect_watchdog:
            self.live_fault_reconnect_port = source.port
            self.live_fault_reboot_at = time.monotonic()
            self.live_fault_detail = "COM port dropped during reset; reconnecting to verify boot reason."
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
        self.demo_fault_kind, self.demo_fault_phase = None, "idle"
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
        if self.fault_dialog is not None:
            self.fault_dialog.refresh()
        if reconnect_watchdog:
            QTimer.singleShot(750, self.reconnect_live_fault)

    def reconnect_live_fault(self):
        if self.worker is not None or self.live_fault_reconnect_port is None:
            return
        if time.monotonic() - self.live_fault_started_at >= 25.0:
            self.live_fault_phase = "failed"
            self.live_fault_detail = "COM port did not return in time; watchdog reset remains unverified."
            return
        port = self.live_fault_reconnect_port
        self.refresh_ports()
        index = self.port_combo.findText(port)
        if index < 0:
            QTimer.singleShot(750, self.reconnect_live_fault)
            return
        self.port_combo.setCurrentIndex(index)
        self.live_fault_reconnecting = True
        self.toggle_connection()

    def send_command(self, cmd):
        if self.is_replay_mode and cmd == 'p':
            self.toggle_replay_playback()
            return
        if self.worker is None or not self.worker.isRunning():
            return
        if cmd == 'p' and self.live_fault_phase in ("sent", "armed", "stalled", "contending", "recovering"):
            self.log_message("SYS >> FINISH LIVE FAULT TEST BEFORE PAUSING TELEMETRY")
            return
        if cmd == 'p' and self.is_demo_mode and self.demo_fault_phase in ("active", "rebooting", "recovering"):
            self.log_message("SYS >> FINISH FAULT RECOVERY BEFORE PAUSING DEMO TELEMETRY")
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
        if self.is_live_serial() and self.live_fault_kind == "watchdog":
            previous_frame = self.live_fault_frame_times[-1] if self.live_fault_frame_times else None
            if (self.live_fault_first_return_at is None and previous_frame is not None and
                    now - previous_frame > .55):
                self.live_fault_gap_start = previous_frame
                self.live_fault_first_return_at = now
                self.live_fault_gap_ms = (now - previous_frame) * 1000
            elif self.live_fault_first_return_at is None and self.live_fault_phase in ("sent", "armed"):
                self.live_fault_gap_start = now
        if (self.is_live_serial() and self.live_fault_kind in ("mutex", "watchdog") and
                now - self.live_fault_started_at < (3.0 if self.live_fault_kind == "mutex" else 25.0) and
                len(self.live_fault_frame_times) < 600):
            self.live_fault_frame_times.append(now)
        if self.is_demo_mode and self.demo_fault_kind in ("mutex", "watchdog"):
            if self.demo_fault_kind == "watchdog" and self.demo_fault_first_return_at is None:
                previous_frame = self.demo_fault_frame_times[-1] if self.demo_fault_frame_times else None
                if previous_frame is not None and now - previous_frame > .55:
                    self.demo_fault_gap_start = previous_frame
                    self.demo_fault_first_return_at = now
                    self.demo_fault_gap_ms = (now - previous_frame) * 1000
            if len(self.demo_fault_frame_times) < 600:
                self.demo_fault_frame_times.append(now)
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
        cutoff = source_time_s - self.plot_window_s
        while self.plot_times and self.plot_times[0] < cutoff:
            self.plot_times.popleft()
            for values in self.data.values():
                values.popleft()
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
        if self.is_live_serial() and self.live_fault_phase == "recovering":
            if self.live_fault_kind == "mutex" and self.live_fault_gap_start is not None:
                gap_ms = (now - self.live_fault_gap_start) * 1000
                self.live_fault_gap_ms = gap_ms
                self.live_fault_detail = f"UART stream resumed after a measured {gap_ms:.0f} ms frame gap."
            elif self.live_fault_kind == "checksum":
                self.live_fault_detail += " Next valid COM3 frame accepted."
            elif self.live_fault_kind == "watchdog":
                self.live_fault_recovered_at = now
                if self.live_fault_gap_ms is None and self.live_fault_gap_start is not None:
                    self.live_fault_gap_ms = (now - self.live_fault_gap_start) * 1000
                self.live_fault_detail = "IWDG reset confirmed; fresh sensor data is streaming again."
            self.live_fault_phase = "recovered"
            if self.fault_dialog is not None:
                self.fault_dialog.record_event(self.live_fault_detail)
                self.fault_dialog.refresh()
        if self.is_demo_mode and self.demo_fault_phase == "recovering":
            if self.demo_fault_kind == "mutex" and self.demo_fault_gap_start is not None:
                self.demo_fault_gap_ms = (now - self.demo_fault_gap_start) * 1000
                self.demo_fault_detail = f"Simulated UART stream resumed after a {self.demo_fault_gap_ms:.0f} ms packet gap."
            elif self.demo_fault_kind == "checksum":
                self.demo_fault_detail = "Changed packet rejected; next valid demo packet reached the graph."
            elif self.demo_fault_kind == "watchdog":
                self.demo_fault_detail = "Simulated IWDG reset confirmed; fresh demo packets are streaming."
            self.demo_fault_recovered_at = now
            self.demo_fault_phase = "recovered"
            self.btn_pause.setEnabled(True)
            self.log_message("SYS >> DEMO VALID TELEMETRY RESUMED · RECOVERY CONFIRMED")
            if self.fault_dialog is not None:
                self.fault_dialog.record_event(self.demo_fault_detail)
                self.fault_dialog.refresh()
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
                self.update_recording_status()
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
        self.update_live_fault(now)
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
            if self.fault_dialog is not None and self.fault_dialog.isVisible():
                self.fault_dialog.refresh()
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
        self.update_recording_status()
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
        if running and self.is_demo_mode and self.demo_fault_phase in ("active", "rebooting"):
            if self.demo_fault_kind == "checksum":
                status, color = "DEGRADED", '#ebc66d'
            elif self.demo_fault_kind == "mutex":
                status, color = "UART WAITING", '#ebc66d'
            elif self.demo_fault_kind == "watchdog":
                status, color = ("REBOOTING" if self.demo_fault_phase == "rebooting" else "TASK STALLED"), '#ebc66d'
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
        health_status = "NOMINAL" if running and fresh and not self.paused else status
        if running and self.is_demo_mode and self.demo_fault_phase in ("active", "rebooting"):
            if self.demo_fault_kind == "checksum":
                health_status = "CHECKSUM FAULT"
            elif self.demo_fault_kind == "mutex":
                health_status = "UART MUTEX HELD"
        elif running and self.is_demo_mode and self.demo_reset_reason:
            health_status = "IWDG RESET (DEMO)"
        self.lbl_health_status.setText(health_status)

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
        if self.fault_dialog is not None:
            self.fault_dialog.close()
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
