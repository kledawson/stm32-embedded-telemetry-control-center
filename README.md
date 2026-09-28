# STM32 Telemetry Console

A desktop view for an STM32F401RE and MPU6050 motion sensor. The STM32 reads the sensor and streams data over USB serial; the Python app plots it, rotates a 3D model, and can record and replay a session.

## What it does

- Shows live acceleration, rotation, motion events, and a 3D aircraft. Demo mode works without hardware.
- Records sessions to CSV with a JSON manifest, then replays them with seek, step, and speed controls.
- Guides gyro and six-face accelerometer calibration. Profiles are saved on the PC for each board.
- Shows connection health: checksum errors, packet loss, timing, throughput, reset cause, and FreeRTOS stack margin.
- Runs STM32 firmware with FreeRTOS tasks, DMA UART output, a watchdog, and a Flash crash snapshot.

## Run the app

Use Python 3.10 or newer on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Choose **DEMO — No Hardware** and **Connect** to explore the dashboard. For live data, build and flash the [firmware](firmware/README.md), connect the Nucleo board, select its COM port, and connect. The app requests the 30 ms visual stream rate.

## Main workflows

**Calibrate:** With live telemetry running and recording stopped, select **Calibrate → Start calibration**. Hold the board comfortably and pause its rotation briefly for the gyro check. Then hold each of its six faces upward until its tile fills. Small hand tremors are okay. Select **Save profile** after all six faces pass. Calibration is applied in the app, not written to the STM32.

**Record and replay:** Select **Start recording**, choose a CSV path, then **Stop & save recording**. The matching JSON manifest is saved beside it. Disconnect, choose **Open session for replay…**, and use Play/Pause, the timeline, single-frame step, and the snapping speed slider. Replay uses the recorded corrected samples without applying calibration again.

**Check health:** Open **Calibrate** to see checksum errors, packet loss, jitter, and throughput. Expand **Technical details** for reset cause, task stack margin, command drops, and the board ID. Older firmware can still stream data but may not provide every health field.

The live shortcuts are **V/F/N/S** for stream rates, **P** for pause, **R** to zero the orientation estimate, and **D** to dump the Flash snapshot. **C** erases and re-arms the crash log in Flash Sector 5. **Escape** closes an expanded plot or 3D view.

## Test and navigate the code

```powershell
python -m unittest discover -s tests -v
python tests/smoke_dashboard.py
```

The first command runs hardware-free unit tests. The second opens the desktop app and needs its UI dependencies and OpenGL context. With a board connected and its port free, `python tests/live_serial_smoke.py COM3 --seconds 7 --gyro-check` checks the serial stream; `python tests/live_dashboard_smoke.py COM3` checks the live UI. Replace `COM3` with your port.

- `app.py`: main window and controls
- `sources.py`: serial, demo, and replay workers
- `models.py`: built-in 3D models and STL loading
- `telemetry.py`, `kinematics.py`, `motion_events.py`: parsing and sensor processing
- `calibration.py`, `calibration_ui.py`, `session_io.py`: calibration and saved sessions
- `firmware/`: STM32CubeIDE project and [firmware build notes](firmware/README.md)

The motion labels are demonstration heuristics, not safety measurements. An MPU6050 estimates attitude and linear acceleration, but it cannot provide drift-free position or absolute yaw.

MIT licensed. See [LICENSE](LICENSE).
