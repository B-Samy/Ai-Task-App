"""AI Task Intelligence System: entry point (Dashboard). Run: streamlit run app.py"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from src.analytics import metrics as M
from src.services.assistant import next_task
from src.ui.common import RISK_ICON, STATUS_ICON, empty, page, safe
from src.notifications.alerts import compute_alerts

svc = page("Dashboard", "🧭")
today = date.today()
s, goals, tasks, blocks = svc.settings, svc.goals(), svc.tasks(), svc.blocks()
habits, logs = svc.habits(), svc.habit_logs()
by_id = {t.id: t for t in tasks}

c1, c2 = st.columns([3, 1])
c1.caption(f"{today.strftime('%A, %d %B %Y')}")
if c2.button("✨ Plan a New Goal with AI", type="primary", use_container_width=True):
    st.switch_page("pages/3_AI_Planner.py")

if not goals and not tasks:
    empty("Welcome! Start by planning a goal with AI, or add one manually under Goals. "
          "You can also load clearly-labelled demo data from the Exports page (it is never loaded automatically).")

tc = M.task_counts(tasks)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Completed", tc["completed"])
m2.metric("Pending", tc["pending"])
m3.metric("Overdue", len(M.overdue_tasks(tasks, today, goals)))
m4.metric("Active goals", sum(g.status == "active" for g in goals))

alerts = compute_alerts(goals, tasks, blocks, habits, logs, s, today)
if alerts:
    with st.expander(f"🔔 Alerts ({len(alerts)})", expanded=any(a.severity == "critical" for a in alerts)):
        for a in alerts[:20]:
            {"critical": st.error, "warning": st.warning, "info": st.info}[a.severity](a.message)

left, right = st.columns([3, 2])
with left:
    st.subheader("Today")
    todays = [b for b in blocks if b.date == today.isoformat() and b.status in ("planned", "completed", "missed", "skipped")]
    if not todays:
        nt = next_task(svc, today)
        empty("Nothing scheduled for today. " + (f"Suggested next: **{nt[0].title}**. " if nt else "") + "Open Schedule to build a plan.")
    for b in sorted(todays, key=lambda x: x.start):
        t = by_id.get(b.task_id)
        if not t:
            continue
        a, bcol = st.columns([4, 1])
        a.write(f"{STATUS_ICON.get(t.status, '')} **{b.start}-{b.end}** · {t.title}  \n<span class='small'>{b.status}</span>", unsafe_allow_html=True)
        if b.status == "planned" and t.status != "done" and bcol.button("Done", key=f"d_{b.id}"):
            if safe(svc.complete_task, t.id, success="Completed"):
                st.rerun()

    st.subheader("Upcoming deadlines")
    due = sorted([t for t in tasks if t.deadline and t.status not in ("done", "archived")], key=lambda t: t.deadline)[:6]
    if not due:
        empty("No upcoming deadlines.")
    for t in due:
        st.write(f"📅 {t.deadline} · {t.title}")

with right:
    st.subheader("Goals")
    risks = {r["goal_id"]: r for r in M.goal_risks(goals, tasks, s, today)}
    shown = [g for g in goals if g.status == "active"]
    if not shown:
        empty("No active goals.")
    for g in shown:
        p = svc.goal_progress(g.id)
        r = risks.get(g.id, {})
        st.write(f"{RISK_ICON.get(r.get('risk', 'none'), '')} **{g.title}**")
        st.progress(min(p["pct"] / 100, 1.0), text=f"{p['pct']}% · risk (heuristic): {r.get('risk', 'n/a')}")
    st.subheader("Habits")
    hs = M.habit_summary([h for h in habits if h.active], logs, today)
    if not hs:
        empty("No habits yet.")
    for h in hs:
        st.write(f"{'✅' if h['done_today'] else ('⬜' if h['due_today'] else '➖')} {h['title']} · 🔥 {h['streak']}")
