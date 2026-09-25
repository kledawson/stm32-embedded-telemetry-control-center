# Contributing and commit rhythm

Keep commits small enough that a recruiter can understand the intent from the title and diff.

Recommended commit boundaries:

- One bug fix or protocol change
- One user-visible feature, such as recording or replay
- One firmware capability, such as calibration or crash diagnostics
- One test/documentation/CI improvement

Before committing:

```powershell
python -m unittest discover -s tests -v
python -m py_compile app.py telemetry.py kinematics.py
```

Use imperative commit titles such as `fix: accept spaced telemetry frames`, `feat: add session replay`, or `test: cover calibration persistence`. Push after a coherent feature is tested—not after every tiny edit. While developing a larger feature, use local commits freely and squash only if the final history becomes noisy.

Suggested release points for the portfolio are the end of each roadmap phase. A tagged milestone such as `v0.1.0-demo`, `v0.2.0-replay`, or `v1.0.0-portfolio` gives recruiters a stable reference.
