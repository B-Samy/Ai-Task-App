import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

from src.analytics import metrics as M
from src.planning.dependencies import blocking_reasons
from src.ui.common import STATUS_ICON, empty, page, safe

svc = page("Tasks", "✅")
today = date.today()
goals, tasks, blocks, s = svc.goals(), svc.tasks(), svc.blocks(), svc.settings
by_id = {t.id: t for t in tasks}
gname = {g.id: g.title for g in goals}

with st.expander("➕ New task"):
    with st.form("new_task", clear_on_submit=True):
        title = st.text_input("Title")
        c1, c2, c3 = st.columns(3)
        goal = c1.selectbox("Goal", [None] + [g.id for g in goals], format_func=lambda i: gname.get(i, "— none —"))
        mins = c2.number_input("Estimate (min)", 5, 6000, 30, 5)
        pr = c3.slider("Priority", 1, 5, 3)
        c4, c5, c6 = st.columns(3)
        cat = c4.text_input("Category", "General")
        dl = c5.date_input("Deadline", value=None, min_value=today)
        split = c6.checkbox("Can be split into blocks", True)
        pre = st.multiselect("Prerequisites", [t.id for t in tasks if t.status != "archived"], format_func=lambda i: by_id[i].title)
        crit = st.text_input("Completion criteria")
        desc = st.text_area("Description")
        if st.form_submit_button("Create task", type="primary"):
            if safe(svc.create_task, title=title, goal_id=goal, estimate_minutes=int(mins), priority=pr, category=cat or "General",
                    deadline=dl.isoformat() if dl else None, splittable=split, prerequisites=pre,
                    completion_criteria=crit, description=desc, success="Task created"):
                st.rerun()

f1, f2, f3, f4, f5 = st.columns(5)
view = f1.selectbox("View", ["Today", "Upcoming", "Backlog", "Completed", "At-Risk", "All"])
q = f2.text_input("Search title")
fg = f3.selectbox("Goal", [None] + [g.id for g in goals], format_func=lambda i: gname.get(i, "All"))
fcat = f4.selectbox("Category", ["All"] + sorted({t.category for t in tasks}))
fpr = f5.selectbox("Min priority", [1, 2, 3, 4, 5])
fs = st.multiselect("Status", ["todo", "in_progress", "paused", "blocked", "done", "archived"])
ftype = st.text_input("Task type contains", "")

scheduled_today = {b.task_id for b in blocks if b.date == today.isoformat() and b.status in ("planned", "completed")}
scheduled_future = {b.task_id for b in blocks if b.status == "planned" and b.date > today.isoformat()}
at_risk_goals = {r["goal_id"] for r in M.goal_risks(goals, tasks, s, today) if r["risk"] in ("high", "medium")}
overdue = {t.id for t in M.overdue_tasks(tasks, today, goals)}


def keep(t):
    if view == "Today" and t.id not in scheduled_today: return False
    if view == "Upcoming" and (t.id not in scheduled_future or t.status in ("done", "archived")): return False
    if view == "Backlog" and (t.id in scheduled_today or t.id in scheduled_future or t.status in ("done", "archived")): return False
    if view == "Completed" and t.status != "done": return False
    if view == "At-Risk" and not (t.id in overdue or t.goal_id in at_risk_goals or t.status == "blocked") : return False
    if view != "Completed" and view != "All" and t.status in ("done", "archived") : return False
    if q and q.lower() not in t.title.lower(): return False
    if fg and t.goal_id != fg: return False
    if fcat != "All" and t.category != fcat: return False
    if t.priority < fpr: return False
    if fs and t.status not in fs: return False
    if ftype and ftype.lower() not in t.task_type.lower(): return False
    return True


shown = sorted([t for t in tasks if keep(t)], key=lambda t: (t.deadline or "9999", -t.priority))
st.caption(f"{len(shown)} task(s)")
if not shown:
    empty({"Today": "Nothing scheduled today. Build a schedule or pick from Backlog.", "Completed": "No completed tasks yet."}.get(view, "No tasks match."))
for t in shown:
    why = blocking_reasons(t, by_id)
    with st.container(border=True):
        a, b = st.columns([5, 3])
        a.markdown(f"{STATUS_ICON.get(t.status, '')} **{t.title}**" + ("  🔥 overdue" if t.id in overdue else ""))
        a.caption(f"{gname.get(t.goal_id, 'no goal')} · {t.category} · P{t.priority} · est {t.estimate_minutes} min · spent {t.actual_minutes} min · due {t.deadline or '—'}")
        if why and t.status not in ("done", "archived"):
            a.warning("Cannot start: " + " ".join(why))
        with b:
            if t.status not in ("done", "archived"):
                r1 = st.columns(3)
                if r1[0].button("Start", key=f"s{t.id}", disabled=bool(why)):
                    safe(svc.start_task, t.id) and st.rerun()
                if r1[1].button("Pause", key=f"p{t.id}", disabled=t.status != "in_progress"):
                    safe(svc.pause_task, t.id) and st.rerun()
                if r1[2].button("Done", key=f"d{t.id}", type="primary", disabled=any(not r.startswith("Manually") for r in why)):
                    safe(svc.complete_task, t.id, success="Completed") and st.rerun()
        if t.status not in ("done", "archived"):
            with st.expander("More actions / details"):
                st.write(t.description or "_No description_")
                if t.completion_criteria:
                    st.write(f"**Done when:** {t.completion_criteria}")
                c1, c2, c3 = st.columns(3)
                mins = c1.number_input("Log minutes", 0, 1440, 0, 5, key=f"m{t.id}")
                if c1.button("Record time", key=f"rt{t.id}") and mins:
                    safe(svc.record_time, t.id, int(mins), success="Recorded") and st.rerun()
                if c2.button("Skip today", key=f"sk{t.id}"):
                    safe(svc.skip_today, t.id, success="Skipped for today; rebuild the schedule") and st.rerun()
                if c2.button("Mark missed", key=f"mi{t.id}"):
                    safe(svc.mark_missed, t.id, success="Marked missed; rebuild the schedule") and st.rerun()
                if t.status == "blocked":
                    if c3.button("Unblock", key=f"ub{t.id}"):
                        safe(svc.unblock_task, t.id) and st.rerun()
                else:
                    reason = c3.text_input("Block reason", key=f"br{t.id}")
                    if c3.button("Mark blocked", key=f"bl{t.id}"):
                        safe(svc.block_task, t.id, reason) and st.rerun()
                nd = st.date_input("New deadline", value=date.fromisoformat(t.deadline) if t.deadline else None, key=f"dl{t.id}")
                if st.button("Change deadline", key=f"cd{t.id}"):
                    safe(svc.change_deadline, "task", t.id, nd.isoformat() if nd else None, success="Deadline changed") and st.rerun()
                others = [x.id for x in tasks if x.id != t.id and x.status != "archived"]
                pre = st.multiselect("Prerequisites", others, default=[p for p in t.prerequisites if p in others],
                                     format_func=lambda i: by_id[i].title, key=f"pre{t.id}")
                if st.button("Save prerequisites", key=f"sp{t.id}"):
                    safe(svc.update_task, t.id, prerequisites=pre, success="Saved") and st.rerun()
                if st.button("Archive task", key=f"ar{t.id}"):
                    safe(svc.archive_task, t.id, success="Archived") and st.rerun()
