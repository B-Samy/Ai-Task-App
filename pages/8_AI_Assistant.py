import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

from src.services import assistant
from src.ui.common import page, provider_or_none, safe

svc = page("AI Assistant", "💬")
s = svc.settings
st.caption("Answers about *what's next, why you're behind, deadlines, progress, habits* are computed locally. Free-form questions use your LLM "
           "with a small slice of task context. The assistant can only **propose** actions; nothing changes until you confirm.")
if s.llm_provider == "openai_compatible":
    st.warning("Hosted provider active: free-form questions send a small summary of your tasks to the provider.")

for m in svc.chat_history():
    with st.chat_message(m["role"]):
        st.write(m["content"])
        if m.get("source"):
            st.caption(f"source: {m['source']}")

if st.session_state.get("pending_proposal"):
    st.info("Proposed action: **rebuild schedule** (history is preserved).")
    a, b = st.columns(2)
    if a.button("✅ Confirm and apply", type="primary"):
        msg = safe(assistant.confirm, svc, st.session_state.pop("pending_proposal"))
        if msg:
            svc.append_chat("assistant", msg, source="local")
            st.rerun()
    if b.button("Cancel proposal"):
        st.session_state.pop("pending_proposal")
        svc.append_chat("assistant", "Cancelled. Nothing was changed.", source="local")
        st.rerun()

st.write("Try:")
cols = st.columns(4)
quick = ["What should I work on next?", "Why am I behind schedule?", "Which deadlines are at risk this week?", "How much progress did I make this month?"]
clicked = next((q for c, q in zip(cols, quick) if c.button(q, use_container_width=True)), None)
prompt = st.chat_input("Ask about your tasks...") or clicked
if prompt:
    svc.append_chat("user", prompt)
    prov = provider_or_none(svc) if (s.llm_provider != "openai_compatible" or s.hosted_disclosure_ack) else None
    with st.spinner("Thinking..."):
        rep = assistant.answer(svc, prompt, date.today(), prov)
    svc.append_chat("assistant", rep.text, source=rep.source)
    if rep.proposal:
        st.session_state.pending_proposal = rep.proposal
    st.rerun()
if st.button("Clear chat history"):
    svc.clear_chat()
    st.rerun()
