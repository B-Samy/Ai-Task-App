"""Validation of LLM-produced plans, plus a clearly-labelled manual/fallback planner."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.llm.base import LLMBadResponse, LLMProvider

VAGUE_TITLES = {"do task", "work", "stuff", "misc", "todo", "task", "things", "tbd", "etc", "work on it", "continue"}
MAX_TASKS = 60


class PlanValidationError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


@dataclass
class PlanTask:
    key: str
    title: str
    description: str = ""
    completion_criteria: str = ""
    milestone: str = ""
    category: str = "General"
    priority: int = 3
    estimate_minutes: int = 30
    prerequisites: list[str] = field(default_factory=list)
    deadline: str | None = None
    splittable: bool = True


@dataclass
class Plan:
    goal_summary: str
    tasks: list[PlanTask]
    milestones: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = "manual"      # "llm:<provider>:<model>" | "fallback" | "manual"


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of model output (handles ``` fences and chatter around it)."""
    if not isinstance(text, str) or not text.strip():
        raise LLMBadResponse("The model returned an empty response.")
    t = re.sub(r"```(?:json)?", "", text).strip()
    start = t.find("{")
    if start < 0:
        raise LLMBadResponse("The model response contained no JSON object.")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise LLMBadResponse(f"The model returned malformed JSON ({exc.msg}).") from exc
                if not isinstance(obj, dict):
                    raise LLMBadResponse("Expected a JSON object.")
                return obj
    raise LLMBadResponse("The model returned incomplete JSON (unbalanced braces).")


def validate_plan(raw: dict[str, Any], source: str, today: date | None = None) -> Plan:
    """Validate and normalise. Unknown fields are ignored; structural problems raise PlanValidationError."""
    issues: list[str] = []
    warnings: list[str] = []
    today = today or date.today()
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise PlanValidationError(["The plan has no tasks."])
    if len(tasks_raw) > MAX_TASKS:
        raise PlanValidationError([f"The plan has {len(tasks_raw)} tasks (max {MAX_TASKS})."])
    tasks: list[PlanTask] = []
    seen_keys: set[str] = set()
    seen_titles: dict[str, str] = {}
    for idx, tr in enumerate(tasks_raw, 1):
        if not isinstance(tr, dict):
            issues.append(f"Task #{idx} is not an object.")
            continue
        key = str(tr.get("key") or tr.get("id") or f"t{idx}").strip()
        title = str(tr.get("title") or "").strip()
        if key in seen_keys:
            issues.append(f"Duplicate task key '{key}'.")
        seen_keys.add(key)
        if len(title) < 4 or norm_title(title) in VAGUE_TITLES:
            issues.append(f"Task '{title or key}' is too vague; give it a specific, actionable title.")
        nt = norm_title(title)
        if nt in seen_titles:
            issues.append(f"Duplicate task title '{title}'.")
        seen_titles[nt] = key
        try:
            est = int(tr.get("estimate_minutes", tr.get("duration_minutes", 0)))
        except (TypeError, ValueError):
            est = 0
            issues.append(f"Task '{title}' has a non-numeric estimate.")
        if est and not 5 <= est <= 6000:
            warnings.append(f"Task '{title}': estimate {est} min was clamped to 5-6000.")
            est = max(5, min(est, 6000))
        if est == 0:
            issues.append(f"Task '{title}' has no estimate_minutes.")
            est = 30
        try:
            pr = int(tr.get("priority", 3))
        except (TypeError, ValueError):
            pr = 3
        pr = max(1, min(5, pr))
        dl = tr.get("deadline") or None
        if dl:
            try:
                d = date.fromisoformat(str(dl)[:10])
                if d < today:
                    warnings.append(f"Task '{title}' has a deadline in the past ({dl}); it was removed.")
                    dl = None
                else:
                    dl = d.isoformat()
            except ValueError:
                warnings.append(f"Task '{title}' had an invalid deadline and it was removed.")
                dl = None
        prereq = tr.get("prerequisites") or []
        if not isinstance(prereq, list):
            issues.append(f"Task '{title}' prerequisites must be a list.")
            prereq = []
        tasks.append(PlanTask(
            key=key, title=title, description=str(tr.get("description") or ""),
            completion_criteria=str(tr.get("completion_criteria") or ""),
            milestone=str(tr.get("milestone") or ""), category=str(tr.get("category") or "General")[:60],
            priority=pr, estimate_minutes=est, prerequisites=[str(p) for p in prereq], deadline=dl,
            splittable=bool(tr.get("splittable", True))))
        if not tasks[-1].completion_criteria:
            warnings.append(f"Task '{title}' has no measurable completion criteria; consider adding some.")
    keys = {t.key for t in tasks}
    graph: dict[str, list[str]] = {}
    for t in tasks:
        for p in t.prerequisites:
            if p == t.key:
                issues.append(f"Task '{t.title}' depends on itself.")
            elif p not in keys:
                issues.append(f"Task '{t.title}' references unknown prerequisite '{p}'.")
        graph[t.key] = [p for p in t.prerequisites if p in keys and p != t.key]
    cyc = _cycle(graph)
    if cyc:
        issues.append("Circular dependency: " + " -> ".join(cyc))
    if issues:
        raise PlanValidationError(issues)
    return Plan(
        goal_summary=str(raw.get("goal_summary") or raw.get("summary") or "")[:500],
        tasks=tasks, milestones=[str(m) for m in raw.get("milestones", []) if isinstance(m, (str, int))][:30],
        assumptions=[str(a) for a in raw.get("assumptions", [])][:20],
        warnings=warnings + [str(w) for w in raw.get("warnings", [])][:20], source=source)


