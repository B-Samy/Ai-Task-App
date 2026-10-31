"""In-app alerts. Deduplicated by stable key. (No background delivery: alerts exist only while the app is open.)"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from src.analytics import habits as H
from src.analytics.metrics import goal_risks, overdue_tasks
from src.config.settings import AppSettings
from src.models.entities import Goal, Habit, HabitLog, ScheduleBlock, Task, parse_date
from src.planning.dependencies import blocking_reasons


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    severity: str     # info | warning | critical
    message: str


def compute_alerts(goals: list[Goal], tasks: list[Task], blocks: list[ScheduleBlock], habits: list[Habit],
                   logs: list[HabitLog], s: AppSettings, today: date, recent_reschedule: str | None = None) -> list[Alert]:
    out: dict[str, Alert] = {}

    def add(a: Alert) -> None:
        out.setdefault(a.key, a)

    by_id = {t.id: t for t in tasks}
    if s.notify_overdue:
        for t in overdue_tasks(tasks, today, goals):
            add(Alert(f"overdue:{t.id}", "overdue", "critical", f"Overdue: '{t.title}'"))
    if s.notify_deadlines:
        horizon = today + timedelta(days=s.deadline_warning_days)
        for t in tasks:
            dl = parse_date(t.deadline)
            if t.status not in ("done", "archived") and dl and today <= dl <= horizon:
                add(Alert(f"deadline:{t.id}", "deadline", "warning", f"'{t.title}' is due {dl.isoformat()}"))
    if s.notify_at_risk:
        for r in goal_risks(goals, tasks, s, today):
            if r["risk"] == "high":
                add(Alert(f"risk:{r['goal_id']}", "at_risk", "critical", f"Goal at risk: '{r['title']}' (needs more time than is available)"))
    if s.notify_blocked:
        for t in tasks:
            if t.status == "blocked":
                add(Alert(f"blocked:{t.id}", "blocked", "warning", f"Blocked: '{t.title}' - {t.blocked_reason or 'no reason'}"))
            elif t.status in ("todo", "in_progress") and any(
                    (by_id.get(p) is not None and by_id[p].status == "blocked") for p in t.prerequisites):
                add(Alert(f"blockedby:{t.id}", "blocked", "info", f"'{t.title}' is waiting on a blocked prerequisite"))
    if s.notify_missed:
        for b in blocks:
            if b.status == "missed" or (b.status == "planned" and date.fromisoformat(b.date) < today):
                t = by_id.get(b.task_id)
                if t and t.status != "done":
                    add(Alert(f"missed:{b.id}", "missed", "warning", f"Missed block for '{t.title}' on {b.date}"))
    if recent_reschedule:
        add(Alert(f"resched:{recent_reschedule}", "reschedule", "info", "Your schedule was recently rebuilt. See Schedule > Activity."))
    if s.notify_habits:
        for h in habits:
            if H.is_due(h, today) and not any(l.habit_id == h.id and l.date == today.isoformat() and l.done for l in logs):
                add(Alert(f"habit:{h.id}:{today.isoformat()}", "habit", "info", f"Habit due today: '{h.title}'"))
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out.values(), key=lambda a: (order[a.severity], a.key))
