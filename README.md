# STM32 Telemetry Console

A desktop telemetry and digital-twin application for an STM32F401RE and MPU6050. The project demonstrates an end-to-end embedded system: sensor acquisition, FreeRTOS task coordination, DMA UART transport, checksummed framing, persistent crash logging, host-side parsing, sensor fusion, and real-time visualization.

## Highlights

- Single-screen PyQt6 dashboard with live acceleration/gyro plots, 3D attitude, and a compact motion-event summary
- Expand either plot or the 3D view over a dimmed dashboard; the same live widget retains its zoom, camera, legends, and interactions
- Persistent serial controls, stream-rate shortcuts, model selection, orientation reset, Flash commands, and a scrollable device log
- Collapsible, independently expandable left-rail panes with vertical scrolling when several control categories are open
- Hardware-free demo mode for development, interviews, screenshots, and screen recordings
- Durable CSV/JSON session recording of validated samples, derived attitude, and event markers
- Guided gyro and six-face accelerometer calibration with local profile storage and live correction
- Device-health view with checksum errors, sequence-based loss, receive jitter, throughput, reset cause, and FreeRTOS stack margins
- Time-accurate replay at 0.25×, 0.5×, 1×, 2×, or 4× with Play/Pause, single-frame step, deterministic seek, and restart
- Jet aircraft by default, with an orbital satellite and quadcopter drone as additional attitude models
- XOR-validated ASCII telemetry protocol with compatibility for legacy spaced frames
- Complementary pitch/roll filter, gyro deadband, bounded integration time, and orientation-aware gravity compensation
- FreeRTOS telemetry and command tasks with ISR-to-queue command delivery and mutex-protected DMA UART output
- Independent watchdog and a persistent crash snapshot in reserved STM32 Flash Sector 5
- Unit-tested protocol and kinematics modules

## Architecture

```text
MPU6050 --I2C--> STM32F401RE / FreeRTOS
                         |
                 checksummed UART
                         |
      SerialWorker / DemoWorker / ReplayWorker
                         |
       parser --> attitude estimator --> PyQt6 UI
                         |
       recorder --> CSV + JSON manifest
            plots / 3D twin / analytics
```

`DemoWorker` and `ReplayWorker` feed the same dashboard pipeline as `SerialWorker`, so the complete UI can run without physical hardware or replay recorded field data.

**Motion Events** summarizes measured activity: stationary, rotating, active
motion, shaking, and impact. Impacts use a 2 g threshold with hysteresis and a
cooldown so one hit produces one event. The latest peak, transient sensor axis,
gyro magnitude, and three recent events are visible beside the 3D model.

These are demonstration heuristics, not calibrated safety or damage limits.
“Stationary” means quiet sensor readings; an IMU cannot distinguish rest from
constant velocity. “Measured |a|” includes gravity (approximately 1 g at rest).
The transient axis comes from sample-to-sample acceleration changes, not a
world direction. Slow streams can miss short impacts. The 3D view's cyan vector
remains an approximate gravity-compensated signal. The selected 3D model—built-in
mesh or custom STL—rotates only from incoming gyro data: X affects pitch, Y
affects roll, and Z affects yaw. It does not contain a simulated position or
car-motion layer.

## Run the dashboard

Use Python 3.10 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Select **DEMO — No Hardware** and click **Connect** to exercise the UI without the board; this source intentionally animates the model with generated gyro data. Select the STM32 virtual COM port to use real telemetry; the dashboard requests the 30 ms visual rate after connecting.

## Calibrate and inspect device health

Connect a physical sensor, then select **Calibrate** in the top bar. Select **Start calibration**, leave the board still for the gyro check, then set each of its six faces upward in any order. Each face captures automatically after a stable hold. The progress tiles show which orientations are complete. Select **Save profile** after all six faces pass validation. Keep the live stream running and stop any recording before starting; the guide requests the 30 ms rate if needed.

The profile is saved in the user's local application-data directory (`STM32TelemetryConsole/calibrations.json`) and applied to live dashboard processing and later recordings. It does not write coefficients to the STM32. New firmware reports its unique device ID so the correct profile reloads on reconnection; older firmware falls back to the serial port name. A recording retains the calibration ID captured when recording starts. Replays already contain processed samples and are not calibrated a second time.

