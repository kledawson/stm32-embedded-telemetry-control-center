# STM32 Telemetry Console

A desktop telemetry and digital-twin application for an STM32F401RE and MPU6050. The project demonstrates an end-to-end embedded system: sensor acquisition, FreeRTOS task coordination, DMA UART transport, checksummed framing, persistent crash logging, host-side parsing, sensor fusion, and real-time visualization.

## Highlights

- Single-screen PyQt6 dashboard with live acceleration/gyro plots, 3D attitude, and a compact motion-event summary
- Expand either plot or the 3D view over a dimmed dashboard; the same live widget retains its zoom, camera, legends, and interactions
- Persistent serial controls, stream-rate shortcuts, model selection, orientation reset, Flash commands, and a scrollable device log
- Hardware-free demo mode for development, interviews, screenshots, and screen recordings
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
              SerialWorker (QThread)
                         |
       parser --> attitude estimator --> PyQt6 UI
                         |
            plots / 3D twin / analytics
```

`DemoWorker` implements the same signal interface as `SerialWorker`, so the complete UI can run without physical hardware.

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

Keyboard controls remain available throughout the window, including expanded views:

- **V / F / N / S:** 33 Hz / 2 Hz / 1 Hz / 0.5 Hz stream rate.
- **P:** pause/resume the stream. **R:** zero the orientation estimate.
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

An optional desktop check exercises demo streaming, focus/restore, camera and
plot-range preservation, pause, the R shortcut, and disconnect at slow rates:

```powershell
python tests/smoke_dashboard.py
```

It requires the UI dependencies and a working desktop/OpenGL context. It never
connects to physical hardware.

## Serial protocol

Current firmware packets use this compact form:

```text
AX:0.01|AY:-0.02|AZ:1.00|GX:0.1|GY:0.2|GZ:-0.3|CHK:0xNN
```

`CHK` is the 8-bit XOR of every character before `|CHK:`. The parser also accepts the older form containing spaces around delimiters.

Commands are single ASCII characters: `v` (30 ms), `f` (500 ms), `n` (1000 ms), `s` (2000 ms), `p` (pause/resume and sensor sleep/wake), `d` (dump crash snapshot), and `c` (erase/re-arm crash logging).

## Repository layout

```text
app.py                 PyQt6 UI and hardware/demo workers
dashboard_ui.py        Panel styling and focus overlay
motion_events.py       Measured motion/impact heuristics
telemetry.py           Protocol model, checksum, parser, and encoder
kinematics.py          Attitude filter and gravity compensation
tests/                 Deterministic unit tests
requirements.txt       Reproducible host dependencies
firmware/              STM32CubeIDE project, FreeRTOS tasks, HAL, and MPU6050 code
ROADMAP.md             Planned portfolio development
```

The firmware tree is versioned in this repository under [`firmware/`](firmware/README.md). Generated `Debug/` and `Release/` outputs are intentionally excluded.
