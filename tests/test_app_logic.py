import csv
import io
import json
import unittest
from datetime import date, timedelta, datetime

from tests.helpers import TmpStoreCase
from src.analytics import habits as H
from src.analytics import metrics as M
from src.config.settings import AppSettings
from src.ml.duration import DurationModel, baseline_ratio
from src.models.entities import Goal, Habit, HabitLog, Task, ValidationError
from src.notifications.alerts import compute_alerts
from src.services import assistant
from src.services.app_service import ServiceError
from src.utils import demo
from src.utils.exports import safe_filename, to_csv, to_json, report_markdown

TODAY = date(2026, 11, 4)


class Validation(TmpStoreCase):
    def test_goal_task_habit_validation(self):
        with self.assertRaises(ServiceError):
            self.svc.create_goal(title="  ")
        with self.assertRaises(ServiceError):
            self.svc.create_goal(title="x", priority=7)
        with self.assertRaises(ServiceError):
            self.svc.create_task(title="ok task", estimate_minutes=1)
        with self.assertRaises(ServiceError):
            self.svc.create_task(title="ok task", goal_id="ghost")
        with self.assertRaises(ServiceError):
            self.svc.create_task(title="ok task", prerequisites=["ghost"])
        with self.assertRaises(ServiceError):
            self.svc.create_habit(title="Weekly", recurrence="weekly", days=[])
        with self.assertRaises(ServiceError):
            self.svc.create_habit(title="Bad", recurrence="hourly")

    def test_prerequisite_rules_on_update(self):
        a = self.svc.create_task(title="Task alpha")
        b = self.svc.create_task(title="Task beta", prerequisites=[a.id])
        with self.assertRaises(ServiceError):
            self.svc.update_task(a.id, prerequisites=[a.id])
        with self.assertRaises(ServiceError):
            self.svc.update_task(a.id, prerequisites=["nonexistent"])
        with self.assertRaises(ServiceError) as e:
            self.svc.update_task(a.id, prerequisites=[b.id])
        self.assertIn("cycle", str(e.exception))

    def test_goal_lifecycle_and_progress(self):
        g = self.svc.create_goal(title="Life cycle")
        t1 = self.svc.create_task(title="Part one", goal_id=g.id, estimate_minutes=60)
        self.svc.create_task(title="Part two", goal_id=g.id, estimate_minutes=60)
        self.svc.complete_task(t1.id, actual_minutes=70)
        self.assertEqual(self.svc.goal_progress(g.id)["pct"], 50.0)
        for st in ("paused", "active", "completed", "archived"):
            self.assertEqual(self.svc.set_goal_status(g.id, st).status, st)

    def test_tasks_are_archived_not_deleted_and_history_kept(self):
        t = self.svc.create_task(title="Archive me")
        self.svc.archive_task(t.id)
        self.assertEqual(len(self.svc.tasks()), 1)
        self.assertEqual(self.svc.tasks()[0].status, "archived")
        self.assertTrue(any(a.action == "task_archived" for a in self.svc.activity()))

    def test_block_requires_reason_and_record_time(self):
        t = self.svc.create_task(title="Blockable task")
        with self.assertRaises(ServiceError):
            self.svc.block_task(t.id, "")
        self.svc.block_task(t.id, "waiting on review")
        self.svc.unblock_task(t.id)
        self.svc.record_time(t.id, 25)
        self.svc.record_time(t.id, 5)
        self.assertEqual(self.svc.tasks()[0].actual_minutes, 30)

    def test_persistence_across_service_reload(self):
        g = self.svc.create_goal(title="Persist me")
        from src.services.app_service import AppService
        from src.storage.json_store import JsonStore
        again = AppService(JsonStore(self.dir))
        self.assertEqual(again.goals()[0].id, g.id)

    def test_apply_plan_creates_tasks_with_mapped_prerequisites(self):
        from src.llm.plan_schema import manual_plan
        plan = manual_plan("Plan goal", ["Read the book | 60", "Write the summary | 30 | after:1"])
        goal, tasks = self.svc.apply_plan(plan, goal_fields={"title": "Plan goal"})
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[1].prerequisites, [tasks[0].id])
        self.assertEqual(self.store.validate_all(), [])


