import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

from src.llm.factory import get_provider
from src.llm.ollama import OllamaProvider
from src.llm.openai_compat import OpenAICompatProvider
from src.ui.common import DAY_NAMES, page, safe

svc = page("Settings", "⚙️")
s = svc.settings

tab_llm, tab_sched, tab_notif, tab_misc = st.tabs(["LLM provider", "Scheduling", "Notifications", "ML & misc"])

with tab_llm:
    prov = st.selectbox("Provider", ["ollama", "openai_compatible", "none"], index=["ollama", "openai_compatible", "none"].index(s.llm_provider),
                        format_func={"ollama": "Ollama (local, no key)", "openai_compatible": "Hosted OpenAI-compatible (e.g. Groq free tier)", "none": "None (manual only)"}.get)
    c1, c2 = st.columns(2)
    ourl = c1.text_input("Ollama URL", s.ollama_url, help="In Docker use http://host.docker.internal:11434")
    omodel = c2.text_input("Ollama model", s.ollama_model)
    if c1.button("List installed Ollama models"):
        try:
            st.write(OllamaProvider(ourl, omodel).installed_models() or "No models installed. Run: ollama pull llama3.2")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    hurl = c1.text_input("Hosted base URL", s.hosted_base_url)
    hmodel = c2.text_input("Hosted model", s.hosted_model, help="Model names and free tiers change; check your provider's docs.")
    key_found = bool(os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY"))
    st.caption("API key: " + ("✅ found in environment/.env" if key_found else "❌ not found. Put LLM_API_KEY=... in the .env file and restart. Keys are never stored in JSON data.") )
    ack = st.checkbox("I understand hosted providers receive the text I submit", s.hosted_disclosure_ack)
    st.caption("Free tiers, quotas and model availability can change at any time; there is no guarantee of free access.")
    t1, t2, t3 = st.columns(3)
    to = t1.number_input("Timeout (s)", 5, 600, s.llm_timeout_seconds)
    rt = t2.number_input("Max retries", 0, 5, s.llm_max_retries)
    if st.button("Save LLM settings", type="primary"):
        safe(svc.update_settings, llm_provider=prov, ollama_url=ourl.strip(), ollama_model=omodel.strip(), hosted_base_url=hurl.strip(),
             hosted_model=hmodel.strip(), llm_timeout_seconds=int(to), llm_max_retries=int(rt), hosted_disclosure_ack=ack, success="Saved")
        st.session_state.pop("llm_status", None)
    if st.button("Test connection"):
        from dataclasses import replace
        p = get_provider(replace(svc.settings, llm_provider=prov, ollama_url=ourl, ollama_model=omodel, hosted_base_url=hurl, hosted_model=hmodel))
        if p is None:
            st.info("No provider selected.")
        else:
            with st.spinner("Checking..."):
                stat = p.check()
            (st.success if stat.ok else st.error)(stat.message)

with tab_sched:
    wd = st.multiselect("Working days", DAY_NAMES, [DAY_NAMES[d] for d in s.working_days])
    c1, c2 = st.columns(2)
    ds, de = c1.text_input("Day start (HH:MM)", s.day_start), c2.text_input("Day end (HH:MM)", s.day_end)
    c3, c4, c5 = st.columns(3)
    cap = c3.number_input("Daily workload limit (min)", 15, 1440, s.daily_capacity_minutes, 15)
    brk = c4.number_input("Break between blocks (min)", 0, 120, s.break_minutes, 5)
    buf = c5.number_input("Buffer on estimates (%)", 0, 100, s.buffer_percent, 5)
    c6, c7 = st.columns(2)
    mx = c6.number_input("Max block length (min)", 10, 600, s.max_block_minutes, 5)
    mn = c7.number_input("Min block length (min)", 5, 240, s.min_block_minutes, 5)
    st.write("**Priority rule weights** (score = urgency + priority + dependency impact + overdue)")
    w = st.columns(4)
    wu = w[0].number_input("Urgency", 0.0, 20.0, s.weight_urgency, 0.5)
    wp = w[1].number_input("Priority", 0.0, 20.0, s.weight_priority, 0.5)
    wd_ = w[2].number_input("Dependency", 0.0, 20.0, s.weight_dependency, 0.5)
    wo = w[3].number_input("Overdue", 0.0, 20.0, s.weight_overdue, 0.5)
    sig = st.number_input("Ask for confirmation when a task moves by more than (min)", 0, 1440, s.significant_change_minutes, 15)
    prot = st.checkbox("Protect high-priority tasks (priority ≥ 4 are always scheduled before lower-priority ones)", s.protect_high_priority)
    if st.button("Save scheduling settings", type="primary"):
        safe(svc.update_settings, working_days=[DAY_NAMES.index(d) for d in wd], day_start=ds, day_end=de, daily_capacity_minutes=int(cap),
             break_minutes=int(brk), buffer_percent=int(buf), max_block_minutes=int(mx), min_block_minutes=int(mn),
             weight_urgency=wu, weight_priority=wp, weight_dependency=wd_, weight_overdue=wo,
             significant_change_minutes=int(sig), protect_high_priority=prot,
             success="Saved. Rebuild the schedule to apply.")

with tab_notif:
    n = {k: st.checkbox(k.replace("notify_", "").replace("_", " ").title(), getattr(s, k)) for k in
         ("notify_overdue", "notify_deadlines", "notify_at_risk", "notify_blocked", "notify_missed", "notify_habits")}
    dwd = st.number_input("Warn this many days before a deadline", 0, 60, s.deadline_warning_days)
    st.caption("Alerts appear in the app only while it is open. Desktop/background notifications are not implemented.")
    if st.button("Save notification settings", type="primary"):
        safe(svc.update_settings, deadline_warning_days=int(dwd), **n, success="Saved")

with tab_misc:
    ml = st.checkbox("Enable ML insights (optional)", s.ml_enabled)
    mls = st.number_input("Minimum completed tasks with recorded time before training", 10, 1000, s.ml_min_samples)
    keep = st.number_input("Backups to keep per file", 1, 100, s.backup_keep)
    if st.button("Save", type="primary"):
        safe(svc.update_settings, ml_enabled=ml, ml_min_samples=int(mls), backup_keep=int(keep), success="Saved")
    st.caption("Backups, exports and demo data are on the Exports page.")
