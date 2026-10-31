"""Application service: all business actions go through here (UI pages never touch files)."""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from src.config.settings import AppSettings
from src.models.entities import (Activity, Goal, Habit, HabitLog, ScheduleBlock, Task, ValidationError,
                                 now_iso, parse_date)
from src.planning.dependencies import validate_prerequisites, would_create_cycle, blocking_reasons
from src.planning.scheduler import (ScheduleResult, SchedulePreview, build_schedule, preview_changes,
                                    split_history)
from src.storage.json_store import JsonStore

log = logging.getLogger(__name__)


class ServiceError(ValueError):
    """User-facing error with a safe message."""


class AppService:
    def __init__(self, store: JsonStore):
        self.store = store

    # ------------------------------------------------------------ loading
    @property
    def settings(self) -> AppSettings:
        return self.store.load_settings()

    def goals(self) -> list[Goal]:
        return self.store.load("goals")

    def tasks(self) -> list[Task]:
        return self.store.load("tasks")

    def blocks(self) -> list[ScheduleBlock]:
        return self.store.load("schedule")

    def habits(self) -> list[Habit]:
        return self.store.load("habits")

    def habit_logs(self) -> list[HabitLog]:
        return self.store.load("habit_logs")

    def activity(self) -> list[Activity]:
        return self.store.load("activity_log")

    # ------------------------------------------------------------ activity
    def log(self, action: str, message: str, entity_type: str = "", entity_id: str = "", **details: Any) -> None:
        acts = self.activity()
        acts.append(Activity(action=action, message=message, entity_type=entity_type, entity_id=entity_id, details=details))
        self.store.save("activity_log", acts[-2000:], backup=False)

    # ------------------------------------------------------------ goals
    def create_goal(self, **fields: Any) -> Goal:
        try:
            g = Goal(**fields)
            g.validate()
        except (ValidationError, TypeError) as exc:
            raise ServiceError(str(exc)) from exc
        goals = self.goals()
        goals.append(g)
        self.store.save("goals", goals)
        self.log("goal_created", f"Created goal '{g.title}'.", "goal", g.id)
        return g

    def update_goal(self, goal_id: str, **changes: Any) -> Goal:
        goals = self.goals()
        for i, g in enumerate(goals):
            if g.id == goal_id:
                new = replace(g, **changes, updated_at=now_iso())
                try:
                    new.validate()
                except ValidationError as exc:
                    raise ServiceError(str(exc)) from exc
                goals[i] = new
                self.store.save("goals", goals)
                self.log("goal_updated", f"Updated goal '{new.title}'.", "goal", goal_id, changes=list(changes))
                return new
        raise ServiceError("Goal not found.")

    def set_goal_status(self, goal_id: str, status: str) -> Goal:
        g = self.update_goal(goal_id, status=status)
        self.log(f"goal_{status}", f"Goal '{g.title}' set to {status}.", "goal", goal_id)
        return g

    def goal_progress(self, goal_id: str) -> dict[str, Any]:
        ts = [t for t in self.tasks() if t.goal_id == goal_id and t.status != "archived"]
        total = sum(t.estimate_minutes for t in ts)
        done = sum(t.estimate_minutes for t in ts if t.status == "done")
        return {"tasks": len(ts), "done": sum(t.status == "done" for t in ts),
                "pct": round(100 * done / total, 1) if total else 0.0}

    # ------------------------------------------------------------ tasks
    def create_task(self, **fields: Any) -> Task:
        try:
            t = Task(**fields)
            t.validate()
        except (ValidationError, TypeError) as exc:
            raise ServiceError(str(exc)) from exc
        tasks = self.tasks()
        tasks.append(t)
        problems = validate_prerequisites(tasks)
        if problems:
            raise ServiceError("; ".join(problems))
        if t.goal_id and t.goal_id not in {g.id for g in self.goals()}:
            raise ServiceError("Goal does not exist.")
        self.store.save("tasks", tasks)
        self.log("task_created", f"Created task '{t.title}'.", "task", t.id)
        return t

    def update_task(self, task_id: str, **changes: Any) -> Task:
        tasks = self.tasks()
        for i, t in enumerate(tasks):
            if t.id != task_id:
                continue
            if "prerequisites" in changes:
                ids = {x.id for x in tasks}
                bad = [p for p in changes["prerequisites"] if p not in ids]
                if bad:
                    raise ServiceError(f"Nonexistent prerequisite ID(s): {', '.join(bad)}")
                if task_id in changes["prerequisites"]:
                    raise ServiceError("A task cannot depend on itself.")
                if would_create_cycle(tasks, task_id, changes["prerequisites"]):
                    raise ServiceError("That prerequisite would create a dependency cycle.")
            new = replace(t, **changes, updated_at=now_iso())
            try:
                new.validate()
            except ValidationError as exc:
                raise ServiceError(str(exc)) from exc
            tasks[i] = new
            self.store.save("tasks", tasks)
            self.log("task_updated", f"Updated task '{new.title}'.", "task", task_id, changes=list(changes))
            return new
        raise ServiceError("Task not found.")

    def archive_task(self, task_id: str) -> Task:
        """Tasks are archived, never silently deleted. Dependents keep the link (and wait)."""
        t = self.update_task(task_id, status="archived")
        self.log("task_archived", f"Archived task '{t.title}'.", "task", task_id)
        return t

    def can_start(self, task_id: str) -> tuple[bool, list[str]]:
        tasks = self.tasks()
        by_id = {t.id: t for t in tasks}
        t = by_id.get(task_id)
        if not t:
            raise ServiceError("Task not found.")
        reasons = blocking_reasons(t, by_id)
        return (not reasons, reasons)

    def start_task(self, task_id: str) -> Task:
        ok, reasons = self.can_start(task_id)
        if not ok:
            raise ServiceError("Cannot start: " + " ".join(reasons))
        t = self.update_task(task_id, status="in_progress")
        self.log("task_started", f"Started '{t.title}'.", "task", task_id)
        return t

    def pause_task(self, task_id: str) -> Task:
        t = self.update_task(task_id, status="paused")
        self.log("task_paused", f"Paused '{t.title}'.", "task", task_id)
        return t

    def complete_task(self, task_id: str, actual_minutes: int | None = None, today: date | None = None) -> Task:
        ok, reasons = self.can_start(task_id)
        task = next(t for t in self.tasks() if t.id == task_id)
        pre_reasons = [r for r in reasons if not r.startswith("Manually")]
        if pre_reasons:
            raise ServiceError("Cannot complete before prerequisites: " + " ".join(pre_reasons))
        changes: dict[str, Any] = {"status": "done", "completed_at": now_iso(), "blocked_reason": ""}
        if actual_minutes is not None:
            if actual_minutes < 0:
                raise ServiceError("Actual time cannot be negative.")
            changes["actual_minutes"] = actual_minutes
        elif task.actual_minutes == 0:
            changes["actual_minutes"] = task.estimate_minutes     # assume as-planned; logged below
        t = self.update_task(task_id, **changes)
        self._mark_blocks(task_id, today or date.today(), "completed")
        self.log("task_completed", f"Completed '{t.title}'.", "task", task_id,
                 actual_minutes=t.actual_minutes, assumed_as_estimated=actual_minutes is None and task.actual_minutes == 0)
        self._maybe_complete_goal(t.goal_id)
        return t

    def _maybe_complete_goal(self, goal_id: str | None) -> None:
        if not goal_id:
            return
        ts = [t for t in self.tasks() if t.goal_id == goal_id and t.status != "archived"]
        if ts and all(t.status == "done" for t in ts):
            g = next((g for g in self.goals() if g.id == goal_id), None)
            if g and g.status == "active":
                self.log("goal_ready", f"All tasks of '{g.title}' are done. You can mark the goal complete.", "goal", goal_id)

    def block_task(self, task_id: str, reason: str) -> Task:
        if not reason.strip():
            raise ServiceError("Please give a reason so the block can be explained later.")
        t = self.update_task(task_id, status="blocked", blocked_reason=reason.strip())
        self.log("task_blocked", f"Blocked '{t.title}': {reason.strip()}", "task", task_id)
        return t

    def unblock_task(self, task_id: str) -> Task:
        t = self.update_task(task_id, status="todo", blocked_reason="")
        self.log("task_unblocked", f"Unblocked '{t.title}'.", "task", task_id)
        return t

    def record_time(self, task_id: str, minutes: int) -> Task:
        if minutes <= 0:
            raise ServiceError("Minutes must be positive.")
        t = next((t for t in self.tasks() if t.id == task_id), None)
        if not t:
            raise ServiceError("Task not found.")
        t = self.update_task(task_id, actual_minutes=t.actual_minutes + minutes)
        self.log("time_recorded", f"Recorded {minutes} min on '{t.title}'.", "task", task_id, minutes=minutes)
        return t

    def skip_today(self, task_id: str, today: date | None = None) -> Task:
        today = today or date.today()
        t = next((t for t in self.tasks() if t.id == task_id), None)
        if not t:
            raise ServiceError("Task not found.")
        t = self.update_task(task_id, skip_dates=sorted(set(t.skip_dates) | {today.isoformat()}))
        self._mark_blocks(task_id, today, "skipped")
        self.log("task_skipped", f"Skipped '{t.title}' for {today.isoformat()}.", "task", task_id)
        return t

    def mark_missed(self, task_id: str, today: date | None = None) -> Task:
        today = today or date.today()
        t = next((t for t in self.tasks() if t.id == task_id), None)
        if not t:
            raise ServiceError("Task not found.")
        t = self.update_task(task_id, miss_count=t.miss_count + 1, status="todo" if t.status == "in_progress" else t.status)
        self._mark_blocks(task_id, today, "missed")
        self.log("task_missed", f"Marked '{t.title}' missed on {today.isoformat()}. Reschedule to rebuild the plan.",
                 "task", task_id)
        return t

    def _mark_blocks(self, task_id: str, day: date, status: str) -> None:
        blocks = self.blocks()
        changed = False
        for b in blocks:
            if b.task_id == task_id and b.status == "planned" and b.date == day.isoformat():
                b.status, b.updated_at, changed = status, now_iso(), True
        if changed:
            self.store.save("schedule", blocks)

    # ------------------------------------------------------------ settings
    def update_settings(self, **changes: Any) -> AppSettings:
        try:
            s = replace(self.settings, **changes)
            s.validate()
        except (ValueError, TypeError) as exc:
            raise ServiceError(str(exc)) from exc
        self.store.save_settings(s)
        self.log("settings_updated", "Settings updated.", "settings", "", changed=list(changes))
        return s

    def change_deadline(self, kind: str, entity_id: str, deadline: str | None) -> None:
        if kind == "goal":
            self.update_goal(entity_id, deadline=deadline)
        else:
            self.update_task(entity_id, deadline=deadline)
        self.log("deadline_changed", f"Deadline of {kind} changed to {deadline}.", kind, entity_id)

    # ------------------------------------------------------------ scheduling
    def propose_schedule(self, today: date | None = None, now: datetime | None = None) -> tuple[ScheduleResult, SchedulePreview]:
        """Compute (but do not save) a new plan and its comparison with the current one."""
        now = now or datetime.now()
        today = today or now.date()
        s = self.settings
        tasks, goals, existing = self.tasks(), self.goals(), self.blocks()
        keep, old_planned = split_history(existing, today)
        fixed = [b for b in keep if b.date == today.isoformat() and b.status in ("completed", "missed", "skipped")]
        result = build_schedule(tasks, goals, s, today, now=now, fixed_blocks=fixed)
        return result, preview_changes(old_planned, result, tasks, s)

    def apply_schedule(self, result: ScheduleResult, reason: str = "Schedule regenerated", today: date | None = None) -> dict[str, int]:
        """Persist a proposed plan. Old planned blocks are marked 'superseded', never deleted."""
        today = today or date.today()
        existing = self.blocks()
        old_ids = {b.id for b in existing}
        kept_new, superseded, auto_missed = 0, 0, 0
        for b in existing:      # planned blocks in the past that were never completed are history: mark them missed
            if b.status == "planned" and date.fromisoformat(b.date) < today:
                b.status, b.updated_at = "missed", now_iso()
                auto_missed += 1
        new_by_id = {b.id: b for b in result.blocks}
        for b in existing:
            if b.status == "planned" and date.fromisoformat(b.date) >= today:
                if b.id in new_by_id:
                    kept_new += 1                    # identical block -> untouched (stable re-runs)
                else:
                    b.status, b.updated_at = "superseded", now_iso()
                    superseded += 1
        added = [b for b in result.blocks if b.id not in old_ids or
                 next(e for e in existing if e.id == b.id).status != "planned"]
        # a previously superseded block with the same id may be reused: reactivate it
        reactivated = 0
        by_existing = {b.id: b for b in existing}
        final_new = []
        for b in added:
            if b.id in by_existing:
                by_existing[b.id].status, by_existing[b.id].updated_at = "planned", now_iso()
                reactivated += 1
            else:
                final_new.append(b)
        self.store.save_many({"schedule": existing + final_new})
        self.log("schedule_applied", f"{reason}: {len(final_new) + reactivated} block(s) added, {superseded} superseded, {kept_new} unchanged"
                 + (f", {auto_missed} past block(s) marked missed." if auto_missed else "."),
                 "schedule", "", added=len(final_new) + reactivated, superseded=superseded, unchanged=kept_new, auto_missed=auto_missed,
                 conflicts=[c.message for c in result.conflicts][:20])
        return {"added": len(final_new) + reactivated, "superseded": superseded, "unchanged": kept_new}

    def reschedule(self, today: date | None = None, now: datetime | None = None, reason: str = "Reschedule") -> tuple[ScheduleResult, SchedulePreview, dict[str, int] | None]:
        """Propose and, if the change is not significant, apply immediately. Otherwise the caller must confirm."""
        res, prev = self.propose_schedule(today, now)
        if prev.significant:
            return res, prev, None
        stats = self.apply_schedule(res, reason, today)
        return res, prev, stats

    # ------------------------------------------------------------ plan import
    def apply_plan(self, plan: Any, goal_id: str | None = None, goal_fields: dict[str, Any] | None = None) -> tuple[Goal | None, list[Task]]:
        """Persist a validated plan (src.llm.plan_schema.Plan) as a goal + tasks atomically."""
        goal = None
        goals, tasks = self.goals(), self.tasks()
        if goal_id is None:
            goal = Goal(**(goal_fields or {"title": plan.goal_summary[:120] or "New goal"}))
            goal.validate()
            goals.append(goal)
            goal_id = goal.id
        key_to_id = {pt.key: Task(title=pt.title).id for pt in plan.tasks}
        new_tasks: list[Task] = []
        for pt in plan.tasks:
            t = Task(
                id=key_to_id[pt.key], title=pt.title, goal_id=goal_id, description=pt.description,
                completion_criteria=pt.completion_criteria, milestone=pt.milestone, category=pt.category,
                priority=pt.priority, estimate_minutes=pt.estimate_minutes, deadline=pt.deadline,
                prerequisites=[key_to_id[k] for k in pt.prerequisites], splittable=pt.splittable)
            t.validate()
            new_tasks.append(t)
        problems = validate_prerequisites(tasks + new_tasks)
        if problems:
            raise ServiceError("; ".join(problems))
        self.store.save_many({"goals": goals, "tasks": tasks + new_tasks})
        self.log("plan_applied", f"Added {len(new_tasks)} task(s) from plan (source: {plan.source}).", "goal", goal_id or "")
        return goal, new_tasks

    # ------------------------------------------------------------ habits
    def create_habit(self, **fields: Any) -> Habit:
        try:
            h = Habit(**fields)
            h.validate()
        except (ValidationError, TypeError) as exc:
            raise ServiceError(str(exc)) from exc
        hs = self.habits()
        hs.append(h)
        self.store.save_many({"habits": hs})
        self.log("habit_created", f"Created habit '{h.title}'.", "habit", h.id)
        return h

    def log_habit(self, habit_id: str, day: date | None = None, done: bool = True, note: str = "") -> HabitLog:
        day = day or date.today()
        if day > date.today():
            raise ServiceError("Cannot log a habit for a future date.")
        if habit_id not in {h.id for h in self.habits()}:
            raise ServiceError("Habit not found.")
        logs = self.habit_logs()
        for l in logs:
            if l.habit_id == habit_id and l.date == day.isoformat():
                l.done, l.note = done, note          # one record per habit/day: update in place
                self.store.save("habit_logs", logs)
                return l
        l = HabitLog(habit_id=habit_id, date=day.isoformat(), done=done, note=note)
        logs.append(l)
        self.store.save("habit_logs", logs)
        return l

    def set_habit_active(self, habit_id: str, active: bool) -> None:
        hs = self.habits()
        for h in hs:
            if h.id == habit_id:
                h.active = active
        self.store.save("habits", hs)

    # ------------------------------------------------------------ chat
    def chat_history(self) -> list[dict[str, Any]]:
        return self.store.load("chat_history")

    def append_chat(self, role: str, content: str, **extra: Any) -> None:
        hist = self.chat_history()
        hist.append({"role": role, "content": content[:8000], "ts": now_iso(), **extra})
        self.store.save("chat_history", hist[-self.settings.chat_history_limit:], backup=False)

    def clear_chat(self) -> None:
        self.store.save("chat_history", [], backup=False)
