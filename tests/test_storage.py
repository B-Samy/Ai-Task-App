import json
import zipfile
from tests.helpers import TmpStoreCase
from src.models.entities import Goal, Task, ValidationError, ScheduleBlock
from src.storage.json_store import CorruptDataError, IntegrityError, JsonStore, ALL_FILES


class StorageTests(TmpStoreCase):
    def test_missing_files_initialise(self):
        for n in ALL_FILES:
            self.assertTrue(self.store.path(n).exists(), n)
        self.assertEqual(self.store.load("goals"), [])
        self.assertEqual(self.store.validate_all(), [])

    def test_reinitialise_does_not_overwrite(self):
        self.store.save("goals", [Goal(title="Keep me")])
        JsonStore(self.dir)
        self.assertEqual(self.store.load("goals")[0].title, "Keep me")

    def test_invalid_data_rejected_before_write(self):
        bad = Task(title="x", priority=9)
        before = self.store.path("tasks").read_text()
        with self.assertRaises(ValidationError):
            self.store.save("tasks", [bad])
        self.assertEqual(self.store.path("tasks").read_text(), before)

    def test_corrupt_json_detected_and_preserved(self):
        p = self.store.path("goals")
        p.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(CorruptDataError) as cm:
            self.store.load("goals")
        self.assertEqual(p.read_text(), "{ not json")                     # original untouched
        self.assertTrue(cm.exception.preserved and cm.exception.preserved.exists())
        self.assertIn("restore a backup", str(cm.exception).lower())

    def test_invalid_record_in_file_is_corruption(self):
        self.store.path("tasks").write_text(json.dumps(
            {"schema_version": 1, "items": [{"title": "", "id": "a"}]}), encoding="utf-8")
        with self.assertRaises(CorruptDataError):
            self.store.load("tasks")

    def test_atomic_write_leaves_no_temp_files_and_backups_created(self):
        self.store.save("goals", [Goal(title="One")])
        self.store.save("goals", [Goal(title="Two")])
        self.assertEqual([p for p in self.dir.glob("*.tmp")], [])
        self.assertTrue(list(self.store.backup_dir.glob("goals.2*.json")))

    def test_backup_rotation(self):
        s = JsonStore(self.dir, backup_keep=3)
        for i in range(8):
            s.save("goals", [Goal(title=f"G{i}")])
        self.assertLessEqual(len(list(s.backup_dir.glob("goals.2*.json"))), 3)

    def test_duplicate_ids_detected(self):
        g = Goal(title="A")
        with self.assertRaises(IntegrityError):
            self.store.save("goals", [g, Goal(title="B", id=g.id)])
        self.store.path("goals").write_text(json.dumps(
            {"schema_version": 1, "items": [g.to_dict(), g.to_dict()]}), encoding="utf-8")
        with self.assertRaises(IntegrityError):
            self.store.load("goals")

    def test_cross_file_references(self):
        t = Task(title="Orphan task", goal_id="missing")
        with self.assertRaises(IntegrityError):
            self.store.save_many({"tasks": [t]})
        t2 = Task(title="Needs pre", prerequisites=["nope"])
        with self.assertRaises(IntegrityError):
            self.store.save_many({"tasks": [t2]})
        b = ScheduleBlock(task_id="nope", date="2026-01-01", start="09:00", end="10:00")
        with self.assertRaises(IntegrityError):
            self.store.save_many({"schedule": [b]})

    def test_backup_and_restore_roundtrip(self):
        self.store.save("goals", [Goal(title="Original")])
        z = self.store.create_backup_zip(self.dir / "out")
        self.store.save("goals", [Goal(title="Changed")])
        restored = self.store.restore_backup_zip(z)
        self.assertIn("goals.json", restored)
        self.assertEqual(self.store.load("goals")[0].title, "Original")

    def test_restore_rejects_corrupt_zip_content_without_touching_data(self):
        self.store.save("goals", [Goal(title="Safe")])
        bad = self.dir / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("goals.json", "garbage")
            zf.writestr("../evil.json", "{}")
        with self.assertRaises(CorruptDataError):
            self.store.restore_backup_zip(bad)
        self.assertEqual(self.store.load("goals")[0].title, "Safe")
        self.assertFalse((self.dir.parent / "evil.json").exists())

    def test_settings_persist_across_reload(self):
        self.svc.update_settings(daily_capacity_minutes=180, working_days=[0, 2, 4])
        again = JsonStore(self.dir).load_settings()
        self.assertEqual(again.daily_capacity_minutes, 180)
        self.assertEqual(again.working_days, [0, 2, 4])

    def test_invalid_settings_rejected(self):
        from src.services.app_service import ServiceError
        with self.assertRaises(ServiceError):
            self.svc.update_settings(day_start="18:00", day_end="09:00")
        with self.assertRaises(ServiceError):
            self.svc.update_settings(working_days=[])