class Habits(TmpStoreCase):
    def test_streak_and_rates(self):
        h = Habit(title="Daily habit", start_date=(TODAY - timedelta(days=10)).isoformat())
        logs = [HabitLog(habit_id=h.id, date=(TODAY - timedelta(days=i)).isoformat()) for i in (0, 1, 2, 4, 5)]
        self.assertEqual(H.current_streak(h, logs, TODAY), 3)
        self.assertEqual(H.longest_streak(h, logs, TODAY), 3)
        self.assertAlmostEqual(H.completion_rate(h, logs, TODAY - timedelta(days=6), TODAY), 5 / 7)

    def test_today_not_done_does_not_break_streak(self):
        h = Habit(title="Daily habit", start_date=(TODAY - timedelta(days=10)).isoformat())
        logs = [HabitLog(habit_id=h.id, date=(TODAY - timedelta(days=i)).isoformat()) for i in (1, 2)]
        self.assertEqual(H.current_streak(h, logs, TODAY), 2)

    def test_missed_day_breaks_streak(self):
        h = Habit(title="Daily habit", start_date=(TODAY - timedelta(days=10)).isoformat())
        logs = [HabitLog(habit_id=h.id, date=(TODAY - timedelta(days=i)).isoformat()) for i in (0, 2, 3)]
        self.assertEqual(H.current_streak(h, logs, TODAY), 1)

    def test_weekdays_skip_weekend_and_weekly_recurrence(self):
        h = Habit(title="Weekday habit", recurrence="weekdays", start_date="2026-10-01")
        fri, sat, sun, mon = date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)
        self.assertFalse(H.is_due(h, sat))
        logs = [HabitLog(habit_id=h.id, date=d.isoformat()) for d in (fri, mon)]
        self.assertEqual(H.current_streak(h, logs, mon), 2)       # weekend does not break it
        w = Habit(title="Tue/Thu habit", recurrence="weekly", days=[1, 3], start_date="2026-10-01")
        self.assertTrue(H.is_due(w, date(2026, 11, 3)))
        self.assertFalse(H.is_due(w, date(2026, 11, 4)))

    def test_start_date_boundary_and_empty_states(self):
        h = Habit(title="Future habit", start_date=(TODAY + timedelta(days=3)).isoformat())
        self.assertEqual(H.current_streak(h, [], TODAY), 0)
        self.assertIsNone(H.completion_rate(h, [], TODAY - timedelta(days=6), TODAY))
        self.assertEqual(H.longest_streak(h, [], TODAY), 0)

    def test_month_boundary(self):
        h = Habit(title="Daily habit", start_date="2026-01-01")
        logs = [HabitLog(habit_id=h.id, date=d) for d in ("2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02")]
        self.assertEqual(H.current_streak(h, logs, date(2026, 11, 2)), 4)

    def test_log_habit_one_record_per_day_no_future(self):
        h = self.svc.create_habit(title="Logged habit")
        self.svc.log_habit(h.id, date.today(), True)
        self.svc.log_habit(h.id, date.today(), False)
        self.assertEqual(len(self.svc.habit_logs()), 1)
        self.assertFalse(self.svc.habit_logs()[0].done)
        with self.assertRaises(ServiceError):
            self.svc.log_habit(h.id, date.today() + timedelta(days=1))
        with self.assertRaises(ServiceError):
            self.svc.log_habit("ghost")


