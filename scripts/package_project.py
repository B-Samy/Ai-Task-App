"""Create and verify AI_Task_Intelligence_System.zip. Run from anywhere: python scripts/package_project.py"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_NAME = "AI_Task_Intelligence_System.zip"
EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".git", "logs", "backups", "exports", ".idea", ".vscode", "node_modules"}
EXCLUDE_GLOBS = ["*.zip", "*.pyc", "*.log", "*.tmp", ".env", "secrets.toml", ".DS_Store", "*.corrupt-*"]
EXCLUDE_DATA_JSON = True      # runtime JSON in data/ is private and never packaged
REQUIRED = ["app.py", "requirements.txt", ".env.example", ".gitignore", "README.md", "Dockerfile", "docker-compose.yml",
            "src/storage/json_store.py", "src/planning/scheduler.py", "src/llm/ollama.py", "pages/2_Goals.py",
            "pages/10_Exports.py", "tests/test_scheduling.py", "scripts/diagnostics.py", "scripts/package_project.py", "data/README.md"]


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = set(rel.parts[:-1])
    if parts & EXCLUDE_DIRS:
        return False
    name = rel.name
    if any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS if g != ".env") or name == ".env":
        return False
    if EXCLUDE_DATA_JSON and rel.parts[0] == "data" and name.endswith(".json"):
        return False
    return True


def build(dest: Path) -> list[str]:
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and included(p))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(ROOT).as_posix())
    return [p.relative_to(ROOT).as_posix() for p in files]


def verify(dest: Path, run_tests: bool = True) -> list[str]:
    problems: list[str] = []
    if not dest.exists() or dest.stat().st_size == 0:
        return ["ZIP missing or empty"]
    with zipfile.ZipFile(dest) as zf:
        bad = zf.testzip()
        if bad:
            problems.append(f"corrupt member: {bad}")
        names = zf.namelist()
        for r in REQUIRED:
            if r not in names:
                problems.append(f"missing expected file: {r}")
        for n in names:
            low = n.lower()
            if (low.endswith(("/.env", ".env")) and not low.endswith(".env.example")) or "secrets.toml" in low or "/.venv/" in low \
                    or "__pycache__" in low or ".pytest_cache" in low or low.endswith(".log") or (low.startswith("data/") and low.endswith(".json")) \
                    or low.endswith(".zip") or "/backups/" in low:
                problems.append(f"forbidden file packaged: {n}")
        import re
        key_re = re.compile(r"(sk-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,})")
        for n in names:
            if n.endswith((".py", ".md", ".txt", ".toml", ".yml", ".example", ".json")):
                if key_re.search(zf.read(n).decode("utf-8", "ignore")):
                    problems.append(f"possible API key in {n}")
        if run_tests and not problems:
            with tempfile.TemporaryDirectory() as tmp:
                zf.extractall(tmp)
                r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=tmp,
                                   capture_output=True, text=True, timeout=600)
                tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
                print("Extracted-copy test run:", " | ".join(tail))
                if r.returncode != 0:
                    problems.append("tests failed in extracted copy")
    return problems


def main() -> int:
    dest = ROOT / ZIP_NAME
    if dest.exists():
        dest.unlink()
    files = build(dest)
    print(f"Wrote {dest} ({dest.stat().st_size:,} bytes, {len(files)} files)")
    problems = verify(dest, run_tests="--no-tests" not in sys.argv)
    if problems:
        print("VERIFICATION FAILED:")
        for p in problems:
            print(" -", p)
        return 1
    print("Verification passed: integrity OK, required files present, no secrets/runtime data/caches packaged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
