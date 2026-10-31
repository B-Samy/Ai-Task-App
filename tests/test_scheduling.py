import unittest
from datetime import date, datetime, timedelta

from tests.helpers import TmpStoreCase
from src.config.settings import AppSettings, hhmm_to_minutes
from src.models.entities import Goal, Task
from src.planning.dependencies import find_cycle, topological_order, validate_prerequisites, would_create_cycle, blocking_reasons
from src.planning.scheduler import build_schedule, available_capacity
from src.services.app_service import ServiceError

MON = date(2026, 11, 2)           # a Monday
NOW = datetime(2026, 11, 2, 7, 0)


def sched(tasks, goals=(), start=MON, **cfg):
    s = AppSettings(**cfg)
    return build_schedule(list(tasks), list(goals), s, start, now=NOW), s


class SchedulerRules(unittest.TestCase):
    def test_respects_working_hours_days_and_capacity(self):
        ts = [Task(title=f"Task number {i}", estimate_minutes=120, splittable=True) for i in range(8)]
        r, s = sched(ts, buffer_percent=0, daily_capacity_minutes=240)
        self.assertTrue(r.blocks)
        per_day: dict[str, int] = {}
        for b in r.blocks:
            self.assertGreaterEqual(hhmm_to_minutes(b.start), hhmm_to_minutes(s.day_start))
            self.assertLessEqual(hhmm_to_minutes(b.end), hhmm_to_minutes(s.day_end))
            self.assertIn(date.fromisoformat(b.date).weekday(), s.working_days)
            per_day[b.date] = per_day.get(b.date, 0) + b.minutes
        self.assertTrue(all(v <= 240 for v in per_day.values()), per_day)
        self.assertEqual(sum(b.minutes for b in r.blocks), 8 * 120)

    def test_no_overlaps_and_breaks(self):
        ts = [Task(title=f"Another task {i}", estimate_minutes=50) for i in range(10)]
        r, s = sched(ts, break_minutes=10)
        by_day: dict[str, list] = {}
        for b in r.blocks:
            by_day.setdefault(b.date, []).append(b)
        for blocks in by_day.values():
            blocks.sort(key=lambda b: b.start)
            for a, b in zip(blocks, blocks[1:]):
                self.assertGreaterEqual(hhmm_to_minutes(b.start) - hhmm_to_minutes(a.end), 10)

    def test_dependencies_respected_in_order(self):
        a = Task(title="First step", estimate_minutes=60, priority=1)
        b = Task(title="Second step", estimate_minutes=60, priority=5, prerequisites=[a.id], deadline="2026-11-02")
        r, _ = sched([b, a])
        fa = min((x.date, x.start) for x in r.blocks if x.task_id == a.id)
        lb = [(x.date, x.start) for x in r.blocks if x.task_id == b.id]
        lastA = max((x.date, x.end) for x in r.blocks if x.task_id == a.id)
        self.assertGreaterEqual(min(lb)[0] + min(lb)[1], lastA[0] + lastA[1])

    def test_dependent_waits_for_all_split_blocks_of_prerequisite(self):
        a = Task(title="Long prerequisite", estimate_minutes=600)
        b = Task(title="Dependent task", estimate_minutes=30, prerequisites=[a.id], priority=5)
        r, _ = sched([a, b], buffer_percent=0)
        a_end = max((x.date, x.end) for x in r.blocks if x.task_id == a.id)
        b_start = min((x.date, x.start) for x in r.blocks if x.task_id == b.id)
        self.assertGreaterEqual(b_start, a_end)

    def test_cycles_rejected(self):
        a = Task(title="Task A here"); b = Task(title="Task B here", prerequisites=[a.id]); a.prerequisites = [b.id]
        self.assertIsNotNone(find_cycle([a, b]))
        self.assertTrue(validate_prerequisites([a, b]))
        r, _ = sched([a, b])
        self.assertEqual(r.blocks, [])
        self.assertEqual(r.conflicts[0].kind, "invalid")
        self.assertFalse(r.feasible)

    def test_would_create_cycle_and_topo(self):
        a = Task(title="Task A here"); b = Task(title="Task B here", prerequisites=[a.id]); c = Task(title="Task C here", prerequisites=[b.id])
        self.assertTrue(would_create_cycle([a, b, c], a.id, [c.id]))
        self.assertFalse(would_create_cycle([a, b, c], c.id, [a.id]))
        self.assertEqual(topological_order([c, b, a]), [a.id, b.id, c.id])

    def test_impossible_deadline_flagged_not_hidden(self):
        t = Task(title="Huge project", estimate_minutes=3000, deadline="2026-11-04")
        g = Goal(title="Big goal", deadline="2026-11-04")
        t.goal_id = g.id
        r, _ = sched([t], [g])
        kinds = {c.kind for c in r.conflicts}
        self.assertIn("late", kinds)
        self.assertIn("capacity", kinds)
        self.assertFalse(r.feasible)
        self.assertIn("WARNING", r.explanations[t.id])

    def test_feasible_schedule_has_no_conflicts(self):
        t = Task(title="Small job", estimate_minutes=60, deadline="2026-11-10")
        r, _ = sched([t])
        self.assertTrue(r.feasible)
        self.assertEqual(r.conflicts, [])

    def test_non_splittable_task_too_long_is_flagged(self):
        t = Task(title="Indivisible beast", estimate_minutes=500, splittable=False)
        r, _ = sched([t], daily_capacity_minutes=240)
        self.assertTrue(any(c.kind == "capacity" for c in r.conflicts))
        self.assertEqual([b for b in r.blocks if b.task_id == t.id], [])

    def test_non_splittable_stays_in_one_block(self):
        filler = Task(title="Filler task here", estimate_minutes=180, priority=5)
        t = Task(title="Atomic ninety", estimate_minutes=90, splittable=False, priority=1)
        r, _ = sched([filler, t], buffer_percent=0, max_block_minutes=120)
        self.assertEqual(len([b for b in r.blocks if b.task_id == t.id]), 1)

    def test_priority_and_deadline_ordering_with_explanation(self):
        low = Task(title="Low priority", estimate_minutes=240, priority=1)
        urgent = Task(title="Urgent due soon", estimate_minutes=240, priority=3, deadline="2026-11-02")
        r, _ = sched([low, urgent], buffer_percent=0)
        first = r.blocks[0]
        self.assertEqual(first.task_id, urgent.id)
        self.assertIn("Delayed", r.explanations[low.id])
        self.assertIn("Score", r.explanations[urgent.id])

    def test_priority_protection_setting(self):
        hi = Task(title="High priority work", estimate_minutes=240, priority=4)
        urgent_low = Task(title="Urgent but low", estimate_minutes=240, priority=1, deadline="2026-11-02")
        r, _ = sched([hi, urgent_low], buffer_percent=0, protect_high_priority=True)
        self.assertEqual(r.blocks[0].task_id, hi.id)
        r2, _ = sched([hi, urgent_low], buffer_percent=0, protect_high_priority=False)
        self.assertEqual(r2.blocks[0].task_id, urgent_low.id)

    def test_blocked_task_and_its_dependents_not_scheduled(self):
        a = Task(title="Blocked prereq", status="blocked", blocked_reason="waiting for access")
        b = Task(title="Depends on blocked", prerequisites=[a.id])
        c = Task(title="Independent work")
        r, _ = sched([a, b, c])
        ids = {x.task_id for x in r.blocks}
        self.assertEqual(ids, {c.id})
        self.assertIn("Depends on blocked", " ".join(x.message for x in r.conflicts))
        reasons = blocking_reasons(b, {a.id: a, b.id: b})
        self.assertTrue(reasons and "Blocked prereq" in reasons[0])

    def test_completed_prereq_satisfies_dependency(self):
        a = Task(title="Already done", status="done", actual_minutes=30)
        b = Task(title="Next up now", prerequisites=[a.id])
        r, _ = sched([a, b])
        self.assertEqual({x.task_id for x in r.blocks}, {b.id})

    def test_paused_goal_tasks_not_scheduled(self):
        g = Goal(title="Paused goal", status="paused")
        t = Task(title="Belongs to paused", goal_id=g.id)
        r, _ = sched([t], [g])
        self.assertEqual(r.blocks, [])

    def test_goal_availability_and_daily_minutes(self):
        g = Goal(title="Weekend study", available_days=[6], daily_minutes=60)
        t = Task(title="Weekend learning", goal_id=g.id, estimate_minutes=120)
        r, _ = sched([t], [g], working_days=[0, 1, 2, 3, 4, 5, 6], buffer_percent=0)
        self.assertTrue(all(date.fromisoformat(b.date).weekday() == 6 for b in r.blocks))
        self.assertTrue(all(b.minutes <= 60 for b in r.blocks))

    def test_skip_date_respected_and_earliest_start(self):
        t = Task(title="Skip me today", skip_dates=[MON.isoformat()], earliest_start="2026-11-03")
        r, _ = sched([t])
        self.assertGreaterEqual(r.blocks[0].date, "2026-11-03")

    def test_stable_repeated_scheduling(self):
        ts = [Task(title=f"Stable task {i}", estimate_minutes=45 + i, priority=1 + i % 5) for i in range(12)]
        r1, _ = sched(ts)
        r2, _ = sched(list(reversed(ts)))
        sig = lambda r: [(b.id, b.task_id, b.date, b.start, b.end) for b in r.blocks]
        self.assertEqual(sig(r1), sig(r2))

    def test_capacity_helper(self):
        s = AppSettings()
        self.assertEqual(available_capacity(MON, MON + timedelta(days=6), s), 5 * 240)
        self.assertEqual(available_capacity(MON, None, s), 0)

    def test_start_mid_day_uses_now(self):
        t = Task(title="Late start", estimate_minutes=30)
        s = AppSettings()
        r = build_schedule([t], [], s, MON, now=datetime(2026, 11, 2, 13, 3))
        self.assertGreaterEqual(hhmm_to_minutes(r.blocks[0].start), 13 * 60 + 5)


