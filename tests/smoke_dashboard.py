"""Optional desktop smoke check: python tests/smoke_dashboard.py [screenshot-dir].

Requires the UI dependencies and a desktop/OpenGL context. Does not connect to
hardware. Core CI remains standard-library-only.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from app import DemoWorker, TelemetryDashboard


QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication([])
window = TelemetryDashboard()
window.show()
window.activateWindow()
QTest.qWait(300)
try:
    window.toggle_connection()
    QTest.qWait(850)
    assert len(window.plot_times) > 5, "Demo stream did not arrive"
    assert "SYS" in window.terminal.toPlainText(), "System messages did not reach Firmware Terminal"
    assert "TEL" in window.terminal.toPlainText(), "Telemetry messages did not reach Firmware Terminal"
    assert window.terminal_records, "Terminal did not retain structured messages"
    for category in ('SYS', 'TEL', 'EVT', 'WARN'):
        window.log_message(f"{category} >> FILTER TEST {category}")
        rendered = f"{category}  FILTER TEST {category}"
        assert rendered in window.terminal.toPlainText(), f"{category} message did not render"
        window.terminal_filter_buttons[category].click()
        assert rendered not in window.terminal.toPlainText(), f"{category} filter did not hide its messages"
        assert any(kind == category and text == f"FILTER TEST {category}" for _, kind, text in window.terminal_records), \
            f"{category} filter discarded terminal data"
        window.terminal_filter_buttons[category].click()
        assert rendered in window.terminal.toPlainText(), f"{category} filter did not restore its messages"
    timestamp = window.terminal_records[-1][0]
    assert timestamp in window.terminal.toPlainText(), "Timestamps were not shown by default"
    window.btn_timestamps.click()
    assert timestamp not in window.terminal.toPlainText(), "Timestamp toggle did not hide timestamps"
    window.btn_timestamps.click()
    assert timestamp in window.terminal.toPlainText(), "Timestamp toggle did not restore timestamps"
    for index, (rate, label) in enumerate(window.terminal_rate_options):
        window.terminal_rate_slider.setValue(index)
        assert window.lbl_terminal_rate.text() == label, f"Terminal rate label did not update for {label}"
        assert abs(window.terminal_rate_interval - 1.0 / rate) < 1e-9, f"Terminal rate did not update for {label}"
    window.terminal_rate_slider.setValue(0)
    for index in range(80):
        window.log_message(f"SYS >> SCROLL TEST {index}")
    window.resize(1060, 730)
    QTest.qWait(100)
    scroll = window.terminal.verticalScrollBar()
    scroll.setValue(scroll.maximum() // 2)
    QTest.qWait(25)
    assert not window.terminal_following, "Terminal did not leave live-follow mode when scrolled upward"
    frozen_document = window.terminal.toHtml()
    window.log_message("TEL >> SCROLL POSITION PRESERVED")
    assert scroll.value() < scroll.maximum(), "Terminal snapped to the bottom while reading history"
    assert window.terminal.toHtml() == frozen_document, "Terminal content moved while reading history"
    assert window.btn_resume_terminal.isVisible(), "Resume-live popup was not shown"
    assert window.btn_resume_terminal.y() > window.terminal.viewport().height() // 2, "Resume-live popup was not near the terminal bottom"
    for index in range(1020):
        window.log_message(f"TEL >> CULL TEST {index}")
    assert len(window.terminal_records) == 1000, "Terminal history is not bounded"
    assert window.terminal.toHtml() == frozen_document, "Culling shifted the terminal while reading history"
    window.btn_resume_terminal.click()
    assert scroll.value() == scroll.maximum(), "Resume-live popup did not return to the newest output"
    assert not window.btn_resume_terminal.isVisible(), "Resume-live popup stayed visible at the bottom"
    assert "CULL TEST 1019" in window.terminal.toPlainText(), "Resume-live did not render buffered output"
    window.btn_health.click()
    assert not window.health_panel.isVisible(), "Session Health did not collapse"
    window.btn_health.click()
    assert window.health_panel.isVisible(), "Session Health did not expand"
    terminal_before_expand = window.terminal.toPlainText()
    window.expand_panel(window.terminal_panel)
    QTest.qWait(100)
    assert window.focus_overlay.source is window.terminal_panel, "Firmware Terminal did not expand"
    assert window.terminal.toPlainText() == terminal_before_expand, "Terminal content changed during expansion"
    window.focus_overlay.restore()
    QTest.qWait(100)
    assert window.terminal.toPlainText() == terminal_before_expand, "Terminal content changed after restore"
    # Feed a later deterministic demo frame to make the screenshot informative.
    for i in range(100):
        s = DemoWorker.sample_at(2 + i * .03)
        window.ingest_telemetry(s.ax, s.ay, s.az, s.gx, s.gy, s.gz, .03)
    window.render_frame(force=True)
    QTest.qWait(150)
    # The active plane / satellite / STL must rotate from real gyro channels:
    # X -> pitch, Y -> roll, Z -> yaw. No autonomous translation is involved.
    window.reset_yaw()
    for _ in range(8):
        window.ingest_telemetry(0.0, 0.0, 1.0, 60.0, -45.0, 30.0, .03)
    window.render_frame(force=True)
    motion = window.latest_motion
    assert motion is not None
    assert motion.pitch > 5.0, "Gyro X did not rotate pitch"
    assert motion.roll < -3.0, "Gyro Y did not rotate roll"
    assert motion.yaw > 5.0, "Gyro Z did not rotate yaw"
    assert "Pitch" in window.lbl_angles.text(), "Gyro attitude did not reach the active 3D model"
    plot = window.accel_graph
    plot.setXRange(.5, 1.5, padding=0)
    plot.setYRange(-1, 2, padding=0)
    before = plot.viewRange()
    camera_before = dict(window.view_3d.cameraParams())
    window.expand_panel(window.accel_panel)
    QTest.qWait(150)
    assert window.focus_overlay.source is window.accel_panel
    assert window.accel_panel.content is plot
    assert plot.viewRange() == before, "Expanded plot lost manual range"
    window.focus_overlay.restore()
    QTest.qWait(100)
    assert plot.viewRange() == before, "Restored plot lost manual range"
    window.expand_panel(window.model_panel)
    QTest.qWait(150)
    assert window.view_3d.cameraParams() == camera_before, "Camera reset on focus"
    if len(sys.argv) > 1:
        output = Path(sys.argv[1])
        output.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(output / 'console-expanded.png'))
    window.focus_overlay.restore()
    window.send_command('p')
    QTest.qWait(100)
    count = len(window.plot_times)
    QTest.qWait(100)
    assert len(window.plot_times) == count, "Paused stream still changes plots"
    window.activateWindow()
    window.btn_connect.setFocus()
    QTest.keyClick(window, Qt.Key.Key_R)
    QTest.qWait(100)
    assert window.latest_motion is None, "R shortcut did not reset orientation"
    if len(sys.argv) > 1:
        window.grab().save(str(output / 'console-dashboard.png'))
    window.send_command('p')
    window.send_command('s')
    QTest.qWait(100)
    assert window.requested_interval == 2
    window.btn_dump.click()
    window.btn_clear.click()
    assert "COMMAND SENT · D" in window.terminal.toPlainText(), "Snapshot command did not reach terminal"
    assert "COMMAND SENT · C" in window.terminal.toPlainText(), "Erase command did not reach terminal"
    started = time.monotonic()
    window.disconnect_source()
    assert time.monotonic() - started < 1.5, "Slow demo would not stop promptly"
    window.render_frame(force=True)
    assert window.lbl_status.text() == 'OFFLINE'
    assert not window.btn_pause.isEnabled()
    print('PASS: terminal filters/health, demo, plot/camera focus preservation, pause, R shortcut, memory commands, slow-rate disconnect, offline state')
finally:
    window.close()
    app.processEvents()
