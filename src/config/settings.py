"""Centralised application settings, persisted in data/settings.json."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
SCHEMA_VERSION = 1


def hhmm_to_minutes(value: str) -> int:
    """Return minutes since midnight for 'HH:MM'."""
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid time '{value}', expected HH:MM.") from exc
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m != 0):
        raise ValueError(f"Invalid time '{value}', expected HH:MM.")
    return h * 60 + m


def minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class AppSettings:
    # --- LLM ---
    llm_provider: str = "ollama"            # "ollama" | "openai_compatible" | "none"
    ollama_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = "llama3.2"
    hosted_base_url: str = "https://api.groq.com/openai/v1"
    hosted_model: str = "llama-3.3-70b-versatile"   # unverified default; change in Settings
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    hosted_disclosure_ack: bool = False     # user acknowledged data is sent to a third party
    # --- Scheduling ---
    working_days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon=0
    day_start: str = "09:00"
    day_end: str = "18:00"
    daily_capacity_minutes: int = 240
    break_minutes: int = 10
    buffer_percent: int = 10                # extra time added to estimates when scheduling
    max_block_minutes: int = 120
    min_block_minutes: int = 25
    horizon_days: int = 365
    protect_high_priority: bool = True      # priority>=4 tasks are never displaced by lower ones
    allow_move_low_priority: bool = True    # reserved; not used by the scheduler yet
    weight_urgency: float = 3.0
    weight_priority: float = 2.0
    weight_dependency: float = 1.0
    weight_overdue: float = 4.0
    significant_change_minutes: int = 60    # shifts above this require a preview/confirmation
    # --- Notifications ---
    notify_overdue: bool = True
    notify_deadlines: bool = True
    notify_at_risk: bool = True
    notify_blocked: bool = True
    notify_missed: bool = True
    notify_habits: bool = True
    deadline_warning_days: int = 3
    # --- ML ---
    ml_enabled: bool = True
    ml_min_samples: int = 30
    # --- Misc ---
    backup_keep: int = 10
    chat_history_limit: int = 100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known})
        s.validate()
        return s

    def validate(self) -> None:
        if not self.working_days or any((not isinstance(d, int)) or d < 0 or d > 6 for d in self.working_days):
            raise ValueError("working_days must be a non-empty list of integers 0 (Mon) to 6 (Sun).")
        if hhmm_to_minutes(self.day_end) <= hhmm_to_minutes(self.day_start):
            raise ValueError("day_end must be later than day_start.")
        if not 15 <= self.daily_capacity_minutes <= 1440:
            raise ValueError("daily_capacity_minutes must be between 15 and 1440.")
        if not 0 <= self.break_minutes <= 120:
            raise ValueError("break_minutes must be between 0 and 120.")
        if not 0 <= self.buffer_percent <= 100:
            raise ValueError("buffer_percent must be between 0 and 100.")
        if self.min_block_minutes < 5 or self.max_block_minutes < self.min_block_minutes:
            raise ValueError("Block sizes are inconsistent (min >= 5 and max >= min).")
        if self.llm_provider not in {"ollama", "openai_compatible", "none"}:
            raise ValueError("llm_provider must be 'ollama', 'openai_compatible' or 'none'.")