class Rescheduling(TmpStoreCase):
    def setUp(self):
        super().setUp()
        self.g = self.svc.create_goal(title="Learn things", deadline="2026-12-31")
        self.a = self.svc.create_task(title="Step one task", goal_id=self.g.id, estimate_minutes=120)
        self.b = self.svc.create_task(title="Step two task", goal_id=self.g.id, estimate_minutes=120, prerequisites=[self.a.id])
        res, prev = self.svc.propose_schedule(MON, NOW)
        self.svc.apply_schedule(res, "initial", MON)

    def test_reapplying_same_inputs_changes_nothing(self):
        before = [(b.id, b.status) for b in self.svc.blocks()]
        res, prev = self.svc.propose_schedule(MON, NOW)
        self.assertEqual(prev.moved, [])
        stats = self.svc.apply_schedule(res, "again", MON)
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["superseded"], 0)
        self.assertEqual([(b.id, b.status) for b in self.svc.blocks()], before)

    def test_missed_task_rescheduled_and_history_preserved(self):
        self.svc.mark_missed(self.a.id, MON)
        day2 = MON + timedelta(days=1)
        res, prev = self.svc.propose_schedule(day2, datetime(2026, 11, 3, 7, 0))
        stats = self.svc.apply_schedule(res, "after miss", day2)
        blocks = self.svc.blocks()
        missed = [b for b in blocks if b.status == "missed"]
        self.assertTrue(missed and all(b.date == MON.isoformat() for b in missed))     # history intact
        planned = [b for b in blocks if b.status == "planned"]
        self.assertTrue(planned and all(b.date >= day2.isoformat() for b in planned))
        self.assertTrue(any(a.action == "schedule_applied" for a in self.svc.activity()))
        self.assertTrue(any(a.action == "task_missed" for a in self.svc.activity()))
        self.assertEqual(self.svc.tasks()[0].miss_count, 1)

    def test_completed_work_preserved_and_unlocks_dependent(self):
        self.svc.complete_task(self.a.id, actual_minutes=100, today=MON)
        t = {t.id: t for t in self.svc.tasks()}
        self.assertEqual(t[self.a.id].status, "done")
        self.assertEqual(t[self.a.id].actual_minutes, 100)
        completed_blocks = [b for b in self.svc.blocks() if b.task_id == self.a.id and b.status == "completed"]
        self.assertTrue(completed_blocks)
        res, prev = self.svc.propose_schedule(MON, datetime(2026, 11, 2, 12, 0))
        self.assertEqual({b.task_id for b in res.blocks}, {self.b.id})
        self.svc.apply_schedule(res, "after completion", MON)
        self.assertTrue([b for b in self.svc.blocks() if b.task_id == self.a.id and b.status == "completed"])

    def test_old_blocks_superseded_not_deleted(self):
        n_before = len(self.svc.blocks())
        self.svc.update_settings(daily_capacity_minutes=60)
        res, prev = self.svc.propose_schedule(MON, NOW)
        self.assertTrue(prev.significant)
        self.svc.apply_schedule(res, "capacity cut", MON)
        blocks = self.svc.blocks()
        self.assertGreaterEqual(len(blocks), n_before)
        self.assertTrue(any(b.status == "superseded" for b in blocks))

    def test_preview_flags_significant_and_reschedule_requires_confirmation(self):
        self.svc.update_settings(daily_capacity_minutes=60)
        res, prev, stats = self.svc.reschedule(MON, NOW)
        self.assertTrue(prev.significant)
        self.assertIsNone(stats)                      # not applied silently

    def test_skip_today_marks_block_and_moves_task(self):
        self.svc.skip_today(self.a.id, MON)
        self.assertTrue(any(b.status == "skipped" for b in self.svc.blocks() if b.task_id == self.a.id))
        res, _ = self.svc.propose_schedule(MON, datetime(2026, 11, 2, 9, 0))
        self.assertTrue(all(b.date > MON.isoformat() for b in res.blocks if b.task_id == self.a.id))

    def test_cannot_start_or_complete_with_unmet_prerequisite(self):
        with self.assertRaises(ServiceError) as e:
            self.svc.start_task(self.b.id)
        self.assertIn("Step one task", str(e.exception))
        with self.assertRaises(ServiceError):
            self.svc.complete_task(self.b.id)
        ok, reasons = self.svc.can_start(self.b.id)
        self.assertFalse(ok)

    def test_change_deadline_logged(self):
        self.svc.change_deadline("task", self.b.id, "2026-11-30")
        self.assertTrue(any(a.action == "deadline_changed" for a in self.svc.activity()))
