"""JSON-file persistence with atomic writes, backups, corruption detection and integrity checks."""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.settings import AppSettings, DEFAULT_DATA_DIR, SCHEMA_VERSION
from src.models.entities import MODELS, ValidationError, _Record

log = logging.getLogger(__name__)

LIST_COLLECTIONS = ["goals", "tasks", "schedule", "habits", "habit_logs", "activity_log"]
RAW_COLLECTIONS = ["chat_history"]          # plain dict lists, bounded
ALL_FILES = LIST_COLLECTIONS + RAW_COLLECTIONS + ["settings"]


class StorageError(RuntimeError):
    pass


class CorruptDataError(StorageError):
    """A JSON file could not be parsed or has an unexpected structure. The original is preserved."""

    def __init__(self, path: Path, reason: str, preserved: Path | None):
        self.path, self.reason, self.preserved = path, reason, preserved
        msg = (
            f"Data file '{path.name}' is corrupt ({reason}). The original was NOT modified"
            + (f"; a copy was saved as '{preserved.name}'." if preserved else ".")
            + " To recover: restore a backup from data/backups/ (Exports page > Restore, or copy the "
            "latest backup file over the corrupt one), or fix the JSON by hand and reload."
        )
        super().__init__(msg)


class IntegrityError(StorageError):
    pass


