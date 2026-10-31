# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **"Cannot reach Ollama"** | Ollama isn't running. Start the Ollama app (or `ollama serve`). Check the URL in Settings (`http://localhost:11434`; in Docker use `http://host.docker.internal:11434`). |
| **"model 'llama3.2' is not installed" / 404** | `ollama pull llama3.2`, then `ollama list`. Or set the exact installed name in Settings. |
| **Ollama times out** | First request loads the model into memory. Retry, raise the timeout in Settings, or use a smaller model (e.g. `llama3.2:1b`). |
| **"API key was rejected" (401/403)** | Check `LLM_API_KEY` in `.env` (no quotes/spaces), restart Streamlit. Make sure the key belongs to the provider in *Hosted base URL*. |
| **"rate limit / quota reached" (429)** | Free tiers have quotas that change. Wait, reduce requests, or switch to Ollama in Settings. |
| **"No API key found"** | `copy .env.example .env`, edit it, restart. Keys are read from the environment only. |
| **Port 8501 already in use** | `streamlit run app.py --server.port 8502`, or find the process: `netstat -ano \| findstr 8501` then `taskkill /PID <pid> /F`. |
| **"Data file ... is corrupt"** | The app **did not modify** the file and saved a copy in `data\backups\<name>.corrupt-*.json`. Recover: (1) close the app; (2) copy the newest good `data\backups\<name>.<timestamp>.json` over `data\<name>.json`; or open the file in an editor, fix the JSON syntax (common: trailing comma, truncated file) and restart; or (3) delete only that one file to start it empty (a fresh file is created next start). |
| **Duplicate ID / missing reference errors** | A JSON file was edited by hand. Fix the offending record (the message names the ID), or restore a backup. |
| **PowerShell: "running scripts is disabled"** | See README §2 (`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`). |
| **Paths with spaces / OneDrive** | Quote paths in PowerShell (`cd "C:\My Folder\AI-Task-Intelligence"`). Avoid OneDrive-synced folders for `data\` (sync locks can block atomic file replacement). |
| **`ModuleNotFoundError: streamlit`** | The venv isn't active. Run `.\.venv\Scripts\Activate.ps1` or use `.\.venv\Scripts\python.exe -m streamlit run app.py`. |
| **ML says "insufficient historical data"** | Expected until enough completed tasks have recorded actual time (default 30). Not an error. |
| **Docker can't reach Ollama** | Use `host.docker.internal`; set `OLLAMA_HOST=0.0.0.0` for Ollama on the host and restart it. |
| **Details of an unexpected error** | See `logs\app.log` (secrets are redacted/never logged). |
