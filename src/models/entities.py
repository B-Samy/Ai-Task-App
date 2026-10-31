"""Typed, validated records persisted to JSON."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, date
from typing import Any, ClassVar


class ValidationError(ValueError):
    """Raised when a record fails validation."""


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def parse_date(value: str | date | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValidationError(f"Invalid date '{value}', expected YYYY-MM-DD.") from exc


GOAL_STATUSES = {"active", "paused", "completed", "archived"}
TASK_STATUSES = {"todo", "in_progress", "paused", "blocked", "done", "archived"}
BLOCK_STATUSES = {"planned", "completed", "missed", "skipped", "superseded"}
RECURRENCES = {"daily", "weekdays", "weekly"}


class _Record:
    COLLECTION: ClassVar[str] = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        obj = cls(**{k: v for k, v in data.items() if k in known})
        obj.validate()
        return obj

    def validate(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


def _req_text(value: Any, name: str, max_len: int = 200) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string.")
    if len(value) > max_len:
        raise ValidationError(f"{name} must be at most {max_len} characters.")


@dataclass
class Goal(_Record):
    COLLECTION: ClassVar[str] = "goals"
    title: str
    id: str = field(default_factory=new_id)
    description: str = ""
    success_criteria: str = ""
    category: str = "General"
    priority: int = 3
    deadline: str | None = None
    status: str = "active"
    available_days: list[int] | None = None      # None -> use global working days
    daily_minutes: int | None = None             # None -> use global capacity
    preferred_hours: str = ""
    skill_level: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    is_demo: bool = False

    def validate(self) -> None:
        _req_text(self.title, "Goal title")
        if self.status not in GOAL_STATUSES:
            raise ValidationError(f"Invalid goal status '{self.status}'.")
        if not isinstance(self.priority, int) or not 1 <= self.priority <= 5:
            raise ValidationError("Goal priority must be an integer from 1 to 5.")
        parse_date(self.deadline)
        if self.available_days is not None and (
            not self.available_days or any(not isinstance(d, int) or not 0 <= d <= 6 for d in self.available_days)
        ):
            raise ValidationError("available_days must be integers 0-6.")
        if self.daily_minutes is not None and not 5 <= self.daily_minutes <= 1440:
            raise ValidationError("daily_minutes must be between 5 and 1440.")


@dataclass
class Task(_Record):
    COLLECTION: ClassVar[str] = "tasks"
    title: str
    id: str = field(default_factory=new_id)
    goal_id: str | None = None
    description: str = ""
    completion_criteria: str = ""
    milestone: str = ""
    task_type: str = "task"                     # task | review | practice | reading ...
    category: str = "General"
    priority: int = 3
    estimate_minutes: int = 30
    actual_minutes: int = 0
    prerequisites: list[str] = field(default_factory=list)
    deadline: str | None = None
    earliest_start: str | None = None
    status: str = "todo"
    splittable: bool = True
    blocked_reason: str = ""
    skip_dates: list[str] = field(default_factory=list)
    miss_count: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    completed_at: str | None = None
    is_demo: bool = False

    def validate(self) -> None:
        _req_text(self.title, "Task title")
        if self.status not in TASK_STATUSES:
            raise ValidationError(f"Invalid task status '{self.status}'.")
        if not isinstance(self.priority, int) or not 1 <= self.priority <= 5:
            raise ValidationError("Task priority must be an integer from 1 to 5.")
        if not isinstance(self.estimate_minutes, int) or not 5 <= self.estimate_minutes <= 6000:
            raise ValidationError("estimate_minutes must be an integer from 5 to 6000.")
        if not isinstance(self.actual_minutes, int) or self.actual_minutes < 0:
            raise ValidationError("actual_minutes cannot be negative.")
        if not isinstance(self.prerequisites, list) or any(not isinstance(p, str) for p in self.prerequisites):
            raise ValidationError("prerequisites must be a list of task IDs.")
        if self.id in self.prerequisites:
            raise ValidationError("A task cannot depend on itself.")
        if len(set(self.prerequisites)) != len(self.prerequisites):
            raise ValidationError("Duplicate prerequisites.")
        d, e = parse_date(self.deadline), parse_date(self.earliest_start)
        if d and e and e > d:
            raise ValidationError("earliest_start is after the deadline.")


@dataclass
class ScheduleBlock(_Record):
    COLLECTION: ClassVar[str] = "schedule"
    task_id: str
    date: str
    start: str
    end: str
    id: str = field(default_factory=new_id)
    status: str = "planned"
    reason: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @property
    def minutes(self) -> int:
        from src.config.settings import hhmm_to_minutes
        return hhmm_to_minutes(self.end) - hhmm_to_minutes(self.start)

    def validate(self) -> None:
        from src.config.settings import hhmm_to_minutes
        if self.status not in BLOCK_STATUSES:
            raise ValidationError(f"Invalid block status '{self.status}'.")
        if parse_date(self.date) is None:
            raise ValidationError("Block date is required.")
        try:
            if hhmm_to_minutes(self.end) <= hhmm_to_minutes(self.start):
                raise ValidationError("Block end must be after start.")
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


@dataclass
class Habit(_Record):
    COLLECTION: ClassVar[str] = "habits"
    title: str
    id: str = field(default_factory=new_id)
    goal_id: str | None = None
    recurrence: str = "daily"                       # daily | weekdays | weekly
    days: list[int] = field(default_factory=list)   # used for "weekly" (0=Mon)
    start_date: str = field(default_factory=lambda: date.today().isoformat())
    active: bool = True
    created_at: str = field(default_factory=now_iso)
    is_demo: bool = False

    def validate(self) -> None:
        _req_text(self.title, "Habit title")
        if self.recurrence not in RECURRENCES:
            raise ValidationError(f"Invalid recurrence '{self.recurrence}'.")
        if self.recurrence == "weekly" and (not self.days or any(not isinstance(d, int) or not 0 <= d <= 6 for d in self.days)):
            raise ValidationError("Weekly habits need at least one weekday (0-6).")
        if parse_date(self.start_date) is None:
            raise ValidationError("Habit start_date is required.")


@dataclass
class HabitLog(_Record):
    COLLECTION: ClassVar[str] = "habit_logs"
    habit_id: str
    date: str
    done: bool = True
    id: str = field(default_factory=new_id)
    note: str = ""
    created_at: str = field(default_factory=now_iso)
    is_demo: bool = False

    def validate(self) -> None:
        if parse_date(self.date) is None:
            raise ValidationError("Habit log date is required.")
        if not self.habit_id:
            raise ValidationError("Habit log needs a habit_id.")


@dataclass
class Activity(_Record):
    COLLECTION: ClassVar[str] = "activity_log"
    action: str
    message: str
    id: str = field(default_factory=new_id)
    entity_type: str = ""
    entity_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now_iso)
    is_demo: bool = False

    def validate(self) -> None:
        _req_text(self.action, "Activity action", 80)


MODELS: dict[str, type[_Record]] = {
    c.COLLECTION: c for c in (Goal, Task, ScheduleBlock, Habit, HabitLog, Activity)
}
