"""Optional desktop smoke check: python tests/smoke_dashboard.py [screenshot-dir].

Requires the UI dependencies and a desktop/OpenGL context. Does not connect to
hardware. Core CI remains standard-library-only.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea
from app import DemoWorker, TelemetryDashboard
from session_io import load_session


QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication([])
window = TelemetryDashboard()
window.show()
window.activateWindow()
QTest.qWait(300)
temporary_directory = tempfile.TemporaryDirectory()
try:
    window.btn_calibrate.click()
    dialog = window.calibration_dialog
    assert dialog.isVisible(), "Top toolbar did not open calibration"
    assert not dialog.start_button.isEnabled(), "Calibration was enabled without a live sensor"
    if len(sys.argv) > 1:
        output = Path(sys.argv[1])
        output.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(output / 'calibration-wide.png'))
    dialog.resize(360, 390)
    QTest.qWait(100)
    if len(sys.argv) > 1:
        dialog.grab().save(str(output / 'calibration-narrow.png'))
    dialog_area = dialog.findChild(QScrollArea)
    dialog_scroll = dialog_area.verticalScrollBar()
    assert dialog_scroll.maximum() > 0, "Calibration content could not scroll in a small window"
    dialog.health_toggle.click()
    assert dialog.health_details.parentWidget().isVisible(), "Device-health details did not expand"
    dialog.profile_toggle.click()
    QTest.qWait(30)
    assert dialog_area.widget().width() <= dialog_area.viewport().width(), "Expanded details overflowed narrow dialog"
    dialog.close()
    assert set(window.rail_sections) == {
        'STREAM CONTROL', 'SESSION CAPTURE + REPLAY', '3D ATTITUDE', 'MEMORY TOOLS', 'FIRMWARE TERMINAL'
    }, "Left rail did not create the expected collapsible panes"
    session_pane = window.rail_sections['SESSION CAPTURE + REPLAY']
    session_pane.toggle.click()
    assert not session_pane.content.isVisible(), "Session pane did not collapse"
    session_pane.toggle.click()
    assert session_pane.content.isVisible(), "Session pane did not expand"
    for title in ('3D ATTITUDE', 'MEMORY TOOLS', 'FIRMWARE TERMINAL'):
        pane = window.rail_sections[title]
        pane.toggle.click()
        assert pane.content.isVisible(), f"{title} pane did not expand"
    window.resize(1060, 730)
    QTest.qWait(100)
    rail_scroll = window.rail_scroll.verticalScrollBar()
    assert rail_scroll.maximum() > 0, "Expanded left rail did not become scrollable"
    rail_scroll.setValue(0)
    wheel = QWheelEvent(
        QPointF(4, 4), QPointF(4, 4), QPoint(), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    QApplication.sendEvent(window.btn_clear, wheel)
    assert rail_scroll.value() > 0, "Wheel input over a rail control did not scroll the sidebar"
    rail_scroll.setValue(rail_scroll.maximum())
    assert rail_scroll.value() == rail_scroll.maximum(), "Left rail could not scroll to lower controls"
    for title in ('3D ATTITUDE', 'MEMORY TOOLS', 'FIRMWARE TERMINAL'):
        window.rail_sections[title].toggle.click()
    window.toggle_connection()
    QTest.qWait(850)
    window.btn_calibrate.click()
    assert not dialog.start_button.isEnabled(), "Simulated data was presented as a calibratable device"
    assert dialog.health_state.text() == 'Demo', "Demo data was presented as device health"
    dialog.close()
    assert len(window.plot_times) > 5, "Demo stream did not arrive"
    recording_path = Path(temporary_directory.name) / "smoke-session.csv"
    assert window.start_recording_at_path(recording_path), "Demo recording did not start"
    QTest.qWait(300)
    window.stop_recording()
    recorded_session = load_session(recording_path)
    assert len(recorded_session.samples) >= 5, "Recorded session was unexpectedly short"
    assert recorded_session.metadata["source"] == "demo", "Session metadata lost the data source"
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
    window.start_replay(recorded_session)
    QTest.qWait(15)
    window.btn_calibrate.click()
    assert not dialog.start_button.isEnabled(), "Replay was presented as a live calibration source"
    dialog.close()
    assert window.model_combo.itemText(0) == 'Jet Aircraft' and window.model_combo.currentIndex() == 0
    for model_index in (1, 2, 0):
        window.model_combo.setCurrentIndex(model_index)
        assert window.active_mesh is not None, 'A built-in model failed to load'
    window.rail_sections['SESSION CAPTURE + REPLAY'].toggle.click()
    assert not window.rail_sections['SESSION CAPTURE + REPLAY'].content.isVisible()
    window.rail_sections['SESSION CAPTURE + REPLAY'].toggle.click()
    assert window.btn_step.isEnabled(), "Replay controls did not survive pane collapse/expand"
    window.btn_play_replay.click()
    assert window.paused and 'Play' in window.btn_play_replay.text(), "Replay did not pause"
    for index, speed in enumerate((.25, .5, 1., 2., 4.)):
        window.replay_speed_slider.setValue(index)
        assert window.worker.speed == speed and window.lbl_replay_speed.text() == f'{speed:g}×'
    before_step = len(window.plot_times)
    window.step_replay()
    QTest.qWait(50)
    assert len(window.plot_times) > before_step, "Frame-step did not ingest a replay sample"
    window.replay_slider.setValue(500)
    window.commit_replay_seek()
    assert window.replay_position_s > 0, "Timeline seek did not change replay position"
    assert window.paused, "Seeking changed the replay pause state"
    position_before_play = window.replay_position_s
    window.btn_play_replay.click()
    assert not window.paused and 'Pause' in window.btn_play_replay.text(), "Replay did not resume"
    QTest.qWait(70)
    assert window.replay_position_s > position_before_play, "Replay made no progress after resuming"
    window.replay_speed_slider.setValue(2)
    window.btn_restart_replay.click()
    QTest.qWait(20)
    window.activateWindow()
    window.btn_connect.setFocus()
    QTest.keyClick(window, Qt.Key.Key_P)
    QTest.qWait(20)
    assert window.paused and 'Play' in window.btn_play_replay.text(), "P did not pause replay"
    QTest.keyClick(window, Qt.Key.Key_P)
    QTest.qWait(20)
    assert not window.paused and 'Pause' in window.btn_play_replay.text(), "P did not resume replay"
    window.btn_restart_replay.click()
    assert window.replay_position_s == 0 and not window.paused, "Restart did not play from the beginning"
    window.disconnect_source()
    window.render_frame(force=True)
    assert window.lbl_status.text() == 'OFFLINE'
    assert not window.btn_pause.isEnabled()
    print('PASS: calibration dialog resize/details, collapsible left rail, terminal filters/health, demo record/replay/seek/step, plot/camera focus, pause, shortcuts, memory commands, slow-rate disconnect, offline state')
finally:
    temporary_directory.cleanup()
    window.close()
    app.processEvents()
