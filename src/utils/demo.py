"""Synthetic demo data. Loaded only on explicit request; every record is flagged is_demo=True."""
from __future__ import annotations

from datetime import date, timedelta

from src.models.entities import Goal, Habit, HabitLog, Task
from src.storage.json_store import JsonStore, LIST_COLLECTIONS


def has_demo(store: JsonStore) -> bool:
    return any(getattr(r, "is_demo", False) for n in ("goals", "tasks", "habits") for r in store.load(n))


def load_demo(store: JsonStore, today: date | None = None) -> dict[str, int]:
    """Append demo records (idempotent: refuses if demo data already exists)."""
    today = today or date.today()
    if has_demo(store):
        raise ValueError("Demo data is already loaded. Reset it first if you want a fresh copy.")
    g1 = Goal(title="[Demo] Learn machine learning in 90 days", category="Learning", priority=4,
              deadline=(today + timedelta(days=90)).isoformat(), success_criteria="Complete 3 projects and a final capstone.",
              skill_level="Knows Python and basic statistics", is_demo=True)
    g2 = Goal(title="[Demo] Run a 10K", category="Health", priority=3, deadline=(today + timedelta(days=60)).isoformat(), is_demo=True)
    t1 = Task(title="[Demo] Review linear algebra basics", goal_id=g1.id, estimate_minutes=120, category="Learning", priority=4,
              completion_criteria="Solve 20 practice problems", is_demo=True)
    t2 = Task(title="[Demo] Implement linear regression from scratch", goal_id=g1.id, estimate_minutes=180, category="Learning",
              prerequisites=[t1.id], priority=4, is_demo=True)
    t3 = Task(title="[Demo] Train a classifier on a public dataset", goal_id=g1.id, estimate_minutes=240, category="Learning",
              prerequisites=[t2.id], priority=3, is_demo=True)
    t4 = Task(title="[Demo] Plan weekly running schedule", goal_id=g2.id, estimate_minutes=45, category="Health", is_demo=True)
    t5 = Task(title="[Demo] Complete first 5K practice run", goal_id=g2.id, estimate_minutes=60, category="Health",
              prerequisites=[t4.id], splittable=False, is_demo=True)
    h1 = Habit(title="[Demo] Read 20 pages", recurrence="daily", start_date=(today - timedelta(days=14)).isoformat(), goal_id=g1.id, is_demo=True)
    h2 = Habit(title="[Demo] Stretch", recurrence="weekdays", start_date=(today - timedelta(days=14)).isoformat(), is_demo=True)
    logs = [HabitLog(habit_id=h1.id, date=(today - timedelta(days=i)).isoformat(), is_demo=True) for i in (1, 2, 3, 5, 6)]
    store.save_many({"goals": store.load("goals") + [g1, g2], "tasks": store.load("tasks") + [t1, t2, t3, t4, t5],
                     "habits": store.load("habits") + [h1, h2], "habit_logs": store.load("habit_logs") + logs})
    return {"goals": 2, "tasks": 5, "habits": 2, "habit_logs": len(logs)}


def reset_demo(store: JsonStore) -> dict[str, int]:
    """Remove ONLY records flagged is_demo (plus schedule blocks of demo tasks). Real data is untouched."""
    state = {n: store.load(n) for n in LIST_COLLECTIONS}
    demo_tasks = {t.id for t in state["tasks"] if t.is_demo}
    removed = {
        "goals": sum(g.is_demo for g in state["goals"]), "tasks": len(demo_tasks),
        "habits": sum(h.is_demo for h in state["habits"]),
    }
    new = {
        "goals": [g for g in state["goals"] if not g.is_demo],
        "tasks": [t for t in state["tasks"] if not t.is_demo],
        "schedule": [b for b in state["schedule"] if b.task_id not in demo_tasks],
        "habits": [h for h in state["habits"] if not h.is_demo],
        "habit_logs": [l for l in state["habit_logs"] if not l.is_demo],
        "activity_log": [a for a in state["activity_log"] if not a.is_demo],
    }
    store.save_many(new)
    return removed