class AnalyticsAndEmpty(TmpStoreCase):
    def test_everything_handles_empty_data(self):
        s = AppSettings()
        r = M.full_report([], [], [], [], [], [], s, TODAY)
        self.assertEqual(r["observed"]["task_counts"]["total"], 0)
        self.assertIsNone(r["observed"]["adherence"]["rate"])
        self.assertIsNone(r["observed"]["planned_vs_actual"]["ratio"])
        self.assertEqual(r["heuristic_estimates"]["goal_risks"], [])
        report_markdown(r)
        self.assertEqual(compute_alerts([], [], [], [], [], s, TODAY), [])
        self.assertEqual(M.time_by_category([]), {})

    def test_overdue_and_goal_risk_heuristic(self):
        g = Goal(title="Risky goal", deadline=(TODAY + timedelta(days=2)).isoformat())
        t = Task(title="Too much work", goal_id=g.id, estimate_minutes=2000)
        o = Task(title="Old task", deadline=(TODAY - timedelta(days=1)).isoformat())
        s = AppSettings()
        risk = M.goal_risks([g], [t], s, TODAY)[0]
        self.assertEqual(risk["risk"], "high")
        self.assertEqual(risk["kind"], "heuristic estimate")
        self.assertEqual([x.id for x in M.overdue_tasks([t, o], TODAY)], [o.id])

    def test_planned_vs_actual_and_weekly(self):
        t = Task(title="Finished one", status="done", estimate_minutes=60, actual_minutes=90, completed_at=f"{TODAY.isoformat()}T10:00:00")
        pva = M.planned_vs_actual([t])
        self.assertEqual(pva["ratio"], 1.5)
        wk = M.weekly_productivity([t], 4, TODAY)
        self.assertEqual(sum(w["tasks_completed"] for w in wk), 1)
        self.assertEqual(len(wk), 4)

    def test_alerts_deduplicate_and_respect_settings(self):
        s = AppSettings()
        t = Task(title="Overdue one", deadline=(TODAY - timedelta(days=2)).isoformat())
        a1 = compute_alerts([], [t, t], [], [], [], s, TODAY)
        self.assertEqual(len([a for a in a1 if a.kind == "overdue"]), 1)
        s.notify_overdue = False
        self.assertFalse([a for a in compute_alerts([], [t], [], [], [], s, TODAY) if a.kind == "overdue"])
        b = Task(title="Blocked one", status="blocked", blocked_reason="x")
        self.assertTrue([a for a in compute_alerts([], [b], [], [], [], AppSettings(), TODAY) if a.kind == "blocked"])
        h = Habit(title="Habit today", start_date=TODAY.isoformat())
        self.assertTrue([a for a in compute_alerts([], [], [], [h], [], AppSettings(), TODAY) if a.kind == "habit"])


class MLGuard(unittest.TestCase):
    def _done(self, n):
        out = []
        for i in range(n):
            est = 30 + (i % 7) * 15
            out.append(Task(title=f"Done task {i}", status="done", estimate_minutes=est, actual_minutes=int(est * (1.2 + 0.1 * (i % 3))),
                            category="A" if i % 2 else "B", priority=1 + i % 5))
        return out

    def test_insufficient_history_never_claims_trained(self):
        m = DurationModel(min_samples=30)
        ins = m.train(self._done(10))
        self.assertEqual(ins.status, "insufficient_data")
        self.assertEqual(ins.metrics, {})
        self.assertIn("Insufficient historical data", ins.message)
        mins, kind = m.predict_minutes(Task(title="New task", estimate_minutes=60))
        self.assertNotEqual(kind, "ML prediction")

    def test_empty_history(self):
        m = DurationModel()
        ins = m.train([])
        self.assertEqual(ins.status, "insufficient_data")
        self.assertIsNone(ins.correction_ratio)
        self.assertEqual(m.predict_minutes(Task(title="New task", estimate_minutes=60)), (60.0, "your estimate (no history)"))

    def test_trains_with_enough_data_and_reports_real_cv_metrics(self):
        m = DurationModel(min_samples=30)
        ins = m.train(self._done(60))
        if ins.status == "unavailable":
            self.skipTest("scikit-learn not installed")
        self.assertEqual(ins.status, "trained")
        self.assertIn("cv_mae_minutes_model", ins.metrics)
        self.assertEqual(ins.kind, "ML prediction")
        self.assertNotIn("actual_minutes", " ".join(ins.features))      # no target leakage
        mins, kind = m.predict_minutes(Task(title="New task", estimate_minutes=60))
        self.assertEqual(kind, "ML prediction")
        self.assertGreater(mins, 0)

    def test_baseline_ratio(self):
        self.assertIsNone(baseline_ratio(self._done(2)))
        self.assertGreater(baseline_ratio(self._done(9)), 1.0)