def _cycle(graph: dict[str, list[str]]) -> list[str] | None:
    color: dict[str, int] = {}

    def dfs(n: str, path: list[str]) -> list[str] | None:
        color[n] = 1
        path.append(n)
        for m in graph.get(n, []):
            if color.get(m) == 1:
                return path[path.index(m):] + [m]
            if color.get(m) is None:
                r = dfs(m, path)
                if r:
                    return r
        color[n] = 2
        path.pop()
        return None

    for n in sorted(graph):
        if color.get(n) is None:
            r = dfs(n, [])
            if r:
                return r
    return None


SYSTEM_PROMPT = """You are a planning assistant. Break the user's goal into a realistic plan.
Respond with ONLY one JSON object (no markdown) with this shape:
{"goal_summary": str, "milestones": [str], "assumptions": [str], "warnings": [str],
 "tasks": [{"key": "t1", "title": str (specific, actionable), "description": str,
            "completion_criteria": str (measurable), "milestone": str, "category": str,
            "priority": 1-5, "estimate_minutes": int, "prerequisites": ["t-keys of earlier tasks"],
            "deadline": "YYYY-MM-DD or null", "splittable": bool}]}
Rules: every prerequisite must be a key that exists; no circular dependencies; no duplicate or vague tasks;
estimates in minutes (5-600 each); today is {today}. Do not include commands or code to execute."""


def generate_plan_with_llm(provider: LLMProvider, goal_text: str, constraints: str, *, timeout: int = 90,
                           max_retries: int = 1, today: date | None = None) -> Plan:
    today = today or date.today()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{today}", today.isoformat())},
        {"role": "user", "content": f"Goal: {goal_text.strip()}\nConstraints/availability: {constraints.strip() or 'none stated'}"},
    ]
    last: Exception | None = None
    for attempt in range(2):                       # one repair attempt for malformed/invalid output
        raw_text = provider.chat(messages, json_mode=True, timeout=timeout, max_retries=max_retries)
        try:
            return validate_plan(extract_json(raw_text), f"llm:{provider.name}:{provider.model}", today)
        except (LLMBadResponse, PlanValidationError) as exc:
            last = exc
            messages += [{"role": "assistant", "content": raw_text[:4000]},
                         {"role": "user", "content": f"That plan was invalid: {exc}. Return a corrected JSON object only."}]
    assert last is not None
    raise last


def manual_plan(goal_title: str, task_lines: list[str], deadline: str | None = None, label: str = "manual") -> Plan:
    """Build a plan from user-typed lines like 'Read chapter 1 | 60 | after:1'. Always labelled non-LLM."""
    tasks: list[PlanTask] = []
    for n, line in enumerate(l.strip() for l in task_lines if l.strip()):
        parts = [p.strip() for p in line.split("|")]
        title = parts[0]
        est = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        pre: list[str] = []
        for p in parts[2:]:
            if p.lower().startswith("after:"):
                pre = [f"t{int(x)}" for x in re.findall(r"\d+", p)]
        tasks.append(PlanTask(key=f"t{n + 1}", title=title, estimate_minutes=est, prerequisites=pre, deadline=deadline))
    raw = {"goal_summary": goal_title, "tasks": [t.__dict__ for t in tasks]}
    plan = validate_plan(raw, label)
    plan.warnings.insert(0, "This plan was entered manually; it was not generated by an LLM.")
    return plan


def template_plan(goal_title: str, total_hours: float, deadline: str | None, steps: int = 5) -> Plan:
    """Generic fallback when no LLM is available. Clearly generic: the user must edit it."""
    steps = max(2, min(steps, 12))
    per = max(15, int(total_hours * 60 / steps))
    names = ["Define scope and success criteria", "Gather resources and set up workspace", "Learn / work through core material",
             "Practice or build a first version", "Review progress and fix gaps", "Finalize and verify success criteria",
             "Reflect and document lessons"]
    tasks = []
    for i in range(steps):
        nm = names[min(i, len(names) - 1)] if i < len(names) else f"Additional work block {i + 1}"
        if i == steps - 1 and steps > 2:
            nm = names[5]
        tasks.append({"key": f"t{i + 1}", "title": f"{nm} - {goal_title}"[:200], "estimate_minutes": per,
                      "prerequisites": [f"t{i}"] if i else [], "deadline": deadline if i == steps - 1 else None,
                      "completion_criteria": "Define what 'done' means for this step."})
    plan = validate_plan({"goal_summary": goal_title, "tasks": tasks}, "fallback")
    plan.warnings.insert(0, "FALLBACK TEMPLATE: no LLM was used. These steps are generic placeholders; edit them before saving.")
    return plan