The same dialog shows the four most useful link metrics at a glance. Expand **Technical details** for validated/malformed frame counts, reset cause, task stack high-water marks, command drops, and device ID. Older firmware remains usable, but sequence-based loss and firmware-only fields display as unavailable until the updated firmware is flashed.

## Record and replay a session

1. Connect either Demo or the physical board, then select **Start recording**.
2. Choose a `.csv` destination. The app saves that data file and a matching `.json` manifest beside it when you select **Stop & save recording** or disconnect.
3. Select **Open session for replay…** while disconnected. Use **Play/Pause** (or **P**) to control playback, **Step one frame** for a single sample, **Restart** to play from the beginning, the timeline to seek, and the snapping speed slider to choose 0.25×, 0.5×, 1×, 2×, or 4×.

Starting a recording establishes a fresh attitude/event-processing baseline, and its first stored timestamp is always `0.0 s`; this keeps each capture self-contained and makes replay state reproducible. The CSV stores IMU readings after any active host calibration, receive timestamps, calculated attitude and linear acceleration, motion state, and event markers. The JSON manifest stores format version, source, requested rate, application and firmware metadata, calibration identifier, and sample count. Seek rebuilds the attitude/event state from the beginning through the selected sample, so its resulting view matches linear playback instead of applying a stateless jump.

Keyboard controls remain available throughout the window, including expanded views:

- **V / F / N / S:** 33 Hz / 2 Hz / 1 Hz / 0.5 Hz stream rate.
- **P:** pause/resume the current live stream or replay. **R:** zero the orientation estimate.
- **D:** dump the Flash snapshot. **C:** erase/re-arm Flash Sector 5.
- **Escape:** return from an expanded view.

Drag or wheel within plots to pan/zoom; the native pyqtgraph context menu and
export tools remain available in either layout. Use its auto-range control to
resume following incoming data after manual zoom. Drag the 3D view to orbit and
wheel to zoom. Splitter handles resize the model/events, graphs, and device log.

## Run tests

The core tests use only Python's standard library:

```powershell
python -m unittest discover -s tests -v
```

An optional desktop check exercises demo streaming, session record/replay/seek/frame-step, focus/restore, camera and
plot-range preservation, pause, the R shortcut, and disconnect at slow rates:

```powershell
python tests/smoke_dashboard.py
```

It requires the UI dependencies and a working desktop/OpenGL context. It never
connects to physical hardware.

With a connected board, run `python tests/live_serial_smoke.py COM3 --seconds 7 --gyro-check`
to inspect the raw stream and test gyro stillness without saving a profile. Run
`python tests/live_dashboard_smoke.py COM3` to check the desktop connection,
calibration dialog, live health values, and disconnect behavior. These checks
open and close the port, so close any other serial monitor first.

## Serial protocol

Current firmware packets use this compact form:

```text
AX:0.01|AY:-0.02|AZ:1.00|GX:0.1|GY:0.2|GZ:-0.3|SEQ:42|CHK:0xNN
```

`CHK` is the 8-bit XOR of every character before `|CHK:`. `SEQ` increments for each transmitted sensor frame, allowing the desktop app to estimate packet loss. The parser also accepts older packets without `SEQ` and the legacy form containing spaces around delimiters. The five-second `[SYS STATUS]` line also carries reset reason, both task stack high-water marks, and a 96-bit device ID.

Commands are single ASCII characters: `v` (30 ms), `f` (500 ms), `n` (1000 ms), `s` (2000 ms), `p` (pause/resume and sensor sleep/wake), `d` (dump crash snapshot), and `c` (erase/re-arm crash logging).

## Repository layout

```text
app.py                 PyQt6 UI and hardware/demo workers
calibration.py         Stable-sample calibration, profile validation, and storage
calibration_ui.py      Compact guided calibration and diagnostics dialog
dashboard_ui.py        Panel styling and focus overlay
motion_events.py       Measured motion/impact heuristics
session_io.py          Versioned CSV/JSON session format, recorder, and validator
telemetry.py           Protocol model, checksum, parser, and encoder
kinematics.py          Attitude filter and gravity compensation
tests/                 Deterministic unit tests
requirements.txt       Reproducible host dependencies
firmware/              STM32CubeIDE project, FreeRTOS tasks, HAL, and MPU6050 code
ROADMAP.md             Planned portfolio development
```

The firmware tree is versioned in this repository under [`firmware/`](firmware/README.md). Generated `Debug/` and `Release/` outputs are intentionally excluded.