class Exports(TmpStoreCase):
    def test_csv_valid_utf8_and_formula_safe(self):
        rows = [{"title": "=cmd|' /C calc'!A0", "note": "héllo, wörld", "n": 3, "tags": ["a", "b"]}]
        data = to_csv(rows)
        text = data.decode("utf-8-sig")
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(parsed[0]["note"], "héllo, wörld")
        self.assertTrue(parsed[0]["title"].startswith("'="))
        self.assertEqual(json.loads(parsed[0]["tags"]), ["a", "b"])
        self.assertEqual(to_csv([], ["a", "b"]).decode("utf-8-sig").strip(), "a,b")

    def test_json_export_valid(self):
        g = self.svc.create_goal(title="Export goal ✓")
        data = json.loads(to_json([g.to_dict()]))
        self.assertEqual(data[0]["title"], "Export goal ✓")

    def test_safe_filenames(self):
        for bad in ("../../etc/passwd", "..\\..\\win.ini", "CON", "", "a/b\\c:d*e?.csv", "   "):
            n = safe_filename(bad)
            self.assertNotIn("/", n); self.assertNotIn("\\", n); self.assertNotIn(":", n)
            self.assertTrue(n and n not in (".", ".."))
        self.assertEqual(safe_filename("goals 2026.csv"), "goals_2026.csv")

    def test_exports_contain_no_secrets(self):
        import os
        os.environ["LLM_API_KEY"] = "sk-TESTSECRET-should-never-appear"
        try:
            self.svc.create_goal(title="Normal goal")
            blob = b"".join(p.read_bytes() for p in self.dir.glob("*.json"))
            blob += to_json(self.svc.settings.to_dict())
            self.assertNotIn(b"TESTSECRET", blob)
            self.assertNotIn("api_key", json.dumps(self.svc.settings.to_dict()).lower())
        finally:
            del os.environ["LLM_API_KEY"]


class DemoMode(TmpStoreCase):
    def test_demo_never_loaded_implicitly(self):
        self.assertEqual(self.svc.goals(), [])
        self.assertFalse(demo.has_demo(self.store))

    def test_load_and_reset_only_touch_demo_records(self):
        real = self.svc.create_goal(title="My real goal")
        rt = self.svc.create_task(title="My real task", goal_id=real.id)
        demo.load_demo(self.store, TODAY)
        self.assertTrue(demo.has_demo(self.store))
        self.assertEqual(self.store.validate_all(), [])
        with self.assertRaises(ValueError):
            demo.load_demo(self.store, TODAY)                  # no accidental double-load
        res, _ = self.svc.propose_schedule(TODAY, datetime(2026, 11, 4, 8, 0))
        self.svc.apply_schedule(res, "x", TODAY)
        removed = demo.reset_demo(self.store)
        self.assertEqual(removed["goals"], 2)
        self.assertEqual([g.id for g in self.svc.goals()], [real.id])
        self.assertEqual([t.id for t in self.svc.tasks()], [rt.id])
        self.assertTrue(all(b.task_id == rt.id for b in self.svc.blocks()))
        self.assertEqual(self.store.validate_all(), [])


class Assistant(TmpStoreCase):
    def test_next_task_and_local_answers_without_llm(self):
        a = self.svc.create_task(title="First thing", priority=5)
        self.svc.create_task(title="Second thing", prerequisites=[a.id])
        r = assistant.answer(self.svc, "What should I work on next?", TODAY)
        self.assertIn("First thing", r.text)
        self.assertEqual(r.source, "local")
        self.assertIn("Connect an LLM", assistant.answer(self.svc, "tell me a joke", TODAY).text)

    def test_reschedule_is_a_proposal_until_confirmed(self):
        self.svc.create_task(title="Needs scheduling", estimate_minutes=60)
        r = assistant.answer(self.svc, "Reschedule my unfinished tasks", TODAY)
        self.assertEqual(r.proposal, {"action": "reschedule"})
        self.assertEqual(self.svc.blocks(), [])                  # nothing applied yet
        msg = assistant.confirm(self.svc, r.proposal, TODAY)
        self.assertIn("Applied and saved", msg)
        self.assertTrue(self.svc.blocks())
        with self.assertRaises(ServiceError):
            assistant.confirm(self.svc, {"action": "delete_everything"})

    def test_context_is_bounded(self):
        for i in range(30):
            self.svc.create_task(title=f"Bulk task {i}")
        ctx = assistant.build_context(self.svc, TODAY)
        self.assertLessEqual(len(ctx["active_tasks"]), 8)
        self.assertNotIn("goals", ctx)

    def test_llm_failure_is_reported_honestly(self):
        from src.llm.base import LLMUnavailable, LLMProvider, ProviderStatus

        class Down(LLMProvider):
            name, model = "down", "m"
            def check(self): return ProviderStatus(False, "down", "m", "x")
            def _chat_once(self, m, j, t): raise LLMUnavailable("offline")
        r = assistant.answer(self.svc, "write me a poem", TODAY, Down())
        self.assertIn("unavailable", r.text)
        self.assertEqual(r.source, "local")

    def test_chat_history_bounded(self):
        self.svc.update_settings(chat_history_limit=10)
        for i in range(25):
            self.svc.append_chat("user", f"m{i}")
        self.assertEqual(len(self.svc.chat_history()), 10)


if __name__ == "__main__":
    unittest.main()