class JsonStore:
    """Single access point for all persisted data. Business logic never touches files directly."""

    def __init__(self, data_dir: Path | str | None = None, backup_keep: int = 10):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.backup_dir = self.data_dir / "backups"
        self.backup_keep = backup_keep
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.initialize()

    # ---------- paths / init ----------
    def path(self, name: str) -> Path:
        if name not in ALL_FILES:
            raise StorageError(f"Unknown collection '{name}'.")
        return self.data_dir / f"{name}.json"

    def initialize(self) -> None:
        """Create any missing files. Existing files (even corrupt) are never touched."""
        for name in LIST_COLLECTIONS + RAW_COLLECTIONS:
            if not self.path(name).exists():
                self._atomic_write(self.path(name), {"schema_version": SCHEMA_VERSION, "items": []})
        if not self.path("settings").exists():
            self._atomic_write(self.path("settings"),
                               {"schema_version": SCHEMA_VERSION, "settings": AppSettings().to_dict()})

    # ---------- low-level ----------
    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CorruptDataError(path, str(exc), self._preserve_corrupt(path)) from exc
        if not isinstance(data, dict) or "schema_version" not in data:
            raise CorruptDataError(path, "missing schema_version / not an object", self._preserve_corrupt(path))
        if data["schema_version"] > SCHEMA_VERSION:
            raise StorageError(f"'{path.name}' was written by a newer app version (schema {data['schema_version']}).")
        return data

    def _preserve_corrupt(self, path: Path) -> Path | None:
        try:
            dest = self.backup_dir / f"{path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S%f}.json"
            shutil.copy2(path, dest)
            return dest
        except OSError:  # pragma: no cover
            return None

    def _backup_file(self, name: str) -> None:
        src = self.path(name)
        if not src.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
        shutil.copy2(src, self.backup_dir / f"{name}.{stamp}.json")
        olds = sorted(self.backup_dir.glob(f"{name}.2*.json"))
        for old in olds[: max(0, len(olds) - self.backup_keep)]:
            old.unlink(missing_ok=True)

    # ---------- typed collections ----------
    def load(self, name: str) -> list[Any]:
        """Load a collection as typed records (or raw dicts for chat_history)."""
        data = self._read_json(self.path(name))
        items = data.get("items")
        if not isinstance(items, list):
            raise CorruptDataError(self.path(name), "'items' is not a list", self._preserve_corrupt(self.path(name)))
        if name in RAW_COLLECTIONS:
            return items
        model = MODELS[name]
        out: list[_Record] = []
        for i, raw in enumerate(items):
            try:
                out.append(model.from_dict(raw))
            except (ValidationError, TypeError) as exc:
                raise CorruptDataError(self.path(name), f"record #{i}: {exc}", self._preserve_corrupt(self.path(name))) from exc
        self._check_duplicates(name, out)
        return out

    @staticmethod
    def _check_duplicates(name: str, records: list[Any]) -> None:
        seen: set[str] = set()
        for r in records:
            if r.id in seen:
                raise IntegrityError(f"Duplicate ID '{r.id}' in {name}.")
            seen.add(r.id)

    def save(self, name: str, records: list[Any], *, backup: bool = True) -> None:
        """Validate then atomically write a collection. Invalid data is rejected before touching disk."""
        if name in RAW_COLLECTIONS:
            items = list(records)
        else:
            model = MODELS[name]
            for r in records:
                if not isinstance(r, model):
                    raise ValidationError(f"{name} accepts only {model.__name__} records.")
                r.validate()
            self._check_duplicates(name, records)
            items = [r.to_dict() for r in records]
        if backup and name not in ("activity_log", "chat_history"):
            self._backup_file(name)
        self._atomic_write(self.path(name), {"schema_version": SCHEMA_VERSION, "items": items})

    def save_many(self, collections: dict[str, list[Any]], *, check_refs: bool = True) -> None:
        """Save several collections; reference integrity is validated first against the combined state."""
        if check_refs:
            state = {n: (collections[n] if n in collections else self.load(n)) for n in LIST_COLLECTIONS}
            problems = self.check_references(state)
            if problems:
                raise IntegrityError("; ".join(problems))
        for name, recs in collections.items():
            self.save(name, recs)

    # ---------- settings ----------
    def load_settings(self) -> AppSettings:
        data = self._read_json(self.path("settings"))
        try:
            return AppSettings.from_dict(data.get("settings", {}))
        except (ValueError, TypeError) as exc:
            raise CorruptDataError(self.path("settings"), str(exc), self._preserve_corrupt(self.path("settings"))) from exc

    def save_settings(self, settings: AppSettings) -> None:
        settings.validate()
        self._backup_file("settings")
        self._atomic_write(self.path("settings"), {"schema_version": SCHEMA_VERSION, "settings": settings.to_dict()})

    # ---------- integrity ----------
    @staticmethod
    def check_references(state: dict[str, list[Any]]) -> list[str]:
        problems: list[str] = []
        goal_ids = {g.id for g in state.get("goals", [])}
        task_ids = {t.id for t in state.get("tasks", [])}
        habit_ids = {h.id for h in state.get("habits", [])}
        for t in state.get("tasks", []):
            if t.goal_id and t.goal_id not in goal_ids:
                problems.append(f"Task '{t.title}' references missing goal {t.goal_id}.")
            for p in t.prerequisites:
                if p not in task_ids:
                    problems.append(f"Task '{t.title}' has missing prerequisite {p}.")
        for b in state.get("schedule", []):
            if b.task_id not in task_ids:
                problems.append(f"Schedule block {b.id} references missing task {b.task_id}.")
        for h in state.get("habits", []):
            if h.goal_id and h.goal_id not in goal_ids:
                problems.append(f"Habit '{h.title}' references missing goal {h.goal_id}.")
        for l in state.get("habit_logs", []):
            if l.habit_id not in habit_ids:
                problems.append(f"Habit log {l.id} references missing habit {l.habit_id}.")
        return problems

    def validate_all(self) -> list[str]:
        """Return integrity problems across files (raises CorruptDataError for unreadable files)."""
        state = {n: self.load(n) for n in LIST_COLLECTIONS}
        self.load_settings()
        return self.check_references(state)

    # ---------- backup / restore ----------
    def create_backup_zip(self, dest_dir: Path | str | None = None) -> Path:
        dest_dir = Path(dest_dir) if dest_dir else self.backup_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"full-backup-{datetime.now():%Y%m%d-%H%M%S%f}.zip"
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in ALL_FILES:
                p = self.path(name)
                if p.exists():
                    zf.write(p, p.name)
        return dest

    def restore_backup_zip(self, zip_path: Path | str) -> list[str]:
        """Restore from a full backup ZIP. Content is validated and current files are backed up first."""
        zip_path = Path(zip_path)
        restored: list[str] = []
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad:
                raise StorageError(f"Backup ZIP is damaged (first bad member: {bad}).")
            allowed = {f"{n}.json" for n in ALL_FILES}
            members = [m for m in zf.namelist() if m in allowed]   # ignores paths/unknown names
            if not members:
                raise StorageError("Backup ZIP contains no recognised data files.")
            with tempfile.TemporaryDirectory() as tmp:
                probe = JsonStore.__new__(JsonStore)
                probe.data_dir, probe.backup_dir, probe.backup_keep = Path(tmp), Path(tmp) / "b", 1
                probe.backup_dir.mkdir()
                for m in members:
                    (probe.data_dir / m).write_bytes(zf.read(m))
                for m in members:                      # validate everything before replacing anything
                    probe.load_settings() if m == "settings.json" else probe.load(m[:-5])
                self.create_backup_zip(self.backup_dir / "pre-restore")
                for m in members:
                    shutil.copy2(probe.data_dir / m, self.data_dir / m)
                    restored.append(m)
        return restored
