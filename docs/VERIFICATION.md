# Verification status (written at build time)

Built and tested in a **Linux sandbox** (Python 3.12.3) with **no network access**. `streamlit`, `pydantic`, `pytest` and `plotly` were not installable there,
so the design avoids pydantic/plotly (validation uses explicit dataclass validators) and tests use `unittest` (also collected by pytest).

## Verified in the sandbox
- Storage, LLM-response handling (with mocked HTTP), scheduling, rescheduling, dependencies, habits, analytics, ML guard, exports, demo mode, assistant: automated tests.
- Every Streamlit page executes without Python errors against a **stub** of Streamlit, on empty data and with demo data + a schedule (`tests/test_ui_smoke.py`).
- `scripts/diagnostics.py` runs; `scripts/package_project.py` builds and verifies the ZIP.

## NOT verified (needs your machine)
- Running under real Streamlit in a browser (layout, widget behaviour, button flows). Only default code paths are exercised by the stub test.
- Real Ollama responses / model quality; real hosted-provider responses (HTTP behaviour is tested only with mocks).
- Docker build/run. Windows-specific behaviour (PowerShell commands, paths, `Activate.ps1`).
- Installing the pinned requirement ranges on your Python version.
- Hosted-provider default URL/model availability (placeholders).

First steps on your machine: run `python scripts\diagnostics.py`, then `python -m pytest -q`, then `streamlit run app.py`.
