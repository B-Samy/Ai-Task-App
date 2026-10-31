import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import streamlit as st

from src.ui.common import DAY_NAMES, empty, page, safe

svc = page("Schedule", "🗓️")
today = date.today()
tasks = {t.id: t for t in svc.tasks()}

st.caption("The plan is built by deterministic rules (working hours, capacity, priorities, dependencies). "
           "Rebuilding never deletes history: replaced blocks are kept as 'superseded'; past unfinished blocks become 'missed'.")

c1, c2 = st.columns([1, 3])
if c1.button("🔄 Build / rebuild schedule", type="primary"):
    res, prev = safe(svc.propose_schedule) or (None, None)
    if res:
        st.session_state.proposal = (res, prev)
if "proposal" in st.session_state:
    res, prev = st.session_state.proposal
    st.subheader("Proposed changes (not saved yet)")
    m1, m2, m3 = st.columns(3)
    m1.metric("Blocks proposed", len(res.blocks))
    m2.metric("Tasks moved or displaced", len(prev.moved))
    m3.metric("Conflicts", len(res.conflicts))
    for c in res.conflicts:
        (st.error if c.kind in ("late", "capacity", "unscheduled", "invalid") else st.warning)(f"{c.kind.upper()}: {c.message}")
    if prev.moved:
        st.write("**Old vs new first slot**")
        st.dataframe(pd.DataFrame([{"Task": m["title"], "Old": " ".join(m["old"]) if m["old"] else "—",
                                    "New": " ".join(m["new"]) if m["new"] else "removed", "Displaced": m["displaced"]} for m in prev.moved]),
                     use_container_width=True, hide_index=True)
    if prev.new_late:
        st.write("**Deadlines affected**")
        for l in prev.new_late:
            st.write(f"- {l['message']}")
    if not res.blocks and not res.conflicts:
        empty("Nothing to schedule. Add tasks first.")
    a, b = st.columns(2)
    if a.button("✅ Apply this schedule", type="primary", disabled=not res.blocks and not prev.moved):
        stats = safe(svc.apply_schedule, res, "Manual schedule rebuild", success="Schedule applied")
        if stats:
            st.session_state.pop("proposal")
            st.rerun()
    if b.button("Cancel"):
        st.session_state.pop("proposal")
        st.rerun()
    with st.expander("Why is each task where it is?"):
        for tid, why in res.explanations.items():
            st.write(f"**{tasks[tid].title if tid in tasks else tid}**: {why}")

st.divider()
st.subheader("Week view")
if "week_off" not in st.session_state:
    st.session_state.week_off = 0
n1, n2, n3 = st.columns([1, 1, 6])
if n1.button("◀ Prev"):
    st.session_state.week_off -= 1
if n2.button("Next ▶"):
    st.session_state.week_off += 1
ws = today - timedelta(days=today.weekday()) + timedelta(weeks=st.session_state.week_off)
n3.write(f"**Week of {ws.isoformat()}**")
blocks = [b for b in svc.blocks() if b.status != "superseded" and ws.isoformat() <= b.date <= (ws + timedelta(days=6)).isoformat()]
if not blocks:
    empty("No scheduled blocks this week.")
else:
    cols = st.columns(7)
    icon = {"planned": "🟦", "completed": "✅", "missed": "❌", "skipped": "⏭️"}
    for i, col in enumerate(cols):
        d = ws + timedelta(days=i)
        col.markdown(f"**{DAY_NAMES[i]} {d.day}**" + (" 📍" if d == today else ""))
        for b in sorted([x for x in blocks if x.date == d.isoformat()], key=lambda x: x.start):
            t = tasks.get(b.task_id)
            col.markdown(f"{icon.get(b.status, '')} `{b.start}-{b.end}`  \n{t.title if t else '?'}")

with st.expander("Activity history (rescheduling decisions)"):
    acts = [a for a in reversed(svc.activity()) if a.action.startswith(("schedule", "task_missed", "task_skipped", "deadline", "settings"))][:50]
    if not acts:
        empty("No rescheduling activity yet.")
    for a in acts:
        st.write(f"`{a.timestamp}` {a.message}")
