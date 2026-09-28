"""Desktop integration check for the three shared demo/live fault visuals."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app import TelemetryDashboard
from session_io import load_session
from sources import DemoWorker


def wait_until(condition, timeout_s=8.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        QTest.qWait(30)
    return bool(condition())


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication([])
    window = TelemetryDashboard()
    window.show()
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
    try:
        window.btn_fault_lab.click()
        lab = window.fault_dialog
        assert lab.start_button.isVisible() and not lab.inject_button.isEnabled()
        assert lab.proof_card.isVisible() and not lab.graph_card.isVisible()
        lab.start_button.click()
        assert wait_until(lambda: isinstance(window.worker, DemoWorker) and len(window.plot_times) > 10)
        assert {key for key, button in lab.scenario_buttons.items() if button.isVisible()} == {
            "checksum", "mutex", "watchdog"}
        assert lab.inject_button.text() == "Run demo fault"
        assert lab.proof_card.isVisible() and not lab.graph_card.isVisible()

        packets = []
        for _ in range(2):
            assert wait_until(lab.inject_button.isEnabled)
            lab.inject_button.click()
            assert wait_until(lambda: window.demo_fault_phase == "recovered" and
                              window.demo_fault_packet is not None and
                              window.demo_fault_next_frame is not None)
            packet = window.demo_fault_packet
            packets.append(packet)
            assert packet["before_ax"] != packet["after_ax"]
            assert int(packet["sent_checksum"], 16) ^ int(packet["calculated_checksum"], 16) == 1
            assert lab.checksum_visual.packet is packet
            assert wait_until(lambda: lab.presentation_stage == 2, 5)
            assert lab.checksum_visual.next_frame is window.demo_fault_next_frame
            if output is not None:
                assert lab.grab().save(str(output / "demo-checksum.png"))
        assert packets[0] != packets[1], "Repeated demo packets were identical"
        assert window.worker.checksum_failures == 2

        lab.scenario_buttons["mutex"].click()
        assert lab.mutex_timeline.isVisible() and lab.mutex_metrics.isVisible()
        lab.inject_button.click()
        assert wait_until(lambda: window.demo_fault_hold_at is not None)
        assert wait_until(lambda: window.demo_fault_phase == "recovered" and
                          window.demo_fault_gap_ms is not None)
        assert window.demo_fault_release_at > window.demo_fault_hold_at
        assert window.demo_fault_gap_ms > 200
        assert any(stamp < window.demo_fault_hold_at for stamp in window.demo_fault_frame_times)
        assert any(stamp >= window.demo_fault_release_at for stamp in window.demo_fault_frame_times), (
            window.demo_fault_phase, window.demo_fault_release_at,
            window.demo_fault_frame_times[-3:])
        assert wait_until(lambda: lab.presentation_stage == 2, 5)
        assert lab.mutex_metric_values["gap"].text().endswith(" ms")
        assert lab.mutex_metric_values["hold"].text().endswith(" ms")
        if output is not None:
            assert lab.grab().save(str(output / "demo-mutex.png"))

        with tempfile.TemporaryDirectory() as temporary:
            recording = Path(temporary) / "before-watchdog.csv"
            assert window.start_recording_at_path(recording)
            lab.scenario_buttons["watchdog"].click()
            assert lab.graph_card.isVisible() and lab.watchdog_gap.isVisible()
            assert wait_until(lab.inject_button.isEnabled), "Demo stream did not resume after recording reset"
            lab.inject_button.click()
            assert window.demo_fault_kind == "watchdog", (window.demo_fault_kind, window.demo_fault_phase)
            assert wait_until(lambda: window.demo_fault_phase == "rebooting", 5), (
                window.demo_fault_phase, window.worker.fault_state)
            assert not window.plot_times and window.latest_motion is None
            assert window.session_recorder is None
            assert wait_until(lambda: window.demo_fault_phase == "recovered", 6)
            assert window.demo_reset_reason == "IWDG (demo)"
            assert window.demo_fault_first_return_at is not None
            assert window.demo_fault_gap_ms > 500
            assert not any(window.demo_fault_gap_start < stamp < window.demo_fault_first_return_at
                           for stamp in window.demo_fault_frame_times)
            assert lab.watchdog_visual.phase == "recovered"
            assert lab.watchdog_visual.demo and lab.watchdog_gap.demo
            assert wait_until(lambda: lab.presentation_stage == 2, 4)
            if output is not None:
                assert lab.grab().save(str(output / "demo-watchdog.png"))

            lab.resize(520, 500)
            QTest.qWait(80)
            assert lab.scroll.widget().width() <= lab.scroll.viewport().width()
            assert lab.scroll.verticalScrollBar().maximum() > 0
            lab.resize(1040, 900)
            QTest.qWait(80)
            assert lab.events_card.height() <= 180

            saved = load_session(recording)
            assert saved.samples
            window.disconnect_source()
            window.start_replay(saved)
            QTest.qWait(80)
            assert not lab.inject_button.isEnabled()
            window.disconnect_source()
        print("PASS: shared demo fault visuals, varied checksum packets, mutex timing, watchdog recovery, resize, replay isolation")
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
