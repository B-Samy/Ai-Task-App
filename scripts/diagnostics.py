"""Environment diagnostics. Run: python scripts/diagnostics.py   (never prints secrets)"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

failures = 0


def line(ok: bool | None, label: str, detail: str = "") -> None:
    global failures
    mark = {True: "[ OK ]", False: "[FAIL]", None: "[ -- ]"}[ok]
    if ok is False:
        failures += 1
    print(f"{mark} {label}" + (f": {detail}" if detail else ""))


def main() -> int:
    line(sys.version_info >= (3, 10), "Python >= 3.10", sys.version.split()[0])
    for mod, required in (("streamlit", True), ("pandas", True), ("requests", True), ("dotenv", True), ("sklearn", False), ("numpy", False)):
        try:
            m = importlib.import_module(mod)
            line(True, f"import {mod}", getattr(m, "__version__", ""))
        except Exception as exc:  # noqa: BLE001
            line(False if required else None, f"import {mod}", ("missing (required)" if required else "missing (optional: ML insights disabled)") + f" - {type(exc).__name__}")
    from src.config.settings import DEFAULT_DATA_DIR
    from src.storage.json_store import CorruptDataError, JsonStore
    try:
        with tempfile.TemporaryDirectory() as t:
            JsonStore(t).validate_all()
        line(True, "Storage layer self-test (temp dir)")
    except Exception as exc:  # noqa: BLE001
        line(False, "Storage layer self-test", str(exc))
    data_dir = Path(os.environ.get("TASK_INTEL_DATA_DIR", DEFAULT_DATA_DIR))
    try:
        store = JsonStore(data_dir)
        problems = store.validate_all()
        line(not problems, f"Data directory valid ({data_dir})", "; ".join(problems[:3]))
        settings = store.load_settings()
    except CorruptDataError as exc:
        line(False, "Data files", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        line(False, "Data directory", str(exc))
        return 1
    from src.llm.factory import get_provider
    p = get_provider(settings)
    if p is None:
        line(None, "LLM provider", "none selected")
    else:
        st = p.check()
        line(True if st.ok else None, f"LLM {st.provider}/{st.model}" + ("" if st.ok else " (warning)"), st.message)
    key = bool(os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY"))
    line(None, "Hosted API key present in environment", "yes" if key else "no (only needed for the hosted provider)")
    line(None, "ollama executable on PATH", "found" if shutil.which("ollama") else "not found (install from https://ollama.com)")
    line(None, "docker executable on PATH", "found" if shutil.which("docker") else "not found (only needed for the Docker option)")
    print("\nResult:", "all required checks passed" if failures == 0 else f"{failures} required check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
