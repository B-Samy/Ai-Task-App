import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

from src.analytics import metrics as M
from src.ui.common import DAY_NAMES, RISK_ICON, empty, page, safe

svc = page("Goals", "🎯")
today = date.today()
goals, tasks, s = svc.goals(), svc.tasks(), svc.settings

with st.expander("➕ New goal", expanded=not goals):
    with st.form("new_goal", clear_on_submit=True):
        title = st.text_input("Title")
        desc = st.text_area("Description")
        crit = st.text_area("Success criteria", help="How will you know it is done?")
        c1, c2, c3 = st.columns(3)
        cat = c1.text_input("Category", "General")
        pr = c2.slider("Priority", 1, 5, 3)
        dl = c3.date_input("Deadline", value=None, min_value=today)
        c4, c5 = st.columns(2)
        days = c4.multiselect("Available days (blank = global working days)", DAY_NAMES)
        dm = c5.number_input("Daily minutes for this goal (0 = global limit)", 0, 1440, 0, 15)
        skill = st.text_input("Existing skill level")
        pref = st.text_input("Preferred hours (e.g. 18:00-20:00)")
        if st.form_submit_button("Create goal", type="primary"):
            if safe(svc.create_goal, title=title, description=desc, success_criteria=crit, category=cat or "General", priority=pr,
                    deadline=dl.isoformat() if dl else None, available_days=[DAY_NAMES.index(d) for d in days] or None,
                    daily_minutes=int(dm) or None, skill_level=skill, preferred_hours=pref, success="Goal created"):
                st.rerun()

if not goals:
    empty("No goals yet. Create one above, or use the AI Planner to generate a plan from a sentence.")
risks = {r["goal_id"]: r for r in M.goal_risks(goals, tasks, s, today)}
flt = st.radio("Show", ["active", "paused", "completed", "archived", "all"], horizontal=True)
for g in goals:
    if flt != "all" and g.status != flt:
        continue
    p, r = svc.goal_progress(g.id), risks.get(g.id, {})
    with st.container(border=True):
        a, b = st.columns([4, 2])
        a.subheader(f"{RISK_ICON.get(r.get('risk', 'none'), '')} {g.title}")
        a.caption(f"{g.category} · priority {g.priority} · deadline {g.deadline or '—'} · {g.status}")
        a.progress(min(p["pct"] / 100, 1.0), text=f"{p['done']}/{p['tasks']} tasks · {p['pct']}%")
        if r.get("risk") in ("high", "medium"):
            a.warning(f"Deadline risk (heuristic): needs {r['need_minutes']} min, capacity {r['capacity_minutes']} min.")
        with b:
            for label, status in (("Pause", "paused"), ("Resume", "active"), ("Complete", "completed"), ("Archive", "archived")):
                if g.status != status and b.button(label, key=f"{label}_{g.id}"):
                    if safe(svc.set_goal_status, g.id, status, success=f"Goal {status}"):
                        st.rerun()
        with st.expander("Edit"):
            with st.form(f"edit_{g.id}"):
                t = st.text_input("Title", g.title)
                d = st.text_area("Description", g.description)
                sc = st.text_area("Success criteria", g.success_criteria)
                c1, c2 = st.columns(2)
                pr = c1.slider("Priority", 1, 5, g.priority)
                dl = c2.date_input("Deadline", value=date.fromisoformat(g.deadline) if g.deadline else None)
                if st.form_submit_button("Save"):
                    if safe(svc.update_goal, g.id, title=t, description=d, success_criteria=sc, priority=pr,
                            deadline=dl.isoformat() if dl else None, success="Saved"):
                        st.rerun()
