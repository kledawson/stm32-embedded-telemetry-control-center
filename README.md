# STM32 Mission Control

A desktop telemetry and digital-twin application for an STM32F401RE and MPU6050. The project demonstrates an end-to-end embedded system: sensor acquisition, FreeRTOS task coordination, DMA UART transport, checksummed framing, persistent crash logging, host-side parsing, sensor fusion, and real-time visualization.

## Highlights

- PyQt6 desktop dashboard with live plots, 3D attitude, force-vector visualization, and acceleration analytics
- Responsive 3D relative-motion car demo driven by filtered linear acceleration
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

The **Motion Car Demo** is deliberately a bounded force-response visualization,
not position tracking. A six-axis MPU6050 cannot separate every linear
acceleration from gravity or provide drift-free position without additional
references, so the car returns toward center when the applied acceleration
stops.

## Run the dashboard

Use Python 3.10 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Select **DEMO — No Hardware** and click **Connect Serial** to exercise the UI without the board. Select the STM32 virtual COM port to use real telemetry; the dashboard automatically requests the 30 ms visual rate after connecting.

## Run tests

The core tests use only Python's standard library:

```powershell
python -m unittest discover -s tests -v
```

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
telemetry.py           Protocol model, checksum, parser, and encoder
kinematics.py          Attitude filter and gravity compensation
tests/                 Deterministic unit tests
requirements.txt       Reproducible host dependencies
ROADMAP.md             Planned portfolio development
```

The STM32CubeIDE project currently lives separately under `OneDrive/Documents/MPU6050_FreeRTOS`.
