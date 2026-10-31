import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import streamlit as st

from src.analytics import habits as H
from src.ui.common import DAY_NAMES, empty, page, safe

svc = page("Habits", "🔁")
today = date.today()
habits, logs, goals = svc.habits(), svc.habit_logs(), svc.goals()
gname = {g.id: g.title for g in goals}

with st.expander("➕ New habit", expanded=not habits):
    with st.form("nh", clear_on_submit=True):
        title = st.text_input("Habit")
        c1, c2 = st.columns(2)
        rec = c1.selectbox("Recurrence", ["daily", "weekdays", "weekly"])
        goal = c2.selectbox("Linked goal (optional)", [None] + [g.id for g in goals], format_func=lambda i: gname.get(i, "— none —"))
        days = st.multiselect("Days (weekly only)", DAY_NAMES)
        start = st.date_input("Start date", today)
        if st.form_submit_button("Create", type="primary"):
            if safe(svc.create_habit, title=title, recurrence=rec, goal_id=goal, days=[DAY_NAMES.index(d) for d in days],
                    start_date=start.isoformat(), success="Habit created"):
                st.rerun()

if not habits:
    empty("No habits yet. Create one above to start tracking streaks.")
for h in habits:
    with st.container(border=True):
        a, b = st.columns([4, 2])
        a.markdown(f"**{h.title}**  " + ("" if h.active else "_(inactive)_"))
        rates = H.weekly_monthly(h, logs, today)
        fmt = lambda v: "n/a" if v is None else f"{v:.0%}"
        a.caption(f"{h.recurrence} · 🔥 streak {H.current_streak(h, logs, today)} (best {H.longest_streak(h, logs, today)}) · "
                  f"7-day {fmt(rates['week'])} · 30-day {fmt(rates['month'])}")
        done_today = any(l.habit_id == h.id and l.date == today.isoformat() and l.done for l in logs)
        if H.is_due(h, today):
            if b.button("✅ Done today" if not done_today else "↩️ Undo today", key=f"hd{h.id}"):
                safe(svc.log_habit, h.id, today, not done_today) and st.rerun()
        else:
            b.caption("Not due today")
        with st.expander("History & backfill"):
            d = st.date_input("Log a past day", value=today, max_value=today, key=f"bf{h.id}")
            c1, c2 = st.columns(2)
            if c1.button("Mark done", key=f"bd{h.id}"):
                safe(svc.log_habit, h.id, d, True) and st.rerun()
            if c2.button("Mark not done", key=f"bn{h.id}"):
                safe(svc.log_habit, h.id, d, False) and st.rerun()
            miss = H.missed_days(h, logs, today - timedelta(days=13), today - timedelta(days=1))
            st.write("Missed in last 14 days: " + (", ".join(m.strftime("%d %b") for m in miss) if miss else "none"))
            if st.button("Deactivate" if h.active else "Activate", key=f"ac{h.id}"):
                svc.set_habit_active(h.id, not h.active)
                st.rerun()
if habits:
    st.subheader("Last 28 days")
    rows = []
    for h in habits:
        done = {l.date for l in logs if l.habit_id == h.id and l.done}
        rows.append({"Habit": h.title, **{(today - timedelta(days=i)).strftime("%m-%d"): ("✅" if (today - timedelta(days=i)).isoformat() in done
                                                else ("·" if H.is_due(h, today - timedelta(days=i)) else "")) for i in range(27, -1, -1)}})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
