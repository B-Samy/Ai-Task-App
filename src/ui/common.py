"""Shared Streamlit helpers: service access, error handling, sidebar status, styling."""
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path
import streamlit as st 

try:
    for _k in ("LLM_API_KEY", "GROQ_API_KEY"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from src.config.settings import DEFAULT_DATA_DIR  # noqa: E402
from src.llm.factory import get_provider  # noqa: E402
from src.services.app_service import AppService, ServiceError  # noqa: E402
from src.storage.json_store import CorruptDataError, JsonStore, StorageError  # noqa: E402
from src.notifications.alerts import compute_alerts  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, filename=str(LOG_DIR / "app.log"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ui")

STATUS_ICON = {"todo": "⚪", "in_progress": "🔵", "paused": "⏸️", "blocked": "🔴", "done": "✅", "archived": "🗄️",
               "active": "🟢", "completed": "✅"}
RISK_ICON = {"high": "🔴", "medium": "🟠", "low": "🟢", "none": "⚪", "done": "✅"}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
h1, h2, h3 {letter-spacing: -0.01em;}
div[data-testid="stMetric"] {background: rgba(128,128,128,0.08); padding: 0.8rem 1rem; border-radius: 12px;}
.small {font-size: 0.85rem; opacity: 0.75;}
</style>
"""


def data_dir() -> Path:
    return Path(os.environ.get("TASK_INTEL_DATA_DIR", str(DEFAULT_DATA_DIR)))


def get_service() -> AppService:
    """Create the service once per session. Corrupt data shows recovery steps instead of a traceback."""
    if "svc" not in st.session_state:
        try:
            st.session_state.svc = AppService(JsonStore(data_dir()))
            st.session_state.svc.store.validate_all()
        except CorruptDataError as exc:
            st.error(str(exc))
            st.info("Nothing was overwritten. See docs/TROUBLESHOOTING.md ('Corrupt JSON').")
            st.stop()
        except StorageError as exc:
            st.error(f"Data problem: {exc}")
            st.stop()
    return st.session_state.svc


def page(title: str, icon: str = "🧭") -> AppService:
    st.set_page_config(page_title=f"{title} · AI Task Intelligence", page_icon=icon, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    svc = get_service()
    sidebar_status(svc)
    st.title(f"{icon} {title}")
    return svc


def sidebar_status(svc: AppService) -> None:
    s = svc.settings
    with st.sidebar:
        st.markdown("### AI Task Intelligence")
        key = "llm_status"
        if s.llm_provider == "none":
            st.caption("🤖 LLM: off (manual & local features only)")
        else:
            stat = st.session_state.get(key)
            label = f"{s.llm_provider} · {s.ollama_model if s.llm_provider == 'ollama' else s.hosted_model}"
            if stat is None:
                st.caption(f"🤖 {label} · not checked")
            else:
                st.caption(f"{'🟢' if stat.ok else '🔴'} {label}")
                if not stat.ok:
                    st.caption(stat.message)
            if st.button("Check LLM connection", key="sb_check"):
                with st.spinner("Checking..."):
                    p = get_provider(s)
                    st.session_state[key] = p.check() if p else None
                st.rerun()
        try:
            n = len(compute_alerts(svc.goals(), svc.tasks(), svc.blocks(), svc.habits(), svc.habit_logs(), s, date.today()))
            st.caption(f"🔔 {n} alert(s)" if n else "🔔 No alerts")
        except Exception:  # noqa: BLE001
            pass


def safe(fn, *args, success: str | None = None, **kwargs):
    """Run a service action, show friendly errors (never tracebacks), return result or None."""
    try:
        res = fn(*args, **kwargs)
        if success:
            st.toast(success)
        return res if res is not None else True
    except ServiceError as exc:
        st.error(str(exc))
    except CorruptDataError as exc:
        st.error(str(exc))
    except StorageError as exc:
        st.error(f"Could not save: {exc}")
    except Exception:  # noqa: BLE001
        log.exception("Unexpected error in %s", getattr(fn, "__name__", fn))
        st.error("Something went wrong. Details were written to logs/app.log (no secrets are logged).")
    return None


def provider_or_none(svc: AppService):
    return get_provider(svc.settings)


def disclose_if_external(svc: AppService) -> bool:
    """For hosted providers, require acknowledgement that task data is sent to a third party."""
    s = svc.settings
    if s.llm_provider != "openai_compatible":
        return True
    st.warning(f"Hosted provider selected: the text you submit (and a small slice of task context) is sent to {s.hosted_base_url}. "
               "Do not include private information you are not comfortable sharing.")
    return st.checkbox("I understand and want to use the hosted provider", value=s.hosted_disclosure_ack, key="ack_hosted")


def empty(msg: str) -> None:
    st.info(msg)
