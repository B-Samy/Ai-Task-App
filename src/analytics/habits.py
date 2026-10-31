"""Habit recurrence, streaks and adherence."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from src.models.entities import Habit, HabitLog, parse_date


def is_due(habit: Habit, day: date) -> bool:
    start = parse_date(habit.start_date)
    if not habit.active or (start and day < start):
        return False
    if habit.recurrence == "daily":
        return True
    if habit.recurrence == "weekdays":
        return day.weekday() < 5
    return day.weekday() in habit.days


def _done_days(habit: Habit, logs: Iterable[HabitLog]) -> set[date]:
    return {parse_date(l.date) for l in logs if l.habit_id == habit.id and l.done}  # type: ignore[misc]


def current_streak(habit: Habit, logs: Iterable[HabitLog], today: date) -> int:
    """Consecutive due days completed, ending today (an uncompleted today does not break the streak yet)."""
    done = _done_days(habit, logs)
    start = parse_date(habit.start_date) or today
    streak, d = 0, today
    if is_due(habit, today) and today not in done:
        d = today - timedelta(days=1)
    while d >= start:
        if is_due(habit, d):
            if d in done:
                streak += 1
            else:
                break
        d -= timedelta(days=1)
    return streak


def longest_streak(habit: Habit, logs: Iterable[HabitLog], today: date) -> int:
    done = _done_days(habit, logs)
    start = parse_date(habit.start_date) or today
    best = run = 0
    d = start
    while d <= today:
        if is_due(habit, d):
            if d in done:
                run += 1
                best = max(best, run)
            elif d != today:
                run = 0
        d += timedelta(days=1)
    return best


def completion_rate(habit: Habit, logs: Iterable[HabitLog], start: date, end: date) -> float | None:
    """Fraction of due days completed in [start, end]; None if no days were due (empty state)."""
    done = _done_days(habit, logs)
    due = completed = 0
    d = max(start, parse_date(habit.start_date) or start)
    while d <= end:
        if is_due(habit, d):
            due += 1
            completed += d in done
        d += timedelta(days=1)
    return None if due == 0 else completed / due


def weekly_monthly(habit: Habit, logs: list[HabitLog], today: date) -> dict[str, float | None]:
    return {
        "week": completion_rate(habit, logs, today - timedelta(days=6), today),
        "month": completion_rate(habit, logs, today - timedelta(days=29), today),
    }


def missed_days(habit: Habit, logs: Iterable[HabitLog], start: date, end: date) -> list[date]:
    done = _done_days(habit, logs)
    out, d = [], max(start, parse_date(habit.start_date) or start)
    while d <= end:
        if is_due(habit, d) and d not in done:
            out.append(d)
        d += timedelta(days=1)
    return out
