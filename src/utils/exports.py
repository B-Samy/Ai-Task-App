"""CSV/JSON/Markdown exports. UTF-8, safe filenames, no secrets."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, default: str = "export", max_len: int = 80) -> str:
    """Strip path separators / reserved characters. Never returns '', '.', '..' or Windows reserved names."""
    base = _UNSAFE.sub("_", name.replace("\\", "/").split("/")[-1]).strip("._") or default
    if base.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        base = "_" + base
    return base[:max_len]


def _flat(v: Any) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


def _formula_safe(v: str) -> str:
    """Neutralise spreadsheet formula injection (cells starting with = + - @)."""
    return "'" + v if v and v[0] in "=+-@" and not re.fullmatch(r"-?\d+(\.\d+)?", v) else v


def to_csv(rows: list[dict[str, Any]], columns: list[str] | None = None) -> bytes:
    cols = columns or (list(rows[0].keys()) if rows else [])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_formula_safe(_flat(r.get(c))) for c in cols])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")      # BOM so Excel on Windows reads UTF-8


def to_json(obj: Any) -> bytes:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def report_markdown(report: dict[str, Any]) -> bytes:
    o, h = report["observed"], report["heuristic_estimates"]
    tc = o["task_counts"]
    lines = [f"# Productivity report ({report['generated']})", "",
             "## Observed metrics", f"- Tasks: {tc['completed']} completed / {tc['pending']} pending", f"- Overdue: {o['overdue']}",
             f"- Planned vs actual: {o['planned_vs_actual']['planned_minutes']} vs {o['planned_vs_actual']['actual_minutes']} min",
             f"- Schedule adherence: {o['adherence']['rate'] if o['adherence']['rate'] is not None else 'n/a'}", "",
             "## Heuristic estimates (not ML)"]
    lines += [f"- {r['title']}: risk **{r['risk']}** (needs {r['need_minutes']} min, capacity {r['capacity_minutes']})" for r in h["goal_risks"]] or ["- none"]
    return "\n".join(lines).encode("utf-8")


def tasks_rows(tasks, goals) -> list[dict[str, Any]]:
    gt = {g.id: g.title for g in goals}
    return [{**t.to_dict(), "goal": gt.get(t.goal_id, "")} for t in tasks]


def today_stamp() -> str:
    return date.today().isoformat()
