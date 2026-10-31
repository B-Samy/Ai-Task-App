"""Contextual assistant. Deterministic answers for common questions; LLM only for free-form ones.

Data-changing requests produce a *proposal* that must be confirmed. The model can never run code, shell, SQL or touch files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from src.analytics import metrics as M
from src.llm.base import LLMError, LLMProvider
from src.services.app_service import AppService, ServiceError
from src.planning.dependencies import blocking_reasons


@dataclass
class Reply:
    text: str
    source: str                     # "local" | "llm:<provider>"
    proposal: dict[str, Any] | None = None


ALLOWED_ACTIONS = {"reschedule"}    # whitelist: the only mutation the assistant may propose


def build_context(svc: AppService, today: date, max_items: int = 8) -> dict[str, Any]:
    """Small, relevant slice of local data (never the whole data directory)."""
    tasks, goals, s = svc.tasks(), svc.goals(), svc.settings
    active = [t for t in tasks if t.status in ("todo", "in_progress", "paused")]
    blocks = [b for b in svc.blocks() if b.status == "planned" and b.date >= today.isoformat()]
    by_id = {t.id: t for t in tasks}
    upcoming = [{"task": by_id[b.task_id].title, "date": b.date, "start": b.start, "minutes": b.minutes}
                for b in blocks[:max_items] if b.task_id in by_id]
    return {
        "today": today.isoformat(),
        "counts": M.task_counts(tasks),
        "overdue": [t.title for t in M.overdue_tasks(tasks, today, goals)[:max_items]],
        "at_risk_goals": [r["title"] for r in M.goal_risks(goals, tasks, s, today) if r["risk"] in ("high", "medium")][:max_items],
        "upcoming_blocks": upcoming,
        "active_tasks": [{"title": t.title, "status": t.status, "deadline": t.deadline} for t in active[:max_items]],
    }


def next_task(svc: AppService, today: date) -> tuple[Any, str] | None:
    tasks = {t.id: t for t in svc.tasks()}
    for b in sorted(svc.blocks(), key=lambda b: (b.date, b.start)):
        if b.status == "planned" and b.date >= today.isoformat() and b.task_id in tasks:
            t = tasks[b.task_id]
            if t.status in ("todo", "in_progress", "paused") and not blocking_reasons(t, tasks):
                return t, f"scheduled {b.date} {b.start}-{b.end}"
    cands = [t for t in tasks.values() if t.status in ("todo", "in_progress") and not blocking_reasons(t, tasks)]
    if cands:
        cands.sort(key=lambda t: (t.deadline or "9999", -t.priority, t.created_at))
        return cands[0], "no schedule yet; chosen by deadline then priority"
    return None


def answer(svc: AppService, question: str, today: date | None = None, provider: LLMProvider | None = None) -> Reply:
    today = today or date.today()
    q = question.lower().strip()
    s, tasks, goals = svc.settings, svc.tasks(), svc.goals()

    if re.search(r"\b(reschedule|re-schedule|rebuild|replan)\b", q):
        res, prev = svc.propose_schedule(today)
        text = (f"I can rebuild your plan from today. Proposed: {len(res.blocks)} block(s); {len(prev.moved)} task(s) would move; "
                f"{len(res.conflicts)} conflict(s). Nothing has been changed yet. Confirm below to apply.")
        return Reply(text, "local", {"action": "reschedule"})
    if re.search(r"(work on next|what.*next|what should i (do|work))", q):
        n = next_task(svc, today)
        return Reply(f"Next: '{n[0].title}' ({n[1]})." if n else "Nothing is ready. Add goals/tasks, or check blocked items.", "local")
    if re.search(r"(behind|late|slip)", q):
        od = M.overdue_tasks(tasks, today, goals)
        missed = [t for t in tasks if t.miss_count > 0 and t.status != "done"]
        parts = []
        if od:
            parts.append(f"{len(od)} overdue task(s): " + ", ".join(f"'{t.title}'" for t in od[:5]))
        if missed:
            parts.append(f"{len(missed)} task(s) were missed at least once")
        risks = [r for r in M.goal_risks(goals, tasks, s, today) if r["risk"] == "high"]
        if risks:
            parts.append("goals needing more time than available: " + ", ".join(r["title"] for r in risks))
        return Reply(("You are behind because of: " + "; ".join(parts) + ".") if parts else "You are not behind on anything I can measure.", "local")
    if re.search(r"(deadline|at risk|at-risk)", q):
        end = today + timedelta(days=7)
        due = [t for t in tasks if t.status not in ("done", "archived") and t.deadline and today <= date.fromisoformat(t.deadline) <= end]
        risks = [r for r in M.goal_risks(goals, tasks, s, today) if r["risk"] in ("high", "medium")]
        txt = []
        if due:
            txt.append("Due within 7 days: " + ", ".join(f"'{t.title}' ({t.deadline})" for t in due))
        if risks:
            txt.append("Goals at risk (heuristic): " + ", ".join(f"{r['title']} [{r['risk']}]" for r in risks))
        return Reply(" ".join(txt) or "No deadlines at risk this week.", "local")
    if re.search(r"(progress|this month|how much)", q):
        mp = M.month_progress(tasks, today)
        return Reply(f"In {mp['month']} you completed {mp['tasks_completed']} task(s) and logged {mp['minutes']} minutes.", "local")
    if re.search(r"habit", q):
        rows = M.habit_summary(svc.habits(), svc.habit_logs(), today)
        weak = sorted([r for r in rows if r["month_rate"] is not None and r["month_rate"] < 0.5], key=lambda r: r["month_rate"])
        if not rows:
            return Reply("You have no habits yet.", "local")
        return Reply("Habits below 50% this month: " + ", ".join(f"'{r['title']}' ({r['month_rate']:.0%})" for r in weak) if weak
                     else "No habit is below 50% over the last 30 days.", "local")

    if provider is None:
        return Reply("I can answer: what to do next, why you're behind, deadlines at risk, monthly progress, habits, or reschedule. "
                     "Connect an LLM in Settings for free-form questions.", "local")
    ctx = build_context(svc, today)
    sys = ("You are a concise task-planning assistant. Use ONLY the JSON context. If you want to change data, say so in words; "
           "you cannot execute anything. Do not invent tasks that are not in the context.\nCONTEXT: " + str(ctx))
    try:
        txt = provider.chat([{"role": "system", "content": sys}, {"role": "user", "content": question[:1500]}],
                            timeout=s.llm_timeout_seconds, max_retries=s.llm_max_retries)
    except LLMError as exc:
        return Reply(f"The language model is unavailable ({exc}). Local answers still work.", "local")
    return Reply(txt.strip(), f"llm:{provider.name}")


def confirm(svc: AppService, proposal: dict[str, Any], today: date | None = None) -> str:
    """Apply a confirmed proposal. Only whitelisted actions; returns a message that reflects what actually happened."""
    if proposal.get("action") not in ALLOWED_ACTIONS:
        raise ServiceError("That action is not allowed.")
    res, _ = svc.propose_schedule(today)
    stats = svc.apply_schedule(res, "Assistant-confirmed reschedule", today)
    return f"Applied and saved: {stats['added']} block(s) added, {stats['superseded']} replaced, {stats['unchanged']} unchanged."
