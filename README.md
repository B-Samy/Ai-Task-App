# AI Task Intelligence System

A local, single-user planner that turns a natural-language goal into milestones and tasks, builds a **deterministic**
schedule around your availability, tracks progress and habits, and **reschedules** when you fall behind.
Streamlit UI · local **JSON files only** (no database) · Ollama/Llama as the primary LLM · optional hosted API.

> **Status of verification:** see [docs/VERIFICATION.md](docs/VERIFICATION.md) for exactly what has and has not been tested.

## What it does

- **Goals & tasks** with priorities, deadlines, success criteria, categories, prerequisites (cycle-checked) and split/no-split flags.
- **AI planner**: an LLM drafts a plan; every response is validated (malformed JSON, vague/duplicate tasks, bad or circular
  prerequisites are rejected); you edit and approve before anything is saved. Without an LLM you get a clearly labelled manual or fallback planner.
- **Scheduler** (pure Python, LLM never involved): working days/hours, daily cap, breaks, buffer, dependency order, no overlaps,
  per-goal availability, deadline conflict detection, per-task explanations, and stable results when inputs are unchanged.
- **Adaptive rescheduling**: complete / start / pause / block / skip today / mark missed / record time / change deadline.
  Rebuilds show an old-vs-new preview; replaced blocks are kept as `superseded`, unfinished past blocks become `missed`; everything is logged.
- **Assistant**: local answers for "what next", "why behind", deadlines, progress, habits; LLM for free-form questions; actions are proposals you must confirm.
- **Habits** with daily / weekdays / weekly recurrence, streaks, 7- and 30-day adherence.
- **Alerts** (in-app only), **analytics** (observed vs heuristic vs ML clearly labelled), **ML duration insights** (only after enough history), **exports/backup/restore**, **demo data** (explicit opt-in).

## Quick start (Windows 10/11, PowerShell)

```powershell
cd C:\Ds_projects\AI-Task-Intelligence
py --version
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-ml.txt     # optional: ML duration insights
streamlit run app.py
```

Open http://localhost:8501. The app works without any LLM (manual planner, scheduler, habits, analytics all work).

### 1. Install Python if missing
Install Python 3.10+ from https://www.python.org/downloads/windows/ (tick **Add python.exe to PATH**), or `winget install Python.Python.3.12`. Reopen PowerShell and run `py --version`.

### 2. PowerShell blocks `Activate.ps1`
Either allow scripts for this window only:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```
or skip activation and call the venv directly: `.\.venv\Scripts\python.exe -m streamlit run app.py`
or use `cmd`: `.venv\Scripts\activate.bat`.

### 3. Install and start Ollama (separate application)
Download from https://ollama.com/download/windows and install. It runs as a background service on `http://localhost:11434`.
```powershell
ollama --version
ollama serve          # only if it is not already running (an "address in use" message means it is)
```

### 4. Download and verify the model
```powershell
ollama pull llama3.2
ollama list           # llama3.2 must appear
ollama run llama3.2 "Reply with OK"
```
Any installed model works: set its exact name in **Settings → LLM provider**. Larger models plan better but are slower.

### 5. Check connectivity
```powershell
curl http://localhost:11434/api/tags
python scripts\diagnostics.py
```
Or use **Settings → Test connection** / the sidebar **Check LLM connection** button.

### 6. Optional hosted API (e.g. Groq free tier)
```powershell
copy .env.example .env
notepad .env          # set LLM_API_KEY=your-key
```
Restart Streamlit, then in **Settings** choose *Hosted OpenAI-compatible*, set base URL and model, tick the disclosure box, and test.
The default base URL is Groq's OpenAI-compatible endpoint, and the default model name is a **placeholder I could not verify**; check your provider's current docs.
Free tiers, quotas and model names change; nothing here promises free access. Keys live only in `.env`/environment (never in JSON data, exports or the ZIP).
**Privacy:** with a hosted provider, text you submit and a small summary of your tasks are sent to that service.

### 7. Diagnostics
```powershell
python scripts\diagnostics.py
```
Checks Python, packages, the data directory (including cross-file integrity), the selected LLM, and whether `ollama`/`docker` are on PATH. It never prints secrets.

### 8. Automated tests
```powershell
pip install -r requirements-dev.txt
python -m pytest -q          # or: python -m unittest discover -s tests -t .
```

### 9. Docker
```powershell
docker compose build
docker compose up
```
Open http://localhost:8501. Data persists in `.\data` (volume mount). No database container exists. Ollama is **not** bundled:
inside a container `localhost` is the container itself, so compose sets `OLLAMA_URL=http://host.docker.internal:11434` (applied to **new** installs; for an existing `data\settings.json` change the URL in Settings).
Ollama on Windows listens on `127.0.0.1` by default; if the container can't reach it, set the user environment variable `OLLAMA_HOST=0.0.0.0`, restart Ollama, and keep Windows Firewall restricted to your machine.
The port is published on `127.0.0.1` only because the app has no login. Do not expose it to a network.

### 10. Back up, restore, export
- **Exports page** → download CSV/JSON (goals+tasks, schedule, habits), analytics report (Markdown/JSON), create/restore a full backup ZIP.
- Automatic: every save of a main data file first copies the previous version into `data\backups\` (last 10 kept per file).
- Manual: copy the `data` folder while the app is stopped. To restore, replace the files with the copies (or use *Restore* on the Exports page, which validates the ZIP first and keeps a pre-restore backup).

### 11. Troubleshooting
See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): missing model, invalid key, rate limits, port conflicts, corrupt JSON, Windows paths.

## Packaging
```powershell
python scripts\package_project.py
```
Creates and verifies `AI_Task_Intelligence_System.zip` (excludes `.env`, secrets, `.venv`, caches, logs, runtime data/backups).

## Project layout
```
app.py                  Dashboard (entry point)
pages/                  Goals, AI Planner, Schedule, Tasks, Habits, Analytics, AI Assistant, Settings, Exports
src/models/             Validated dataclasses (Goal, Task, ScheduleBlock, Habit, HabitLog, Activity)
src/storage/            JsonStore: atomic writes, backups, corruption detection, integrity checks, restore
src/planning/           dependencies.py (cycles/blocked reasons), scheduler.py (priority scoring, build, preview)
src/services/           AppService (all actions + activity log), assistant.py
src/llm/                Provider interface, Ollama, OpenAI-compatible, plan validation, fallbacks
src/analytics/          Metrics and habit logic     src/ml/  Duration model     src/notifications/  Alerts
src/utils/              Exports, demo data          src/ui/  Streamlit helpers
tests/  scripts/  docs/  data/
```
`docs/ARCHITECTURE.md` documents the scoring formula and scheduling rules.

## Limitations (please read)
- **Single user, local only.** JSON files are not safe for concurrent writers and are **not encrypted**. Anyone with file access can read them.
- No login. Do not expose the Streamlit port to a network.
- Alerts only exist while the app is open; there is no background or desktop notification service.
- "Protect high priority" is implemented (priority ≥ 4 always outranks lower priorities). A "lock lower-priority tasks in place" option is **not** implemented; rescheduling always re-optimises all future planned blocks.
- ML needs `scikit-learn` and at least `ml_min_samples` (default 30) completed tasks with recorded time; until then only a labelled heuristic ratio is shown.
- Hosted-provider defaults are unverified placeholders.
- `pages/1_Dashboard.py` from the suggested layout is `app.py` here (Streamlit's main page).
