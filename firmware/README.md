# STM32F401RE FreeRTOS firmware

This directory contains the STM32CubeIDE project for the Nucleo STM32F401RE and MPU6050 telemetry demo.

## Build

Open `MPU6050_FreeRTOS.ioc` or `.project` in STM32CubeIDE and build the project there. Generated `Debug/` and `Release/` directories are intentionally excluded from version control.

## Current firmware behavior

- FreeRTOS telemetry and status tasks
- Synchronized 14-byte MPU6050 burst reads over I2C
- 200 Hz sensor configuration with a 30 ms host visual telemetry interval (about 33 Hz)
- Checksummed UART telemetry packets and command handling
- UART receive/drop counters included in the heartbeat
- Deadline-based telemetry scheduling with `osDelayUntil`
- Flash-backed crash-log support

## Provenance

The source folder did not contain an earlier Git repository or archived firmware snapshot. This import is therefore a snapshot of the CubeIDE tree available on 2026-09-25, not a reconstructed exact “pre-Codex” commit. The repository history keeps the available host-side Python commits and labels this firmware import as a snapshot rather than inventing an original firmware version.
