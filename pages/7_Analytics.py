import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import streamlit as st

from src.analytics import metrics as M
from src.ml.duration import DurationModel
from src.ui.common import RISK_ICON, empty, page

svc = page("Analytics", "📊")
today = date.today()
goals, tasks, blocks, habits, logs, acts, s = (svc.goals(), svc.tasks(), svc.blocks(), svc.habits(), svc.habit_logs(),
                                                svc.activity(), svc.settings)
st.caption("🟢 **Observed** = counted from your data · 🟡 **Heuristic** = simple rules, not learned · 🔵 **ML** = trained model (only when enough history exists).")
if not tasks and not habits:
    empty("No data yet. Create goals and complete some tasks to see analytics.")

st.subheader("🟢 Observed metrics")
tc = M.task_counts(tasks)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Completed", tc["completed"]); c2.metric("Pending", tc["pending"])
c3.metric("Overdue", len(M.overdue_tasks(tasks, today, goals)))
ad = M.schedule_adherence(blocks, today)
c4.metric("Schedule adherence", "n/a" if ad["rate"] is None else f"{ad['rate']:.0%}", help=f"{ad['completed']} of {ad['blocks']} past blocks completed")
pva = M.planned_vs_actual(tasks)
if pva["tasks"]:
    st.write(f"Planned vs actual: {pva['planned_minutes']} min planned, {pva['actual_minutes']} min actual (ratio {pva['ratio']}) over {pva['tasks']} tasks.")
else:
    empty("Planned-vs-actual appears after you complete tasks with recorded time.")
col1, col2 = st.columns(2)
with col1:
    st.write("**Weekly productivity**")
    wk = pd.DataFrame(M.weekly_productivity(tasks, 8, today)).set_index("week_start")
    st.bar_chart(wk["tasks_completed"]) if wk["tasks_completed"].sum() else empty("No completions in the last 8 weeks.")
with col2:
    st.write("**Time per category (min)**")
    tbc = M.time_by_category(tasks)
    st.bar_chart(pd.Series(tbc)) if tbc else empty("No time recorded yet.")
col3, col4 = st.columns(2)
with col3:
    st.write("**Rescheduling frequency**")
    rf = pd.DataFrame(M.rescheduling_frequency(acts, 8, today)).set_index("week_start")
    st.bar_chart(rf["reschedules"]) if rf["reschedules"].sum() else empty("No reschedules yet.")
with col4:
    st.write("**Habits**")
    hs = M.habit_summary(habits, logs, today)
    if hs:
        st.dataframe(pd.DataFrame([{"Habit": h["title"], "Streak": h["streak"], "Best": h["longest"],
                                    "7d": None if h["week_rate"] is None else round(h["week_rate"] * 100), "30d": None if h["month_rate"] is None else round(h["month_rate"] * 100)}
                                   for h in hs]), hide_index=True, use_container_width=True)
    else:
        empty("No habits tracked.")

st.subheader("🟡 Goal deadline risk (heuristic)")
risks = M.goal_risks(goals, tasks, s, today)
if risks:
    st.dataframe(pd.DataFrame([{"Goal": f"{RISK_ICON[r['risk']]} {r['title']}", "Risk": r["risk"], "Needs (min)": r["need_minutes"],
                                "Capacity (min)": r["capacity_minutes"], "Progress %": r["progress_pct"]} for r in risks]),
                 hide_index=True, use_container_width=True)
    st.caption("Risk compares remaining estimated work (incl. buffer) with working-hour capacity before the deadline. It is not a statistical forecast.")
else:
    empty("No active goals.")

st.subheader("🔵 ML insights: task duration")
if not s.ml_enabled:
    st.info("ML insights are disabled in Settings.")
else:
    model = DurationModel(s.ml_min_samples)
    ins = model.train(tasks)
    st.write(f"**Status:** {ins.status.replace('_', ' ')}")
    st.progress(min(ins.samples / ins.needed, 1.0), text=f"{ins.samples}/{ins.needed} completed tasks with recorded time")
    st.write(ins.message)
    if ins.correction_ratio:
        st.write(f"Heuristic (not ML): you typically take **{ins.correction_ratio}×** your estimate (median).")
    if ins.status == "trained":
        st.write("Features used: " + ", ".join(ins.features) + ". Target: actual/estimate ratio. Only information known before starting a task is used.")
        st.json(ins.metrics)
        st.caption("Metrics are cross-validated mean absolute errors in minutes, computed from your data.")
        open_ = [t for t in tasks if t.status in ("todo", "in_progress")][:10]
        if open_:
            st.dataframe(pd.DataFrame([{"Task": t.title, "Your estimate": t.estimate_minutes, "Predicted": round(model.predict_minutes(t)[0]), "Kind": model.predict_minutes(t)[1]} for t in open_]),
                         hide_index=True, use_container_width=True)
