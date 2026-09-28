"""Live desktop fault check: python tests/live_fault_dashboard_smoke.py COM3."""

from pathlib import Path
import os
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app import TelemetryDashboard
from session_io import load_session


def wait_until(condition, timeout_s=10):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        QTest.qWait(30)
    return bool(condition())


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    application = QApplication([])
    window = TelemetryDashboard()
    window.show()
    capture_dir = Path(os.environ["FAULT_SCREENSHOT_DIR"]) if os.getenv("FAULT_SCREENSHOT_DIR") else None
    if capture_dir:
        capture_dir.mkdir(parents=True, exist_ok=True)
    try:
        index = window.port_combo.findText(port)
        assert index >= 0, f"{port} is not listed"
        window.port_combo.setCurrentIndex(index)
        window.toggle_connection()
        assert wait_until(lambda: window.is_live_serial() and window.serial_packets_seen > 15)
        window.btn_fault_lab.click()
        lab = window.fault_dialog
        available = lab.screen().availableGeometry()
        assert lab.width() <= available.width() and lab.height() <= available.height()
        assert (lab.width(), lab.height()) == (min(1140, max(1, available.width() - 48)),
                                              min(770, max(1, available.height() - 48)))
        assert lab.watchdog_gap.tick_step_s == 1.0
        if capture_dir:
            assert lab.grab().save(str(capture_dir / "fault-default-size.png"))
        assert lab.inject_button.isEnabled() and lab.scenario_buttons["mutex"].isVisible()
        assert set(lab.scenario_buttons) == {"checksum", "mutex", "watchdog"}
        assert all(not button.text()[:1].isdigit() for button in lab.scenario_buttons.values())
        lab.resize(520, 500)
        QTest.qWait(80)
        assert lab.scroll.widget().width() <= lab.scroll.viewport().width(), "Live fault view overflowed a narrow window"
        lab.resize(1040, 710)
        scroll = lab.scroll.verticalScrollBar()
        scroll.setValue(min(45, scroll.maximum()))
        starting_scroll = scroll.value()

        checksum_started = time.monotonic()
        lab.inject_button.click()
        assert lab.presentation_stage == 0
        QTest.qWait(80)
        assert abs(scroll.value() - starting_scroll) <= 1, "Trigger jumped the scroll position"
        assert wait_until(lambda: window.live_fault_phase == "recovered", 6), (
            window.live_fault_phase, window.live_fault_detail)
        assert wait_until(lambda: lab.presentation_stage == 1, 3), "Checksum evidence never paused on screen"
        assert lab.proof_card.isVisible() and not lab.graph_card.isVisible()
        assert lab.proof_card.width() >= 500, "Packet view is too cramped at the default window size"
        assert lab.checksum_visual.isVisible() and not lab.mutex_timeline.isVisible()
        assert lab.checksum_visual.packet is window.live_fault_packet
        initial_progress = lab.checksum_visual.progress
        QTest.qWait(200)
        assert lab.checksum_visual.progress > initial_progress, "Checksum animation did not advance"
        packet = window.live_fault_packet
        assert packet["before_ax"] != packet["after_ax"]
        assert int(packet["sent_checksum"], 16) ^ int(packet["calculated_checksum"], 16) == 1
        lab.resize(520, 500)
        QTest.qWait(80)
        assert lab.scroll.widget().width() <= lab.scroll.viewport().width(), (
            f"Captured packet overflowed the narrow view: content={lab.scroll.widget().width()} "
            f"viewport={lab.scroll.viewport().width()} proof={lab.proof_card.minimumSizeHint().width()}")
        assert lab.checksum_visual.minimumHeight() >= 400, "Narrow packet flow did not stack vertically"
        if capture_dir:
            lab.scroll.verticalScrollBar().setValue(
                lab.proof_card.mapTo(lab.scroll.widget(), lab.proof_card.rect().topLeft()).y())
            QTest.qWait(40)
            assert lab.grab().save(str(capture_dir / "checksum-narrow.png"))
        lab.resize(1040, 710)
        assert wait_until(lambda: lab.presentation_stage == 2, 4)
        assert window.live_fault_next_frame is not None
        assert lab.checksum_visual.stage == 2
        assert lab.checksum_visual.next_frame is window.live_fault_next_frame
        assert lab.proof_status.text() == "NEXT FRAME ACCEPTED"
        if capture_dir:
            assert lab.grab().save(str(capture_dir / "checksum-flow.png"))
        assert time.monotonic() - checksum_started >= 2.1, "Checksum stages still flashed too quickly"
        assert window.worker.checksum_failures >= 1
        assert "Parser rejected" in "\n".join(lab.events.toPlainText().splitlines())
        assert "next valid" in window.live_fault_detail.lower()
        print("UI checksum:", window.live_fault_detail)

        lab.scenario_buttons["mutex"].click()
        QTest.qWait(70)
        mutex_started = time.monotonic()
        mutex_scroll = scroll.value()
        lab.inject_button.click()
        QTest.qWait(60)
        assert abs(scroll.value() - mutex_scroll) <= 1, (
            f"UART trigger jumped the scroll position: before={mutex_scroll} after={scroll.value()} max={scroll.maximum()}")
        assert lab.mutex_timeline.isVisible() and lab.mutex_timeline.stage == 0
        assert wait_until(lambda: window.live_fault_hold_at is not None, 3)
        assert lab.mutex_timeline.hold_at is not None, "Live HOLD marker did not reach the timeline"
        first_tick = lab.mutex_timeline.now
        QTest.qWait(90)
        assert lab.mutex_timeline.now > first_tick, "UART timeline did not animate during the real event"
        assert lab.mutex_timeline.stage == 0, "UART live capture ended before the hold was visible"
        if capture_dir:
            assert lab.grab().save(str(capture_dir / "uart-live.png"))
        assert wait_until(lambda: window.live_fault_phase == "recovered", 6), (
            window.live_fault_phase, window.live_fault_detail)
        assert wait_until(lambda: lab.presentation_stage == 1, 3)
        assert lab.mutex_timeline.stage == 2, "UART event animated a second time after capture"
        first_frames = lab.mutex_timeline.frames
        QTest.qWait(180)
        assert lab.mutex_timeline.stage == 2 and lab.mutex_timeline.frames == first_frames
        if capture_dir:
            assert lab.grab().save(str(capture_dir / "uart-animation.png"))
        assert lab.proof_card.isVisible() and lab.mutex_timeline.isVisible()
        assert lab.mutex_timeline.width() >= 500, "UART timeline is too cramped at the default window size"
        assert not lab.graph_card.isVisible()
        assert not lab.checksum_visual.isVisible()
        assert window.live_fault_hold_at is not None and window.live_fault_release_at is not None
        assert window.live_fault_hold_at < window.live_fault_release_at
        assert any(stamp < window.live_fault_hold_at for stamp in window.live_fault_frame_times)
        assert any(stamp > window.live_fault_release_at for stamp in window.live_fault_frame_times)
        lab.resize(520, 500)
        QTest.qWait(80)
        assert lab.scroll.widget().width() <= lab.scroll.viewport().width(), "Mutex timeline overflowed a narrow window"
        lab.resize(1040, 710)
        assert wait_until(lambda: lab.presentation_stage == 2, 4)
        assert lab.mutex_metric_values["gap"].text() == f"{window.live_fault_gap_ms:.0f} ms"
        expected_hold = (window.live_fault_release_at - window.live_fault_hold_at) * 1000
        assert lab.mutex_metric_values["hold"].text() == f"{expected_hold:.0f} ms"
        assert lab.mutex_metric_values["gap"].isVisible() and lab.mutex_metric_values["hold"].isVisible()
        assert lab.mutex_metric_values["gap"].font().pixelSize() == lab.mutex_metric_values["hold"].font().pixelSize(), (
            "Packet gap and mutex held readouts have different sizes")
        if capture_dir:
            assert lab.grab().save(str(capture_dir / "uart-timeline.png"))
        assert time.monotonic() - mutex_started >= 2.1, "Mutex stages still flashed too quickly"
        assert "measured" in window.live_fault_detail
        print("UI mutex:", window.live_fault_detail)

        with tempfile.TemporaryDirectory() as temp:
            recorded = Path(temp) / "before-watchdog.csv"
            assert window.start_recording_at_path(recorded)
            assert wait_until(lambda: len(window.plot_times) > 10)
            lab.scenario_buttons["watchdog"].click()
            assert lab.graph_card.isVisible() and not lab.proof_card.isVisible()
            assert lab.watchdog_visual.isVisible()
            assert lab.watchdog_gap.isVisible() and not lab.proof_card.isVisible()
            watchdog_scroll = scroll.value()
            lab.inject_button.click()
            first_elapsed = lab.watchdog_visual.elapsed
            QTest.qWait(100)
            assert abs(scroll.value() - watchdog_scroll) <= 1, "Watchdog trigger jumped the scroll position"
            assert lab.watchdog_visual.elapsed > first_elapsed, "Watchdog activity visual did not animate"
            assert lab.watchdog_visual.phase in ("sent", "armed", "stalled")
            assert window.session_recorder is None, "Watchdog test did not close the recording"
            assert wait_until(lambda: window.live_fault_phase == "recovered", 25), (
                window.live_fault_phase, window.live_fault_detail)
            assert window.firmware_health is not None and window.firmware_health.reset_reason == "IWDG"
            assert lab.watchdog_visual.phase == "recovered"
            assert lab.watchdog_visual.reset_reason == "IWDG"
            assert lab.watchdog_gap.recovered_at is not None
            assert lab.watchdog_gap.first_return_at is not None
            assert lab.watchdog_gap.last_before < lab.watchdog_gap.first_return_at <= lab.watchdog_gap.recovered_at
            assert any(stamp < lab.watchdog_gap.last_before for stamp in lab.watchdog_gap.frames)
            assert any(stamp >= lab.watchdog_gap.first_return_at for stamp in lab.watchdog_gap.frames)
            assert not any(lab.watchdog_gap.last_before < stamp < lab.watchdog_gap.first_return_at
                           for stamp in lab.watchdog_gap.frames), "Watchdog gap includes valid packets"
            assert window.live_fault_gap_ms > 500
            assert window.serial_packets_seen > 0 and window.last_sample_at is not None
            QTest.qWait(300)
            if capture_dir:
                assert lab.grab().save(str(capture_dir / "watchdog-sequence.png"))
            # The ready view must not restart a perpetual feed dot after the
            # completed watchdog run is no longer the active fault.
            window.live_fault_kind = None
            lab.refresh()
            assert lab.watchdog_visual.phase == "idle"
            still_frame = lab.watchdog_visual.grab().toImage()
            QTest.qWait(180)
            lab.refresh()
            assert still_frame == lab.watchdog_visual.grab().toImage(), (
                "Idle watchdog illustration is still animating after a fault")
            window.live_fault_kind = "watchdog"
            lab.refresh()
            print("UI watchdog:", window.live_fault_detail)
            lab.resize(520, 500)
            QTest.qWait(80)
            assert lab.scroll.widget().width() <= lab.scroll.viewport().width(), "Watchdog view overflowed a narrow window"
            assert lab.watchdog_visual.minimumHeight() >= 340
            if capture_dir:
                lab.scroll.verticalScrollBar().setValue(
                    lab.graph_card.mapTo(lab.scroll.widget(), lab.graph_card.rect().topLeft()).y())
                QTest.qWait(40)
                assert lab.grab().save(str(capture_dir / "watchdog-narrow.png"))
            lab.resize(1040, 710)
            session = load_session(recorded)
            assert session.samples, "Pre-reset recording was empty"

            window.disconnect_source()
        for message, expected in (("command sent", "#55b7cd"),
                                  ("frame rejected", "#e5af73"),
                                  ("frame accepted", "#79d7a6")):
            lab.record_event(message)
            formats = lab.events.document().lastBlock().textFormats()
            assert formats[-1].format.foreground().color().name() == expected
        window.start_replay(session)
        QTest.qWait(100)
        assert not lab.inject_button.isEnabled(), "Fault button was active during replay"
        window.disconnect_source()
        print("PASS: live Fault Injection UI, real checksum/mutex/IWDG evidence, recording and replay isolation")
    finally:
        window.close()
        application.processEvents()


if __name__ == "__main__":
    main()
