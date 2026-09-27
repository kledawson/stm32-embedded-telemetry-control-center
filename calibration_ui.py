"""Compact, resizable calibration and device-health dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from calibration import FACES


class CalibrationDialog(QDialog):
    def __init__(self, dashboard):
        super().__init__(dashboard)
        self.dashboard = dashboard
        self.setWindowTitle("Calibration & device health")
        self.resize(720, 680)
        self.setMinimumSize(360, 390)
        self.setStyleSheet("""
            QDialog, QScrollArea, QWidget#calRoot { background: #0b0e14; color: #edf3fa; }
            QFrame#calCard { background: #151c28; border: 1px solid #2c3748; border-radius: 9px; }
            QLabel#calTitle { font-size: 20px; font-weight: 650; }
            QLabel#calStep { font-size: 16px; font-weight: 600; }
            QLabel#calSubtle { color: #a9bbcb; }
            QLabel#calFace { background: #202d3b; border: 1px solid #354457; border-radius: 7px;
                padding: 12px; font-weight: 600; }
            QLabel#calFaceDone { background: #1d453c; border: 1px solid #378768; border-radius: 7px;
                padding: 12px; font-weight: 600; }
            QScrollArea { border: none; }
            QToolButton { color: #a9bbcb; border: none; padding: 5px 2px; text-align: left; }
            QProgressBar { background: #273342; border: none; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #55b7cd; border-radius: 4px; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("calRoot")
        scroll.setWidget(body)
        column = QVBoxLayout(body)
        column.setContentsMargins(6, 4, 12, 8)
        column.setSpacing(12)

        title = QLabel("Calibrate your sensor")
        title.setObjectName("calTitle")
        column.addWidget(title)
        self.source_label = QLabel()
        self.source_label.setObjectName("calSubtle")
        column.addWidget(self.source_label)

        guide = self._card(column)
        self.step_label = QLabel()
        self.step_label.setObjectName("calStep")
        guide.addWidget(self.step_label)
        self.instruction = QLabel()
        self.instruction.setWordWrap(True)
        guide.addWidget(self.instruction)
        self.progress = QProgressBar()
        self.progress.setRange(0, 700)
        self.progress.setTextVisible(False)
        guide.addWidget(self.progress)
        self.face_grid = QGridLayout()
        self.face_grid.setSpacing(6)
        self.face_labels = {}
        for index, face in enumerate(FACES):
            label = QLabel(face)
            label.setObjectName("calFace")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.face_grid.addWidget(label, index // 3, index % 3)
            self.face_labels[face] = label
        guide.addLayout(self.face_grid)
        self.feedback = QLabel()
        self.feedback.setObjectName("calSubtle")
        self.feedback.setWordWrap(True)
        guide.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start calibration")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.dashboard.start_calibration)
        buttons.addWidget(self.start_button)
        self.save_button = QPushButton("Save profile")
        self.save_button.clicked.connect(self.dashboard.save_calibration)
        buttons.addWidget(self.save_button)
        self.reset_button = QPushButton("Start over")
        self.reset_button.clicked.connect(self.dashboard.restart_calibration)
        buttons.addWidget(self.reset_button)
        guide.addLayout(buttons)

        health = self._card(column)
        head = QHBoxLayout()
        heading = QLabel("Device health")
        heading.setObjectName("calStep")
        head.addWidget(heading)
        head.addStretch()
        self.health_state = QLabel("Offline")
        head.addWidget(self.health_state)
        health.addLayout(head)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.summary_labels = {}
        for index, (key, caption) in enumerate((
            ("checksum", "Checksum errors"), ("loss", "Packet loss"),
            ("jitter", "p95 jitter"), ("rate", "Throughput"))):
            cell = QFrame()
            cell.setObjectName("calCard")
            box = QVBoxLayout(cell)
            box.setContentsMargins(10, 8, 10, 8)
            name = QLabel(caption)
            name.setObjectName("calSubtle")
            box.addWidget(name)
            value = QLabel("—")
            value.setObjectName("calStep")
            box.addWidget(value)
            self.summary_labels[key] = value
            grid.addWidget(cell, index // 2, index % 2)
        health.addLayout(grid)
        self.health_toggle, self.health_details = self._disclosure(health, "Technical details")
        self.detail_labels = {}
        for key, caption in (
            ("frames", "Validated frames"), ("missing", "Missing sequence numbers"),
            ("malformed", "Malformed frames"), ("reset", "Last reset reason"),
            ("telemetry_stack", "Telemetry stack minimum free"),
            ("status_stack", "Status stack minimum free"),
            ("command_drop", "Firmware command drops"), ("identity", "Device identity")):
            row = QHBoxLayout()
            name = QLabel(caption)
            name.setObjectName("calSubtle")
            row.addWidget(name)
            row.addStretch()
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(value)
            self.detail_labels[key] = value
            self.health_details.addLayout(row)
        self.health_note = QLabel()
        self.health_note.setObjectName("calSubtle")
        self.health_note.setWordWrap(True)
        self.health_details.addWidget(self.health_note)

        profile_card = self._card(column)
        self.profile_toggle, self.profile_details = self._disclosure(profile_card, "Saved profile & coefficients")
        self.profile_text = QLabel()
        self.profile_text.setWordWrap(True)
        self.profile_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.profile_details.addWidget(self.profile_text)
        column.addStretch()
        self.refresh()

    @staticmethod
    def _card(column):
        card = QFrame()
        card.setObjectName("calCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        column.addWidget(card)
        return box

    @staticmethod
    def _disclosure(parent, title):
        toggle = QToolButton()
        toggle.setText("▸  " + title)
        toggle.setCheckable(True)
        parent.addWidget(toggle)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 0)
        layout.setSpacing(6)
        parent.addWidget(content)
        content.hide()
        toggle.toggled.connect(lambda open_: (content.setVisible(open_),
            toggle.setText(("▾  " if open_ else "▸  ") + title)))
        return toggle, layout

    def refresh(self):
        dashboard = self.dashboard
        wizard = dashboard.calibration_wizard
        live = dashboard.is_live_serial()
        active = live and not dashboard.paused and dashboard.session_recorder is None
        source = dashboard.port_combo.currentText() if live else (
            "Replay" if dashboard.is_replay_mode else "Demo" if dashboard.is_demo_mode else "No device connected")
        self.source_label.setText(source + (" · Live sensor" if live else " · Connect a serial sensor to calibrate"))
        stages = {
            "idle": ("Ready when you are", "Hold the board comfortably. Small hand movements are okay; pause briefly when prompted."),
            "gyro": ("1 / 2 · Brief pause", "Hold it comfortably and pause movement while the short gyro check fills."),
            "faces": ("2 / 2 · Turn the board", "Hold each face upward and pause briefly while its tile fills. Small tremors are okay."),
            "ready": ("Ready to save", "Your six faces passed the checks. Save to use these corrections in the dashboard."),
        }
        title, instruction = stages[wizard.stage]
        self.step_label.setText(title)
        self.instruction.setText(instruction)
        feedback = ("Stop recording before calibration." if dashboard.session_recorder else
                    "Resume telemetry to calibrate." if live and dashboard.paused else wizard.status)
        self.feedback.setText(feedback)
        completed = (1 if wizard.gyro_bias else 0) + len(wizard.faces)
        self.progress.setValue(round(100 * (completed + wizard.hold_fraction())))
        self.start_button.setText("Recalibrate" if dashboard.current_profile else "Start calibration")
        for face, label in self.face_labels.items():
            label.setVisible(wizard.stage in ("faces", "ready"))
            label.setObjectName("calFaceDone" if face in wizard.faces else "calFace")
            label.style().unpolish(label)
            label.style().polish(label)
        self.start_button.setVisible(wizard.stage == "idle")
        self.start_button.setEnabled(active)
        self.save_button.setVisible(wizard.stage == "ready")
        self.save_button.setEnabled(active)
        self.reset_button.setVisible(wizard.stage != "idle")
        self.reset_button.setEnabled(active)

        metrics = dashboard.device_health_snapshot()
        self.health_state.setText(metrics["state"])
        for key in self.summary_labels:
            self.summary_labels[key].setText(metrics[key])
        for key in self.detail_labels:
            self.detail_labels[key].setText(metrics[key])
        self.health_note.setText(metrics["note"])
        profile = dashboard.current_profile
        if profile is None:
            self.profile_text.setText("No saved calibration is active for this device.")
        else:
            vector = lambda values: " / ".join(f"{value:+.4f}" for value in values)
            self.profile_text.setText(
                f"{profile.id} · saved {profile.created_at}\n"
                f"{profile.sensor_model} · {profile.faces_completed} faces · {profile.protocol}\n"
                f"Gyro bias (°/s): {vector(profile.gyro_bias)}\n"
                f"Accel offset (g): {vector(profile.accel_offset)}\n"
                f"Accel scale: {vector(profile.accel_scale)}\n"
                f"Device key: {profile.device_key}\n"
                "Applied to live dashboard values and new recordings. Stored on this PC."
            )

    def closeEvent(self, event):
        if self.dashboard.calibration_wizard.stage in ("gyro", "faces", "ready"):
            self.dashboard.calibration_wizard.cancel("Calibration stopped. Your saved profile is unchanged.")
        super().closeEvent(event)
