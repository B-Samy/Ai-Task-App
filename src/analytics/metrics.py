"""Observed metrics and heuristic estimates. Everything handles empty data. No ML in this module."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from src.analytics import habits as H
from src.config.settings import AppSettings
from src.models.entities import Activity, Goal, Habit, HabitLog, ScheduleBlock, Task, parse_date
from src.planning.scheduler import available_capacity, remaining_minutes


def task_counts(tasks: list[Task]) -> dict[str, int]:
    live = [t for t in tasks if t.status != "archived"]
    return {"completed": sum(t.status == "done" for t in live),
            "pending": sum(t.status != "done" for t in live), "total": len(live)}


def overdue_tasks(tasks: list[Task], today: date, goals: list[Goal] | None = None) -> list[Task]:
    gd = {g.id: parse_date(g.deadline) for g in goals or []}
    out = []
    for t in tasks:
        if t.status in ("done", "archived"):
            continue
        dl = parse_date(t.deadline) or gd.get(t.goal_id)
        if dl and dl < today:
            out.append(t)
    return out


def planned_vs_actual(tasks: list[Task]) -> dict[str, Any]:
    done = [t for t in tasks if t.status == "done" and t.actual_minutes > 0]
    p, a = sum(t.estimate_minutes for t in done), sum(t.actual_minutes for t in done)
    return {"tasks": len(done), "planned_minutes": p, "actual_minutes": a,
            "ratio": round(a / p, 2) if p else None, "kind": "observed"}


def weekly_productivity(tasks: list[Task], weeks: int, today: date) -> list[dict[str, Any]]:
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    rows = []
    for w in range(weeks):
        ws = start + timedelta(weeks=w)
        we = ws + timedelta(days=6)
        done = [t for t in tasks if t.completed_at and ws <= parse_date(t.completed_at[:10]) <= we]  # type: ignore[operator]
        rows.append({"week_start": ws.isoformat(), "tasks_completed": len(done),
                     "minutes": sum(t.actual_minutes for t in done)})
    return rows


def schedule_adherence(blocks: list[ScheduleBlock], today: date) -> dict[str, Any]:
    """Share of blocks whose day has passed that were completed (observed)."""
    past = [b for b in blocks if b.status in ("completed", "missed", "skipped")
            or (b.status == "planned" and date.fromisoformat(b.date) < today)]
    done = sum(b.status == "completed" for b in past)
    return {"blocks": len(past), "completed": done, "rate": round(done / len(past), 3) if past else None}


def time_by_category(tasks: list[Task]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for t in tasks:
        if t.actual_minutes:
            out[t.category] += t.actual_minutes
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def goal_risks(goals: list[Goal], tasks: list[Task], settings: AppSettings, today: date) -> list[dict[str, Any]]:
    """Heuristic deadline risk: remaining work vs. available capacity (NOT an ML prediction)."""
    out = []
    for g in goals:
        if g.status != "active":
            continue
        ts = [t for t in tasks if t.goal_id == g.id and t.status not in ("done", "archived")]
        need = sum(remaining_minutes(t, settings.buffer_percent) for t in ts)
        total = sum(t.estimate_minutes for t in tasks if t.goal_id == g.id and t.status != "archived")
        done = sum(t.estimate_minutes for t in tasks if t.goal_id == g.id and t.status == "done")
        dl = parse_date(g.deadline)
        cap = available_capacity(today, dl, settings, g) if dl else None
        if dl is None:
            level, ratio = "none", None
        elif need == 0:
            level, ratio = "done" if total else "none", 0.0
        elif cap == 0:
            level, ratio = "high", None
        else:
            ratio = need / cap
            level = "high" if ratio > 1.0 else "medium" if ratio > 0.8 else "low"
        out.append({"goal_id": g.id, "title": g.title, "need_minutes": need, "capacity_minutes": cap,
                    "ratio": round(ratio, 2) if ratio is not None else None, "risk": level,
                    "progress_pct": round(100 * done / total, 1) if total else 0.0, "kind": "heuristic estimate"})
    return out


def rescheduling_frequency(activity: list[Activity], weeks: int, today: date) -> list[dict[str, Any]]:
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    rows = []
    for w in range(weeks):
        ws = start + timedelta(weeks=w)
        we = ws + timedelta(days=6)
        n = sum(1 for a in activity if a.action == "schedule_applied" and ws <= parse_date(a.timestamp[:10]) <= we)  # type: ignore[operator]
        rows.append({"week_start": ws.isoformat(), "reschedules": n})
    return rows


def habit_summary(habits: list[Habit], logs: list[HabitLog], today: date) -> list[dict[str, Any]]:
    out = []
    for h in habits:
        r = H.weekly_monthly(h, logs, today)
        out.append({"habit_id": h.id, "title": h.title, "streak": H.current_streak(h, logs, today),
                    "longest": H.longest_streak(h, logs, today), "week_rate": r["week"], "month_rate": r["month"],
                    "due_today": H.is_due(h, today), "done_today": any(l.habit_id == h.id and l.date == today.isoformat() and l.done for l in logs)})
    return out


def month_progress(tasks: list[Task], today: date) -> dict[str, Any]:
    first = today.replace(day=1)
    done = [t for t in tasks if t.completed_at and parse_date(t.completed_at[:10]) >= first]  # type: ignore[operator]
    return {"month": first.strftime("%Y-%m"), "tasks_completed": len(done), "minutes": sum(t.actual_minutes for t in done)}


def full_report(goals, tasks, blocks, habits, logs, activity, settings: AppSettings, today: date) -> dict[str, Any]:
    return {
        "generated": today.isoformat(),
        "observed": {"task_counts": task_counts(tasks), "overdue": len(overdue_tasks(tasks, today, goals)),
                     "planned_vs_actual": planned_vs_actual(tasks), "weekly": weekly_productivity(tasks, 8, today),
                     "adherence": schedule_adherence(blocks, today), "time_by_category": time_by_category(tasks),
                     "reschedules": rescheduling_frequency(activity, 8, today), "habits": habit_summary(habits, logs, today)},
        "heuristic_estimates": {"goal_risks": goal_risks(goals, tasks, settings, today)},
        "ml_predictions": "see ML Insights; none unless a model has been trained on enough history",
    }
