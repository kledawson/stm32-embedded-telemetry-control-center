# Portfolio Roadmap

The goal is a polished, demonstrable embedded/desktop system with enough software-engineering depth to discuss architecture, testing, reliability, and trade-offs in interviews.

## Phase 0 — Stabilization (completed in this pass)

- Define and test the telemetry framing contract
- Maintain backward compatibility with legacy packet spacing
- Add hardware-free demo mode
- Extract protocol and kinematics code from the GUI
- Remove gravity in the sensor frame using the estimated attitude
- Clamp delayed-frame integration time
- Reserve the firmware crash-log sector in the linker configuration
- Add dependency documentation and unit tests

## Phase 1 — Session data and reproducibility

- Record validated raw samples, derived attitude, linear acceleration, timestamps, and event markers to CSV
- Add a session metadata sidecar containing firmware version, sample rate, calibration identifier, and application version
- Load recorded sessions and replay them at 0.5x, 1x, 2x, and 4x
- Add seek, pause, frame-step, and timeline controls
- Include deterministic replay files in integration tests

**Portfolio value:** data pipelines, serialization, state machines, reproducible debugging, and separation between live and replay data sources.

## Phase 2 — Calibration and signal quality

- Guided stationary calibration with progress and stability checks
- Estimate accelerometer and gyro bias from a statistically valid sample window
- Store versioned calibration data in JSON and surface the active calibration in the UI
- Display packet count, checksum failures, dropped-frame estimates, sample jitter, and serial throughput
- Add configurable low-pass filtering without changing the raw recorded data

**Portfolio value:** numerical reasoning, configuration management, observability, and honest treatment of sensor limitations.

## Phase 3 — Frequency and event analysis

- Add a windowed FFT tab with selectable axis, window size, and sample frequency
- Identify dominant vibration frequencies and peak amplitudes
- Add configurable shock events with pre/post-trigger sample capture
- Correlate firmware flash events with host-side session markers

**Portfolio value:** DSP fundamentals, real-time buffering, and event-driven design.

## Phase 4 — Product-quality architecture

- Introduce a data-source interface implemented by serial, demo, and replay sources
- Move widgets into focused modules and use a controller/view-model layer
- Add structured logging and a diagnostics export
- Add type checking, formatting, linting, and CI
- Package the app with PyInstaller and publish versioned Windows releases
- Put the Python and firmware projects in one Git repository with an architecture decision record directory

**Portfolio value:** maintainability, automated quality gates, packaging, and professional delivery.

## Phase 5 — Portfolio demonstration

Recommended 90–120 second video sequence:

1. Show the Nucleo board and MPU6050 wiring on camera.
2. Launch the packaged dashboard and connect to the detected COM port.
3. Rotate the board while showing the physical board and matching 3D attitude side-by-side.
4. Apply a short controlled translation to show the force vector and acceleration analytics.
5. Trigger a safe shock event, disconnect/reboot, and dump the persistent Flash snapshot.
6. Record a session, disconnect the hardware, and replay the exact run at 2x.
7. Finish on the diagnostics/FFT view and briefly show the automated test result.

Use a picture-in-picture layout: board camera in one corner, screen capture as the main view, and a small protocol/architecture overlay for ten seconds. Avoid claiming position tracking; the MPU6050-only system estimates attitude and linear acceleration but cannot provide drift-free position or absolute yaw.

## Strong optional extensions

- Add a magnetometer or replace the IMU with a 9-DOF device for absolute yaw correction
- Use COBS or SLIP plus a binary schema and sequence numbers for a production-grade protocol
- Add a boot-time firmware/version handshake and reject incompatible protocol versions cleanly
- Stream device health such as task stack high-water marks and watchdog-reset cause
- Add fault injection in demo/replay mode for corrupt checksums, delayed frames, and disconnects

