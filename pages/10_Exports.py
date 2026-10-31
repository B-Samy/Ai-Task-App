import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

from src.analytics import metrics as M
from src.utils import demo
from src.utils.exports import report_markdown, safe_filename, tasks_rows, to_csv, to_json
from src.ui.common import empty, page, safe

svc = page("Exports & Backup", "📦")
today = date.today()
goals, tasks, blocks, habits, logs, acts, s = (svc.goals(), svc.tasks(), svc.blocks(), svc.habits(), svc.habit_logs(), svc.activity(), svc.settings)
stamp = today.isoformat()

st.subheader("Download")
st.caption("Files are UTF-8. Exports contain only your task data. API keys are never stored in the data files.")
sets = {
    "goals_and_tasks": {"csv": to_csv(tasks_rows(tasks, goals)), "json": to_json({"goals": [g.to_dict() for g in goals], "tasks": [t.to_dict() for t in tasks]})},
    "schedule": {"csv": to_csv([b.to_dict() for b in blocks]), "json": to_json([b.to_dict() for b in blocks])},
    "habits": {"csv": to_csv([{**l.to_dict()} for l in logs], ["habit_id", "date", "done", "note"]),
               "json": to_json({"habits": [h.to_dict() for h in habits], "habit_logs": [l.to_dict() for l in logs]})},
}
cols = st.columns(3)
for col, (name, fmts) in zip(cols, sets.items()):
    col.write(f"**{name.replace('_', ' ').title()}**")
    for ext, data in fmts.items():
        col.download_button(f"{ext.upper()}", data, file_name=safe_filename(f"{name}_{stamp}.{ext}"),
                            mime="text/csv" if ext == "csv" else "application/json", key=f"dl_{name}_{ext}")
report = M.full_report(goals, tasks, blocks, habits, logs, acts, s, today)
st.write("**Analytics report**")
r1, r2 = st.columns(2)
r1.download_button("Markdown", report_markdown(report), file_name=safe_filename(f"report_{stamp}.md"), mime="text/markdown")
r2.download_button("JSON", to_json(report), file_name=safe_filename(f"report_{stamp}.json"), mime="application/json")

st.divider()
st.subheader("Backup & restore")
st.caption("A full backup is a ZIP of all JSON data files. Restoring validates every file first and saves a pre-restore backup of your current data.")
if st.button("Create full backup"):
    z = safe(svc.store.create_backup_zip)
    if z and z is not True:
        st.session_state.last_backup = Path(z).read_bytes()
        st.session_state.last_backup_name = Path(z).name
        st.success(f"Backup saved in data/backups/{Path(z).name}")
if st.session_state.get("last_backup"):
    st.download_button("⬇️ Download that backup", st.session_state.last_backup, file_name=st.session_state.last_backup_name, mime="application/zip")
up = st.file_uploader("Restore from a backup ZIP", type=["zip"])
if up is not None:
    st.warning("Restoring replaces current data files with the ZIP's contents (a pre-restore backup is kept).")
    if st.button("Restore now", type="primary"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(up.getvalue())
        res = safe(svc.store.restore_backup_zip, tmp.name)
        if res:
            svc.log("restore", f"Restored: {', '.join(res) if res is not True else 'files'}")
            st.session_state.pop("svc", None)
            st.success("Restored. Reloading...")
            st.rerun()

st.divider()
st.subheader("Demo data")
st.caption("Synthetic examples, clearly marked '[Demo]'. Never loaded automatically; Reset removes **only** demo records.")
d1, d2 = st.columns(2)
has = demo.has_demo(svc.store)
if d1.button("Load demo data", disabled=has):
    if safe(demo.load_demo, svc.store, success="Demo data loaded"):
        svc.log("demo_loaded", "Demo data loaded by user.")
        st.rerun()
if d2.button("Reset demo data", disabled=not has):
    if safe(demo.reset_demo, svc.store, success="Demo data removed"):
        svc.log("demo_reset", "Demo data removed by user.")
        st.rerun()

st.divider()
st.subheader("Activity history")
if not acts:
    empty("No activity yet.")
for a in list(reversed(acts))[:100]:
    st.write(f"`{a.timestamp}` · {a.message}")
