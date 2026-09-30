# STM32 Telemetry Console

An end-to-end motion telemetry project for the **Nucleo STM32F401RE** and **MPU6050**. FreeRTOS firmware streams checksummed sensor data to a Python console for plots, 3D attitude, recording, and fault diagnosis. **Demo mode runs without hardware.**

![Desktop dashboard connected to the live sensor on COM3](docs/media/dashboard-live.png)

*Live COM3 telemetry during board movement: 30-second plots, 3D attitude, and motion events.*

## Download and set up

Install Python 3.10 or newer. Clone the repository, or use **Code → Download ZIP** on GitHub and extract the complete archive. Keep the folder structure below intact, then open PowerShell in the repository root (the folder containing `app.py`):

```powershell
git clone https://github.com/kledawson/stm32-embedded-telemetry-control-center.git
cd stm32-embedded-telemetry-control-center
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

For the ZIP option, skip the `git clone` and `cd` lines; open PowerShell in the extracted project folder and run the remaining commands. The `.venv` folder is created locally for Python dependencies.

To update a Git clone later, run `git pull origin main` from the repository root. For a ZIP install, download and extract the latest ZIP again.

```text
project-root/                              ← run app.py from this folder
├── app.py                                 ← desktop app entry point
├── calibration.py, *_ui.py, ...           ← Python modules imported by app.py
├── requirements.txt                       ← Python packages
├── firmware/                              ← .ioc project, Core, Drivers, Middleware
├── docs/media/                            ← README screenshots and diagrams
├── tests/                                 ← unit and hardware smoke checks
└── .venv/                                 ← created on your computer by setup
```

## Try the demo

Select **DEMO — No Hardware → Connect**. No board is required. Open **Fault Injection** and run **Bit flip + checksum** to see a bad packet rejected and the next valid one accepted. UART contention and watchdog recovery are also available. Simulated evidence is labeled **Demo**.

## Connect a real board

You need a Nucleo STM32F401RE, an MPU6050 breakout, jumper wires, and a USB connection for ST-LINK. The firmware expects I²C address **0x68** (`AD0` low). Check your breakout's voltage requirements before wiring:

| MPU6050 | STM32F401RE |
| --- | --- |
| SDA | PB9 / I²C1 SDA |
| SCL | PB8 / I²C1 SCL |
| GND | GND |
| VCC | 3.3 V, if supported by the breakout |
| AD0 | GND for address 0x68 |

Open [`firmware/MPU6050_FreeRTOS.ioc`](firmware/MPU6050_FreeRTOS.ioc) in STM32CubeIDE, build, and flash through ST-LINK. Connect the board's USB serial port, run `python app.py`, select its **COM** port, and click **Connect**. The app requests a 30 ms stream interval. See [firmware build notes](firmware/README.md).

## Explore the console

| Workflow | In the app |
| --- | --- |
| Live motion | 30-second plots, motion events, connection health, and a 3D aircraft. |
| Capture and replay | **Start recording → Stop & save recording** writes CSV plus JSON. Use **Open session for replay…** to seek, step, and change speed. |
| Calibrate | With live data and recording stopped, open **Calibrate → Start calibration** for gyro and six-face checks; save a PC profile. |
| Diagnose | Open **Calibrate** for link and firmware health. Open **Fault Injection** for one-shot faults. |

![Calibration progress and live device health on COM3](docs/media/diagnostics-live.png)

*Live calibration and health: two faces completed; 2,167 validated frames, 0 checksum errors, and 0 missing sequence numbers. “Needs attention” reflects the board's previous IWDG reset.*

## System design

![Architecture from MPU6050 through STM32 and USB serial to the desktop console](docs/media/system-architecture.svg)

- **Firmware:** synchronized 14-byte I²C reads, FreeRTOS telemetry and status tasks, DMA UART, watchdog, and Flash crash snapshot.
- **Protocol:** sequence numbers and an 8-bit XOR checksum let the host reject bad frames and measure gaps.
- **Desktop:** serial, demo, and replay sources feed the same console. PC calibration and validated recordings keep replay consistent.

![Three deliberate fault paths and the evidence used to verify recovery](docs/media/fault-evidence.svg)

On hardware, the three faults corrupt one frame, hold the UART lock, or stop IWDG refresh. The app waits for packet, timing, or boot evidence before reporting success. Demo mode runs labeled simulations through the host parser.

![Live COM3 watchdog fault showing verified reboot and restored telemetry](docs/media/fault-watchdog-live.png)

*Live watchdog demonstration: the board reports `Reset: IWDG`, then fresh validated telemetry resumes after a measured 4.1 s packet gap.*

## Verify and navigate

```powershell
python -m unittest discover -s tests -v
python tests/smoke_dashboard.py
```

The first command runs hardware-free tests; the second needs a desktop/OpenGL context. Connected-board checks are in `tests/live_*_smoke.py`.

Code map: [`firmware/`](firmware/) handles acquisition; [`app.py`](app.py), [`sources.py`](sources.py), and [`fault_ui.py`](fault_ui.py) handle the console and sources; [`telemetry.py`](telemetry.py), [`kinematics.py`](kinematics.py), and [`motion_events.py`](motion_events.py) process data; [`session_io.py`](session_io.py) and [`calibration.py`](calibration.py) manage saved state.

Shortcuts: **V/F/N/S** change stream rate, **P** pauses, **R** zeros orientation, **D** dumps the Flash snapshot, **C** erases and re-arms the crash log, and **Esc** closes an expanded view.

Motion labels are demonstration heuristics, not safety measurements. The MPU6050 cannot provide drift-free position or absolute yaw. MIT licensed; see [LICENSE](LICENSE).
