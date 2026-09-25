import sys, time, math, serial
import serial.tools.list_ports
import numpy as np
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QComboBox, QTextEdit, 
                             QGroupBox, QGridLayout, QSplitter, QLabel, QFrame,
                             QTabWidget, QFileDialog, QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from stl import mesh as stl_mesh

from kinematics import AttitudeEstimator
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
            self.log_received.emit(f"SYS >> Connected to {self.port}")
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
                            self.log_received.emit(f"WARN >> Rejected {self.invalid_frames} invalid telemetry frame(s)")
                    else:
                        self.log_received.emit(line)
                time.sleep(0.001)
        except Exception as e: self.log_received.emit(f"ERR >> Serial: {e}")

    def send_cmd(self, cmd: str):
        if self.ser and self.ser.is_open:
            self.ser.write(cmd.encode('utf-8'))
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
        self.started_at = time.monotonic()

    def run(self):
        self.log_received.emit("SYS >> Demo mode active (simulated MPU6050)")
        previous = time.monotonic()
        while self.running:
            now = time.monotonic()
            if not self.paused:
                t = now - self.started_at
                pitch = math.radians(15.0 * math.sin(0.60 * t))
                roll = math.radians(20.0 * math.sin(0.45 * t))
                pulse = 0.28 * math.sin(2.4 * t) if int(t) % 6 < 2 else 0.0
                ax = -math.sin(roll) * math.cos(pitch) + pulse
                ay = math.sin(pitch) + 0.12 * math.cos(1.8 * t)
                az = math.cos(roll) * math.cos(pitch)
                gx = 9.0 * math.cos(0.60 * t)
                gy = 9.0 * math.cos(0.45 * t)
                gz = 18.0
                self.data_received.emit(ax, ay, az, gx, gy, gz, now - previous)
                previous = now
            self.msleep(max(1, int(self.interval * 1000)))

    def send_cmd(self, cmd: str):
        rates = {'v': 0.03, 'f': 0.5, 'n': 1.0, 's': 2.0}
        if cmd in rates:
            self.interval = rates[cmd]
            self.log_received.emit(f"DEMO >> Sampling interval set to {int(self.interval * 1000)} ms")
        elif cmd == 'p':
            self.paused = not self.paused
            self.log_received.emit(f"DEMO >> Telemetry {'paused' if self.paused else 'resumed'}")
        elif cmd in ('c', 'd'):
            self.log_received.emit(f"DEMO >> Flash command '{cmd}' simulated")

    def stop(self):
        self.running = False

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

