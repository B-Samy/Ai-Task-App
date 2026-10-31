"""Deterministic scheduling engine. The LLM never controls any constraint in this module."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from src.config.settings import AppSettings, hhmm_to_minutes, minutes_to_hhmm
from src.models.entities import Goal, ScheduleBlock, Task, parse_date
from src.planning.dependencies import transitive_dependents, validate_prerequisites

ACTIVE_TASK = {"todo", "in_progress", "paused"}
_NS = uuid.UUID("6f1c1d7e-2b9a-4a53-9a43-5e0d5c2d1a11")


@dataclass
class Conflict:
    kind: str            # late | unscheduled | capacity | invalid | blocked
    task_id: str | None
    message: str


@dataclass
class ScheduleResult:
    blocks: list[ScheduleBlock]
    conflicts: list[Conflict] = field(default_factory=list)
    explanations: dict[str, str] = field(default_factory=dict)
    finish: dict[str, str] = field(default_factory=dict)       # task_id -> last block date
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        return not any(c.kind in ("late", "unscheduled", "capacity", "invalid") for c in self.conflicts)


def remaining_minutes(task: Task, buffer_percent: int = 0) -> int:
    base = max(task.estimate_minutes - task.actual_minutes, 0)
    if task.status == "in_progress" and base == 0:
        base = 15
    if base == 0:
        return 0
    return int(math.ceil(base * (1 + buffer_percent / 100.0)))


def priority_score(task: Task, goal: Goal | None, today: date, dependents: int, s: AppSettings) -> tuple[float, str]:
    """Documented, configurable score. Higher = schedule earlier.

    score = w_urgency * urgency + w_priority * priority_norm + w_dependency * dependency_impact + w_overdue * overdue
      urgency            = 1 / (1 + days_to_deadline)         (0 if no deadline; uses goal deadline as fallback)
      priority_norm      = (task.priority + goal.priority) / 10   (task only if no goal)
      dependency_impact  = min(1, dependents / 5)
      overdue            = 1 if deadline < today else 0
    If settings.protect_high_priority, tasks with priority >= 4 get +100 so they are always placed first.
    """
    dl = parse_date(task.deadline) or (parse_date(goal.deadline) if goal else None)
    urgency, overdue, days = 0.0, 0.0, None
    if dl:
        days = (dl - today).days
        overdue = 1.0 if days < 0 else 0.0
        urgency = 1.0 / (1 + max(days, 0))
    pr = ((task.priority + goal.priority) / 10.0) if goal else task.priority / 5.0
    dep = min(1.0, dependents / 5.0)
    score = s.weight_urgency * urgency + s.weight_priority * pr + s.weight_dependency * dep + s.weight_overdue * overdue
    if s.protect_high_priority and task.priority >= 4:
        score += 100.0          # priority protection: high-priority tasks are never outranked by lower-priority ones
    parts = [f"priority {task.priority}"]
    if days is not None:
        parts.append(f"{'overdue by ' + str(-days) + ' day(s)' if days < 0 else 'due in ' + str(days) + ' day(s)'}")
    if dependents:
        parts.append(f"{dependents} task(s) depend on it")
    return round(score, 4), ", ".join(parts)


def _block_id(task_id: str, day: str, start: str) -> str:
    return uuid.uuid5(_NS, f"{task_id}|{day}|{start}").hex


def build_schedule(
    tasks: list[Task],
    goals: list[Goal],
    settings: AppSettings,
    start_date: date,
    now: datetime | None = None,
    fixed_blocks: list[ScheduleBlock] | None = None,
) -> ScheduleResult:
    """Build a feasible plan from `start_date`. Pure function: same inputs -> identical output.

    `fixed_blocks` are existing blocks (e.g. today's already-completed/in-progress work) that new blocks must not overlap.
    """
    s = settings
    goals_by_id = {g.id: g for g in goals}
    by_id = {t.id: t for t in tasks}
    result = ScheduleResult(blocks=[])
    problems = validate_prerequisites(tasks)
    if problems:
        for p in problems:
            result.conflicts.append(Conflict("invalid", None, p))
        return result

    dependents = transitive_dependents(tasks)

    def goal_ok(t: Task) -> bool:
        g = goals_by_id.get(t.goal_id) if t.goal_id else None
        return g is None or g.status == "active"

    pending = {t.id: t for t in tasks if t.status in ACTIVE_TASK and goal_ok(t) and remaining_minutes(t) > 0}
    remaining = {i: remaining_minutes(t, s.buffer_percent) for i, t in pending.items()}
    # Tasks whose chain includes a blocked / archived / paused-goal prerequisite can never be placed.
    unplaceable: dict[str, str] = {}
    for t in tasks:
        if t.status == "blocked":
            unplaceable[t.id] = f"'{t.title}' is blocked ({t.blocked_reason or 'no reason given'})."
    changed = True
    while changed:
        changed = False
        for i, t in pending.items():
            if i in unplaceable:
                continue
            for p in t.prerequisites:
                pre = by_id[p]
                if p in unplaceable or pre.status == "archived" or (pre.status != "done" and p not in pending):
                    unplaceable[i] = f"'{t.title}' waits on '{pre.title}', which cannot be scheduled right now."
                    changed = True
                    break
    for i, msg in unplaceable.items():
        if i in pending:
            result.conflicts.append(Conflict("blocked", i, msg))
            result.explanations[i] = "Not scheduled: " + msg
    for i in list(pending):
        if i in unplaceable:
            del pending[i]
            remaining.pop(i, None)

    scores: dict[str, float] = {}
    why: dict[str, str] = {}
    for i, t in pending.items():
        g = goals_by_id.get(t.goal_id) if t.goal_id else None
        scores[i], why[i] = priority_score(t, g, start_date, dependents.get(i, 0), s)
    result.scores = scores

    def order_key(i: str):
        t = pending[i]
        return (-scores[i], t.deadline or "9999-12-31", t.created_at, i)

    fixed_by_day: dict[str, list[tuple[int, int]]] = {}
    for b in fixed_blocks or []:
        if b.status != "superseded":
            fixed_by_day.setdefault(b.date, []).append((hhmm_to_minutes(b.start), hhmm_to_minutes(b.end)))

    day_start, day_end = hhmm_to_minutes(s.day_start), hhmm_to_minutes(s.day_end)
    window_cap = day_end - day_start
    done_blocks: dict[str, int] = {}     # task -> end minute index (absolute across days) is tracked via finish_dt
    finish_dt: dict[str, tuple[str, int]] = {}
    completed_tasks = {t.id for t in tasks if t.status == "done"}
    first_seen_wait: dict[str, str] = {}
    cap_reached_day: dict[str, str] = {}

    for offset in range(s.horizon_days):
        if not remaining:
            break
        day = start_date + timedelta(days=offset)
        dstr = day.isoformat()
        if day.weekday() not in s.working_days:
            continue
        cursor = day_start
        if day == start_date and now is not None:
            nm = now.hour * 60 + now.minute
            cursor = max(day_start, int(math.ceil(nm / 5.0) * 5))
        day_cap = min(s.daily_capacity_minutes, window_cap)
        used_total = sum(e - st for st, e in fixed_by_day.get(dstr, []))
        used_goal: dict[str | None, int] = {}
        placed_any = False
        guard = 0
        while remaining and guard < 500:
            guard += 1
            # skip over fixed blocks overlapping the cursor
            moved = True
            while moved:
                moved = False
                for fs, fe in sorted(fixed_by_day.get(dstr, [])):
                    if fs < cursor + s.min_block_minutes and fe > cursor:
                        cursor = fe + (s.break_minutes if s.break_minutes else 0)
                        moved = True
            time_left_window = day_end - cursor
            cap_left = day_cap - used_total
            if time_left_window < min(s.min_block_minutes, 15) or cap_left <= 0:
                break
            ready = []
            for i in sorted(remaining, key=order_key):
                t = pending[i]
                if dstr in t.skip_dates:
                    first_seen_wait.setdefault(i, f"skipped on {dstr} at your request")
                    continue
                es = parse_date(t.earliest_start)
                if es and day < es:
                    first_seen_wait.setdefault(i, f"earliest start is {es.isoformat()}")
                    continue
                if any(p not in completed_tasks and p not in finish_dt for p in t.prerequisites):
                    first_seen_wait.setdefault(i, "waiting on prerequisite tasks")
                    continue
                if any(p in remaining for p in t.prerequisites):
                    continue
                g = goals_by_id.get(t.goal_id) if t.goal_id else None
                if g and g.available_days is not None and day.weekday() not in g.available_days:
                    first_seen_wait.setdefault(i, f"goal not available on {day.strftime('%A')}s")
                    continue
                gcap = g.daily_minutes if g and g.daily_minutes else None
                if gcap is not None and used_goal.get(g.id if g else None, 0) >= gcap:
                    cap_reached_day.setdefault(i, dstr)
                    continue
                # prerequisites must have finished before the cursor on the same day
                same_day_end = max((finish_dt[p][1] for p in t.prerequisites if p in finish_dt and finish_dt[p][0] == dstr), default=0)
                if same_day_end and cursor < same_day_end:
                    continue
                ready.append(i)
            placed = False
            for i in ready:
                t = pending[i]
                g = goals_by_id.get(t.goal_id) if t.goal_id else None
                gcap = g.daily_minutes if g and g.daily_minutes else None
                g_left = (gcap - used_goal.get(g.id if g else None, 0)) if gcap is not None else 10**6
                room = min(time_left_window, cap_left, g_left)
                need = remaining[i]
                if t.splittable:
                    length = min(need, s.max_block_minutes, room)
                    if need - length < s.min_block_minutes and need <= room:
                        length = need          # avoid a tiny trailing fragment
                    if length < min(s.min_block_minutes, need):
                        cap_reached_day.setdefault(i, dstr)
                        continue
                else:
                    if need > room:
                        full_room = min(window_cap, s.daily_capacity_minutes, gcap or 10**6)
                        if need > full_room:
                            result.conflicts.append(Conflict(
                                "capacity", i, f"'{t.title}' needs {need} min but cannot be split and a day only fits {full_room} min."))
                            remaining.pop(i)
                            result.explanations[i] = "Cannot be scheduled: longer than a day's capacity and not splittable."
                            placed = True
                            break
                        cap_reached_day.setdefault(i, dstr)
                        continue
                    length = need
                length = int(length)
                st, en = cursor, cursor + length
                result.blocks.append(ScheduleBlock(
                    id=_block_id(i, dstr, minutes_to_hhmm(st)), task_id=i, date=dstr,
                    start=minutes_to_hhmm(st), end=minutes_to_hhmm(en),
                    reason=f"Scheduled by score {scores[i]} ({why[i]})",
                    created_at="", updated_at=""))
                remaining[i] -= length
                used_total += length
                used_goal[g.id if g else None] = used_goal.get(g.id if g else None, 0) + length
                cursor = en + s.break_minutes
                finish_dt[i] = (dstr, en)
                if remaining[i] <= 0:
                    del remaining[i]
                placed = placed_any = True
                break
            if not placed:
                break

    # explanations & conflicts
    last_date: dict[str, str] = {i: v[0] for i, v in finish_dt.items()}
    result.finish = last_date
    for i, t in pending.items():
        first = next((b for b in result.blocks if b.task_id == i), None)
        if i in remaining:
            result.conflicts.append(Conflict(
                "unscheduled", i, f"'{t.title}' could not be fully scheduled within {s.horizon_days} days "
                                  f"({remaining[i]} min left)."))
            result.explanations[i] = "Not fully scheduled: planning horizon or capacity exhausted."
            continue
        if i in result.explanations:
            continue
        parts = [f"Score {scores[i]} ({why[i]})."]
        if first and first.date != start_date.isoformat():
            reason = first_seen_wait.get(i) or cap_reached_day.get(i) and f"daily capacity was full on {cap_reached_day[i]}" or "higher-scoring tasks filled earlier days"
            parts.append(f"Delayed to {first.date}: {reason}.")
        elif first:
            parts.append(f"Placed at {first.start} on {first.date}.")
        dl = parse_date(t.deadline) or (parse_date(goals_by_id[t.goal_id].deadline) if t.goal_id in goals_by_id else None)
        if dl and i in last_date and date.fromisoformat(last_date[i]) > dl:
            late = (date.fromisoformat(last_date[i]) - dl).days
            result.conflicts.append(Conflict("late", i, f"'{t.title}' would finish {late} day(s) after its deadline ({dl.isoformat()})."))
            parts.append(f"WARNING: finishes {last_date[i]}, after deadline {dl.isoformat()}.")
        result.explanations[i] = " ".join(parts)
    # capacity feasibility by deadline, per goal
    for g in goals:
        if g.status != "active" or not g.deadline:
            continue
        need = sum(remaining_minutes(t, s.buffer_percent) for t in tasks if t.goal_id == g.id and t.status in ACTIVE_TASK)
        have = available_capacity(start_date, parse_date(g.deadline), s, g)
        if need > have:
            result.conflicts.append(Conflict(
                "capacity", None, f"Goal '{g.title}' needs {need} min but only {have} min of capacity exist before {g.deadline}."))
    result.blocks.sort(key=lambda b: (b.date, b.start, b.task_id))
    for b in result.blocks:
        b.created_at = b.updated_at = (now or datetime.now()).replace(microsecond=0).isoformat()
    return result


def available_capacity(start: date, end: date | None, s: AppSettings, goal: Goal | None = None) -> int:
    """Total schedulable minutes between start and end inclusive for a goal (or global)."""
    if end is None or end < start:
        return 0
    days = goal.available_days if goal and goal.available_days is not None else s.working_days
    per_day = min(s.daily_capacity_minutes, hhmm_to_minutes(s.day_end) - hhmm_to_minutes(s.day_start))
    if goal and goal.daily_minutes:
        per_day = min(per_day, goal.daily_minutes)
    total, d = 0, start
    while d <= end:
        if d.weekday() in days and d.weekday() in s.working_days:
            total += per_day
        d += timedelta(days=1)
    return total


# ---------------------------------------------------------------- rescheduling

@dataclass
class SchedulePreview:
    old_finish: dict[str, str]
    new_finish: dict[str, str]
    moved: list[dict[str, Any]]
    new_late: list[dict[str, Any]]
    max_shift_minutes: int
    significant: bool


def split_history(existing: list[ScheduleBlock], today: date) -> tuple[list[ScheduleBlock], list[ScheduleBlock]]:
    """(preserved, replaceable). Replaceable = planned blocks from today onward. Everything else is history."""
    keep, replace = [], []
    for b in existing:
        if b.status == "planned" and date.fromisoformat(b.date) >= today:
            replace.append(b)
        else:
            keep.append(b)
    return keep, replace


def preview_changes(old_planned: list[ScheduleBlock], result: ScheduleResult, tasks: list[Task],
                    settings: AppSettings) -> SchedulePreview:
    by_id = {t.id: t for t in tasks}

    def finish(blocks: list[ScheduleBlock]) -> dict[str, str]:
        out: dict[str, str] = {}
        for b in blocks:
            if b.task_id not in out or (b.date, b.end) > (out[b.task_id], ""):
                out[b.task_id] = b.date
        return out

    def first_start(blocks: list[ScheduleBlock]) -> dict[str, tuple[str, str]]:
        out: dict[str, tuple[str, str]] = {}
        for b in sorted(blocks, key=lambda x: (x.date, x.start)):
            out.setdefault(b.task_id, (b.date, b.start))
        return out

    of, nf = finish(old_planned), finish(result.blocks)
    os_, ns = first_start(old_planned), first_start(result.blocks)
    moved, shift_max = [], 0
    for tid in sorted(set(os_) | set(ns)):
        if os_.get(tid) != ns.get(tid):
            t = by_id.get(tid)
            moved.append({"task_id": tid, "title": t.title if t else tid, "old": os_.get(tid), "new": ns.get(tid),
                          "displaced": tid in os_ and tid not in ns})
            if tid in os_ and tid in ns:
                d0 = datetime.fromisoformat(f"{os_[tid][0]}T{os_[tid][1]}")
                d1 = datetime.fromisoformat(f"{ns[tid][0]}T{ns[tid][1]}")
                shift_max = max(shift_max, abs(int((d1 - d0).total_seconds() // 60)))
    new_late = [c.__dict__ for c in result.conflicts if c.kind == "late"]
    significant = shift_max >= settings.significant_change_minutes or any(m["displaced"] for m in moved) or bool(new_late)
    return SchedulePreview(of, nf, moved, new_late, shift_max, significant)
