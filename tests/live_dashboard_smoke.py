"""Live desktop check: python tests/live_dashboard_smoke.py COM3."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app import TelemetryDashboard


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    application = QApplication([])
    window = TelemetryDashboard()
    window.show()
    try:
        index = window.port_combo.findText(port)
        assert index >= 0, f"{port} is not in the port picker"
        window.port_combo.setCurrentIndex(index)
        window.toggle_connection()
        QTest.qWait(2300)
        assert window.is_live_serial(), "Desktop serial worker did not connect"
        assert window.serial_packets_seen >= 30, "Desktop did not receive live frames"
        window.btn_calibrate.click()
        dialog = window.calibration_dialog
        assert dialog.isVisible() and dialog.start_button.isEnabled(), "Calibration did not enable on COM3"
        assert dialog.summary_labels["rate"].text() != "—", "Live throughput did not reach the dialog"
        assert dialog.summary_labels["checksum"].text() == "0", "Unexpected checksum failures"
        dialog.start_button.click()
        # A person may move the board during a live smoke check; the wizard
        # should stay active and continue asking for stillness rather than
        # making a flaky pass/fail hinge on physical positioning.
        QTest.qWait(5000)
        print("Calibration stage:", window.calibration_wizard.stage,
              "faces:", sorted(window.calibration_wizard.faces),
              "status:", window.calibration_wizard.status)
        assert window.calibration_wizard.stage in ("gyro", "faces", "ready"), "Calibration wizard stopped unexpectedly"
        if window.firmware_health is None:
            assert dialog.detail_labels["reset"].text() == "—", "Old firmware reset reason was fabricated"
            assert dialog.summary_labels["loss"].text() == "—", "Old firmware loss was fabricated"
            print("Firmware protocol: legacy (extended health unavailable)")
        else:
            assert dialog.detail_labels["reset"].text() != "—", "Reset cause did not reach the dialog"
            assert dialog.detail_labels["telemetry_stack"].text() != "—", "Task stack did not reach the dialog"
            assert dialog.summary_labels["loss"].text() != "—", "Sequence-based loss did not reach the dialog"
            telemetry_stack = int(dialog.detail_labels["telemetry_stack"].text().split()[0])
            assert telemetry_stack >= 32, "Telemetry stack margin is below the dashboard warning threshold"
            print("Firmware protocol: extended health; UI metrics:",
                  {name: label.text() for name, label in dialog.summary_labels.items()},
                  "health:", dialog.health_state.text(),
                  "reset:", dialog.detail_labels["reset"].text(),
                  "stacks:", dialog.detail_labels["telemetry_stack"].text(),
                  dialog.detail_labels["status_stack"].text())
        window.disconnect_source()
        QTest.qWait(100)
        assert window.calibration_wizard.stage == "idle", "Disconnect did not cancel calibration"
        print(f"PASS: {port} live dashboard, calibration stillness, and health display")
    finally:
        window.close()
        application.processEvents()


if __name__ == "__main__":
    main()