# --- MAIN DASHBOARD WINDOW ---
class TelemetryDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Telemetry & Kinematics Control Center")
        self.resize(1400, 880)
        self.setStyleSheet("background-color: #0F0F13; color: #E0E0E0; font-family: 'Segoe UI', sans-serif;")
        
        self.worker, self.max_points, self.button_map, self.peak_g = None, 150, {}, 1.00
        self.pitch, self.roll, self.yaw = 0.0, 0.0, 0.0
        self.estimator = AttitudeEstimator()
        self.active_mesh = None
        self.last_model_index = 0
        self.data = {'ax': [], 'ay': [], 'az': [], 'gx': [], 'gy': [], 'gz': []}
        
        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # --- LEFT PANEL (Controls) ---
        left_widget = QWidget()
        left_panel = QVBoxLayout(left_widget)
        left_panel.setContentsMargins(0, 0, 10, 0)
        left_panel.setSpacing(10)
        
        groupbox_style = "QGroupBox { border: 1px solid #333340; border-radius: 6px; margin-top: 20px; padding-top: 10px; font-weight: bold; } QGroupBox::title { subcontrol-origin: margin; left: 10px; top: 2px; padding: 0 4px; background: #0F0F13; color: #00E5FF; }"
        btn_style = "QPushButton { background: #22222A; color: #E0E0E0; padding: 8px; border: 1px solid #3A3A45; border-radius: 4px; font-weight: 600; } QPushButton:hover { background: #2D2D38; border-color: #00E5FF; } QPushButton:pressed { background: #15151C; border-color: #2962FF; }"
        
        # 1. Hardware Connection
        conn_group = QGroupBox("1. Hardware Connection")
        conn_group.setStyleSheet(groupbox_style)
        conn_layout = QVBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setStyleSheet("padding: 6px; background: #1C1C22; border: 1px solid #444; color: white;")
        self.refresh_ports()
        
        self.btn_connect = QPushButton("Connect Serial")
        self.btn_connect.setStyleSheet("QPushButton { background: #2962FF; color: white; padding: 10px; font-weight: bold; border-radius: 4px; } QPushButton:hover { background: #448AFF; }")
        self.btn_connect.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.port_combo); conn_layout.addWidget(self.btn_connect)
        conn_group.setLayout(conn_layout); left_panel.addWidget(conn_group)
        
        # 2. Commands
        ctrl_group = QGroupBox("2. Telemetry Rate")
        ctrl_group.setStyleSheet(groupbox_style)
        ctrl_layout = QGridLayout()
        
        self.btn_vis = QPushButton("🚀 33 FPS (30ms) ('v')")
        self.btn_fast = QPushButton("⚡ Fast (500ms) ('f')")
        self.btn_norm = QPushButton("⏱ Norm (1000ms) ('n')")
        self.btn_slow = QPushButton("🐢 Slow (2000ms) ('s')")
        self.btn_pause = QPushButton("⏸ Pause/Resume ('p')")
        
        for btn in [self.btn_vis, self.btn_fast, self.btn_norm, self.btn_slow, self.btn_pause]: 
            btn.setStyleSheet(btn_style)
            
        self.btn_vis.setStyleSheet("QPushButton { background: #00838F; color: white; padding: 8px; border: 1px solid #00ACC1; border-radius: 4px; font-weight: bold; } QPushButton:hover { background: #00ACC1; border-color: #00E5FF; } QPushButton:pressed { background: #006064; }")
        
        self.btn_vis.clicked.connect(lambda: self.send_command('v'))
        self.btn_fast.clicked.connect(lambda: self.send_command('f'))
        self.btn_norm.clicked.connect(lambda: self.send_command('n'))
        self.btn_slow.clicked.connect(lambda: self.send_command('s'))
        self.btn_pause.clicked.connect(lambda: self.send_command('p'))
        
        self.button_map.update({'v': self.btn_vis, 'f': self.btn_fast, 'n': self.btn_norm, 's': self.btn_slow, 'p': self.btn_pause})
        
        ctrl_layout.addWidget(self.btn_vis, 0, 0, 1, 2)
        ctrl_layout.addWidget(self.btn_fast, 1, 0); ctrl_layout.addWidget(self.btn_norm, 1, 1)
        ctrl_layout.addWidget(self.btn_slow, 2, 0); ctrl_layout.addWidget(self.btn_pause, 2, 1)
        ctrl_group.setLayout(ctrl_layout); left_panel.addWidget(ctrl_group)
        
        # 3. Flash Memory
        flash_group = QGroupBox("3. Non-Volatile Flash")
        flash_group.setStyleSheet(groupbox_style)
        flash_layout = QVBoxLayout()
        self.btn_dump = QPushButton("📥 Dump Snapshot ('d')")
        self.btn_dump.setStyleSheet("QPushButton { background: #E65100; color: white; padding: 8px; font-weight: bold; border-radius: 4px; } QPushButton:hover { background: #FF9800; }")
        self.btn_dump.clicked.connect(lambda: self.send_command('d'))
        
        self.btn_clear = QPushButton("🗑 Erase Sector 5 ('c')")
        self.btn_clear.setStyleSheet("QPushButton { background: #C62828; color: white; padding: 8px; font-weight: bold; border-radius: 4px; } QPushButton:hover { background: #F44336; }")
        self.btn_clear.clicked.connect(lambda: self.send_command('c'))
        
        self.button_map.update({'d': self.btn_dump, 'c': self.btn_clear})
        flash_layout.addWidget(self.btn_dump); flash_layout.addWidget(self.btn_clear)
        flash_group.setLayout(flash_layout); left_panel.addWidget(flash_group)
        
        # 4. Simulation Target
        sim_group = QGroupBox("4. 3D Digital Twin Target")
        sim_group.setStyleSheet(groupbox_style)
        sim_layout = QVBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.addItems(["Satellite / Spacecraft", "Jet Aircraft", "CubeSat Enclosure", "Load Custom STL…"])
        self.model_combo.setStyleSheet("padding: 6px; background: #1C1C22; border: 1px solid #444; color: white;")
        self.model_combo.currentIndexChanged.connect(self.change_3d_model)
        
        self.btn_reset_yaw = QPushButton("Zero Orientation ('r')")
        self.btn_reset_yaw.setStyleSheet(btn_style)
        self.btn_reset_yaw.clicked.connect(self.reset_yaw)
        self.button_map['r'] = self.btn_reset_yaw
        
        sim_layout.addWidget(self.model_combo); sim_layout.addWidget(self.btn_reset_yaw)
        sim_group.setLayout(sim_layout); left_panel.addWidget(sim_group)
        
        left_panel.addStretch()
        splitter.addWidget(left_widget)
        
        # --- RIGHT PANEL (Tabs) ---
        right_widget = QWidget()
        right_panel = QVBoxLayout(right_widget)
        right_panel.setContentsMargins(0, 0, 0, 0)
        
        stats_frame = QFrame()
        stats_frame.setStyleSheet("background-color: #18181E; border-radius: 6px; border: 1px solid #2A2A35;")
        stats_layout = QHBoxLayout(stats_frame)
        self.lbl_status = QLabel("OFFLINE")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #888; background: #222; padding: 4px 12px; border-radius: 8px;")
        self.lbl_current_g = QLabel("0.00 g")
        self.lbl_current_g.setStyleSheet("font-size: 20px; font-weight: bold; color: #00E5FF;")
        self.lbl_max_g = QLabel("0.00 g")
        self.lbl_max_g.setStyleSheet("font-size: 20px; font-weight: bold; color: #FF5252;")
        
        stats_layout.addWidget(self.lbl_status); stats_layout.addStretch()
        stats_layout.addWidget(QLabel("NET G:")); stats_layout.addWidget(self.lbl_current_g); stats_layout.addSpacing(15)
        stats_layout.addWidget(QLabel("PEAK:")); stats_layout.addWidget(self.lbl_max_g)
        right_panel.addWidget(stats_frame)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #333; } QTabBar::tab { background: #1E1E24; color: #888; padding: 8px 16px; } QTabBar::tab:selected { background: #2962FF; color: white; font-weight: bold; }")
        
        # TAB 1: GRAPHS
        tab_graphs = QWidget()
        graphs_layout = QVBoxLayout(tab_graphs)
        pg.setConfigOptions(antialias=True)
        self.accel_graph = pg.PlotWidget(title="Acceleration (g)"); self.accel_graph.setYRange(-3.0, 3.0); self.accel_graph.addLegend()
        self.curve_ax = self.accel_graph.plot(pen=pg.mkPen('#00E5FF', width=2), name="X")
        self.curve_ay = self.accel_graph.plot(pen=pg.mkPen('#E040FB', width=2), name="Y")
        self.curve_az = self.accel_graph.plot(pen=pg.mkPen('#FFEA00', width=2), name="Z")
        
        self.gyro_graph = pg.PlotWidget(title="Rotational Velocity (°/s)"); self.gyro_graph.setYRange(-300.0, 300.0); self.gyro_graph.addLegend()
        self.curve_gx = self.gyro_graph.plot(pen=pg.mkPen('#00E5FF', width=2), name="X")
        self.curve_gy = self.gyro_graph.plot(pen=pg.mkPen('#E040FB', width=2), name="Y")
        self.curve_gz = self.gyro_graph.plot(pen=pg.mkPen('#FFEA00', width=2), name="Z")
        
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(
            "QTextEdit { background-color: #08080A; color: #00E676; "
            "font-family: Consolas; padding: 8px; border: 1px solid #2A2A35; }"
        )
        self.terminal.viewport().setStyleSheet("background-color: #08080A;")
        
        graph_splitter = QSplitter(Qt.Orientation.Vertical)
        graph_splitter.addWidget(self.accel_graph); graph_splitter.addWidget(self.gyro_graph); graph_splitter.addWidget(self.terminal)
        graphs_layout.addWidget(graph_splitter)
        
        # TAB 2: 3D DIGITAL TWIN
        tab_3d = QWidget()
        layout_3d = QVBoxLayout(tab_3d); layout_3d.setContentsMargins(0, 0, 0, 0)
        self.view_3d = gl.GLViewWidget()
        self.view_3d.setCameraPosition(distance=25, elevation=30, azimuth=45)
        self.view_3d.setBackgroundColor('#0A0A0E')
        
        grid = gl.GLGridItem(); grid.setSize(x=50, y=50, z=0); grid.setSpacing(x=5, y=5, z=0); grid.translate(0, 0, -4)
        self.view_3d.addItem(grid)
        self.triad = gl.GLAxisItem(); self.triad.setSize(x=6, y=6, z=6); self.view_3d.addItem(self.triad)
        
        self.force_vector = gl.GLLinePlotItem(pos=np.array([[0,0,0], [0,0,0]]), color=(0.0, 1.0, 1.0, 1.0), width=8, antialias=True)
        self.view_3d.addItem(self.force_vector)
        
        self.lbl_angles = QLabel("Pitch: 0.0° | Roll: 0.0° | Yaw: 0.0°", self.view_3d)
        self.lbl_angles.setStyleSheet("color: #00E5FF; font-size: 15px; font-weight: bold; background: rgba(15,15,20,220); padding: 6px 12px; border-radius: 4px;")
        self.lbl_angles.move(15, 15)
        layout_3d.addWidget(self.view_3d)

        # TAB 3: DEDICATED ACCELERATION VISUALIZER
        tab_accel = QWidget()
        layout_accel = QVBoxLayout(tab_accel)
        
        # 1. 3-Axis Dynamic Bar Chart
        self.bar_accel = pg.PlotWidget(title="Real-Time Linear Acceleration Components (g)")
        self.bar_accel.setYRange(-2.5, 2.5)
        self.bar_accel.setXRange(-0.5, 2.5)
        self.bar_accel.getAxis('bottom').setTicks([[(0, 'Lin-X (Pitch)'), (1, 'Lin-Y (Roll)'), (2, 'Lin-Z (Heave)')]])
        self.bars = pg.BarGraphItem(x=[0, 1, 2], height=[0, 0, 0], width=0.5, brushes=['#00E5FF', '#E040FB', '#FFEA00'])
        self.bar_accel.addItem(self.bars)
        
        # 2. Planar Thrust Vector Scatter Plot
        self.scatter_plot = pg.PlotWidget(title="2D Planar Vector Thrust Path (X-Y Direction)")
        self.scatter_plot.setXRange(-2.0, 2.0)
        self.scatter_plot.setYRange(-2.0, 2.0)
        self.scatter_plot.showGrid(x=True, y=True, alpha=0.3)
        self.trail_line = self.scatter_plot.plot(pen=pg.mkPen('#00E5FF', width=2))
        self.scatter_point = self.scatter_plot.plot(pen=None, symbol='o', symbolSize=15, symbolBrush='#FFEA00')
        self.trail_x, self.trail_y = [], []
        
        accel_splitter = QSplitter(Qt.Orientation.Horizontal)
        accel_splitter.addWidget(self.bar_accel)
        accel_splitter.addWidget(self.scatter_plot)
        layout_accel.addWidget(accel_splitter)
        
        self.tabs.addTab(tab_graphs, "Telemetry Stream")
        self.tabs.addTab(tab_3d, "3D Digital Twin")
        self.tabs.addTab(tab_accel, "Acceleration Analytics")
        
        right_panel.addWidget(self.tabs)
        splitter.addWidget(right_widget)
        splitter.setSizes([300, 1050])
        self.change_3d_model(0)

    # --- LOGIC & MESH HANDLERS ---
    def change_3d_model(self, index):
        new_mesh = None
        if index == 0:
            new_mesh = create_satellite_mesh()
        elif index == 1:
            new_mesh = create_aircraft_mesh()
        elif index == 2:
            new_mesh = create_cubesat_mesh()
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Load Digital Twin STL", "", "STL Models (*.stl)")
            if not path:
                self.model_combo.blockSignals(True)
                self.model_combo.setCurrentIndex(self.last_model_index)
                self.model_combo.blockSignals(False)
                return
            try:
                new_mesh = create_stl_mesh(path)
            except Exception as error:
                QMessageBox.warning(self, "STL Load Failed", str(error))
                self.model_combo.blockSignals(True)
                self.model_combo.setCurrentIndex(self.last_model_index)
                self.model_combo.blockSignals(False)
                return

        if self.active_mesh is not None:
            self.view_3d.removeItem(self.active_mesh)
        self.active_mesh = new_mesh
        self.last_model_index = index
        
        self.view_3d.addItem(self.active_mesh)
        self.view_3d.update()

    def reset_yaw(self):
        self.estimator.reset()
        self.yaw, self.pitch, self.roll = 0.0, 0.0, 0.0
        self.log_message("SYS >> Orientation Zeroed.")

    def keyPressEvent(self, event):
        key = event.text().lower()
        if key in self.button_map:
            if key == 'r': self.reset_yaw()
            else: self.send_command(key)
            self.animate_button_press(self.button_map[key])
            
    def animate_button_press(self, btn):
        orig = btn.styleSheet()
        btn.setStyleSheet(orig + " QPushButton { border: 2px solid #00E5FF; background: #333345; }")
        QTimer.singleShot(150, lambda: btn.setStyleSheet(orig))

    def refresh_ports(self):
        self.port_combo.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo.addItem("DEMO — No Hardware")
        if ports:
            self.port_combo.addItems(ports)

    def toggle_connection(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop(); self.worker.wait(1000); self.worker = None
            self.btn_connect.setText("Connect Serial")
            self.lbl_status.setText("OFFLINE"); self.lbl_status.setStyleSheet("color: #888; background: #222;")
        else:
            port = self.port_combo.currentText()
            self.worker = DemoWorker() if port.startswith("DEMO") else SerialWorker(port)
            self.worker.data_received.connect(self.process_telemetry)
            self.worker.log_received.connect(self.log_message)
            self.worker.start()
            self.btn_connect.setText("Disconnect")
            self.lbl_status.setText("SIMULATION" if port.startswith("DEMO") else "CONNECTING")
            self.lbl_status.setStyleSheet("color: white; background: #00838F;")
            QTimer.singleShot(500, lambda: self.send_command('v'))

    def send_command(self, cmd):
        if self.worker and self.worker.isRunning(): self.worker.send_cmd(cmd)

    def process_telemetry(self, ax, ay, az, gx, gy, gz, dt):
        self.data['ax'].append(ax); self.data['ay'].append(ay); self.data['az'].append(az)
        self.data['gx'].append(gx); self.data['gy'].append(gy); self.data['gz'].append(gz)
        for k in self.data:
            if len(self.data[k]) > self.max_points: self.data[k].pop(0)
                
        self.curve_ax.setData(self.data['ax']); self.curve_ay.setData(self.data['ay']); self.curve_az.setData(self.data['az'])
        self.curve_gx.setData(self.data['gx']); self.curve_gy.setData(self.data['gy']); self.curve_gz.setData(self.data['gz'])
        
        motion = self.estimator.update(TelemetrySample(ax, ay, az, gx, gy, gz), dt)
        self.pitch, self.roll, self.yaw = motion.pitch, motion.roll, motion.yaw

        # 3D Matrix Rotation
        transform = pg.Transform3D()
        transform.rotate(self.yaw, 0, 0, 1)
        transform.rotate(self.pitch, 1, 0, 0)
        transform.rotate(self.roll, 0, 1, 0)
        
        if self.active_mesh is not None: self.active_mesh.setTransform(transform)
        self.triad.setTransform(transform)
        
        # Linear acceleration after orientation-aware body-frame gravity removal
        lin_ax, lin_ay, lin_az = motion.linear_ax, motion.linear_ay, motion.linear_az
        
        # Update 3D Force Vector Arrow
        vector_end = np.array([[-lin_ax * 10.0, lin_ay * 10.0, lin_az * 10.0]])
        self.force_vector.setData(pos=np.array([[0,0,0], vector_end[0]]))
        self.lbl_angles.setText(f"Pitch: {self.pitch:.1f}° | Roll: {self.roll:.1f}° | Yaw: {self.yaw:.1f}°")
        
        # TAB 3 UPDATES: Bar Chart & Planar Scatter
        self.bars.setOpts(height=[lin_ax, lin_ay, lin_az])
        
        self.trail_x.append(lin_ax)
        self.trail_y.append(lin_ay)
        if len(self.trail_x) > 30:
            self.trail_x.pop(0)
            self.trail_y.pop(0)
        self.trail_line.setData(self.trail_x, self.trail_y)
        self.scatter_point.setData([lin_ax], [lin_ay])
        
        # Stats Cards
        net_g = (ax**2 + ay**2 + az**2) ** 0.5
        self.lbl_current_g.setText(f"{net_g:.2f} g")
        if net_g > self.peak_g: self.peak_g = net_g; self.lbl_max_g.setText(f"{self.peak_g:.2f} g")
            
        if net_g > 1.5:
            self.lbl_status.setText("CRASH DETECTED"); self.lbl_status.setStyleSheet("color: white; background: #D50000;")
        elif net_g > 1.15 or net_g < 0.85:
            self.lbl_status.setText("ACTIVE MOTION"); self.lbl_status.setStyleSheet("color: black; background: #FFD600;")
        else:
            self.lbl_status.setText("NOMINAL"); self.lbl_status.setStyleSheet("color: white; background: #00C853;")

    def log_message(self, msg):
        self.terminal.append(msg)
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop(); self.worker.wait(1000)
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = TelemetryDashboard()
    window.show()
    sys.exit(app.exec())

