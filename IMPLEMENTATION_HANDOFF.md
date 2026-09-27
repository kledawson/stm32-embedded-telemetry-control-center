# STM32 Telemetry Console handoff

## Completed

- Implemented the agreed single-screen PyQt6 dashboard with connection toolbar, control rail, 3D attitude, both native pyqtgraph plots, compact motion events, and scrollable device log.
- Kept V/F/N/S stream rates, P pause, R orientation reset, D snapshot dump, C Flash erase, all existing model choices, and custom STL loading.
- Added simple measured motion/impact heuristics in motion_events.py, with debounce, impact hysteresis, and cooldown.
- Removed the car simulation. The active 3D model (including a custom STL) now rotates exclusively from the incoming gyro channels: X to pitch, Y to roll, and Z to yaw.
- Focus overlay expands only the 3D view and graphs. It moves the same widget, preserving plot range and camera state. Escape and Close restore it.
- Demo mode uses a continuous gyro source to demonstrate the 3D attitude view. Stop interrupts the slow demo wait promptly.
- Updated README. No firmware changes, commits, or pushes.

## Verified

- All 12 standard-library unit tests passed, including four new motion-event tests.
- Python compilation passed for the updated app and modules.
- Actual desktop/OpenGL smoke test passed after the final UI tweaks: demo samples arrive, expansion/restoration preserves plot range and camera, pause stops plot changes, R resets orientation, slow-rate disconnect stops promptly, and offline status remains correct.
- Inspected final screenshots of the dashboard and expanded 3D view at 1440 × 900; no clipping was visible.
- Added a desktop smoke assertion that gyro X/Y/Z samples update pitch/roll/yaw in the active 3D model path.

## Remaining checks (no feature expansion needed)

1. Test with the actual STM32: connect/disconnect, each stream rate, pause/resume, R, and live attitude. Hardware was not available during this implementation. Check Flash commands only when intended; C erases Sector 5.
2. Check custom STL loading/cancellation and all three model presets. These use the existing mesh loaders.
3. Optionally try the 16-second demo at the target display scaling and confirm graph pan/zoom/context-menu export in normal and expanded views.

## Scope guardrails

Keep this a polished embedded telemetry showcase. Do not add recording/replay, FFT, dashboards with many modes, cloud services, or position tracking. Event labels are heuristics, not calibrated safety limits. Preserve the existing attitude estimator and native graph interactions.

## Files

- app.py: workers, meshes, dashboard layout and control wiring.
- dashboard_ui.py: styling, panels, focus overlay.
- motion_events.py: pure event heuristics.
- tests/test_motion_events.py: event regression tests.
- tests/smoke_dashboard.py: optional desktop integration smoke check.
- README.md: updated usage and limitations.

The repository reported these source files as untracked before implementation. Preserve the user's files; do not reset or clean the working directory. Git status on this host required the per-command safe-directory option because of ownership; no global Git configuration was changed.
