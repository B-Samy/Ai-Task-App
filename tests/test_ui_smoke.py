"""Import/run smoke test of every Streamlit page against a lightweight stub of `streamlit`.

This is NOT a browser test: it executes each page's Python top to bottom with widgets returning defaults
(buttons unpressed). It catches NameError/AttributeError/logic errors but not layout or real widget behaviour.
If the real `streamlit` is installed it is temporarily shadowed by the stub for this test only.
"""
import os
import runpy
import sys
import tempfile
import types
import unittest
from datetime import date, datetime
from pathlib import Path

from tests.helpers import ROOT


_STUB = {}


class _Ctx:
    """Context manager that also exposes widgets (so `col.button(...)` behaves like `st.button(...)`)."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, name):
        mod = _STUB.get("m")
        if mod is not None and name in mod.__dict__:
            return mod.__dict__[name]
        return _Callable()
    def __iter__(self): return iter([])


class _Callable(_Ctx):
    def __call__(self, *a, **k): return _Ctx()


class _State(dict):
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v
    def __delattr__(self, k): self.pop(k, None)


def make_stub():
    m = types.ModuleType("streamlit")
    m.session_state = _State()
    def first_opt(label, options=(), index=0, *a, **k):
        opts = list(options)
        return opts[index] if opts and 0 <= (index or 0) < len(opts) else None
    m.columns = lambda spec, **k: [_Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
    m.tabs = lambda labels: [_Ctx() for _ in labels]
    m.button = lambda *a, **k: False
    m.form_submit_button = lambda *a, **k: False
    m.download_button = lambda *a, **k: False
    m.checkbox = lambda label, value=False, **k: value
    m.text_input = lambda label, value="", **k: value
    m.text_area = lambda label, value="", **k: value
    m.number_input = lambda label, min_value=0, max_value=0, value=0, *a, **k: value
    m.slider = lambda label, mn=0, mx=0, value=0, *a, **k: value
    m.date_input = lambda label, value=None, **k: value
    m.selectbox = first_opt
    m.radio = first_opt
    m.multiselect = lambda label, options=(), default=None, **k: list(default or [])
    m.data_editor = lambda df, **k: df
    m.chat_input = lambda *a, **k: None
    m.file_uploader = lambda *a, **k: None
    m.stop = lambda: (_ for _ in ()).throw(AssertionError("st.stop() called: page hit an error path"))
    m.rerun = lambda: None
    m.switch_page = lambda *a, **k: None
    m.column_config = _Callable()
    m.sidebar = _Ctx()
    for n in ("expander", "container", "form", "spinner", "chat_message", "empty"):
        setattr(m, n, lambda *a, **k: _Ctx())
    for n in ("set_page_config markdown title subheader header caption write info warning error success toast divider metric progress "
              "dataframe bar_chart line_chart json table code page_link").split():
        setattr(m, n, lambda *a, **k: None)
    _STUB["m"] = m
    return m


class UiSmoke(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("streamlit")
        self.stub = make_stub()
        sys.modules["streamlit"] = self.stub
        for k in [k for k in sys.modules if k.startswith("src.ui")]:
            del sys.modules[k]
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TASK_INTEL_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        if self._saved is not None:
            sys.modules["streamlit"] = self._saved
        else:
            sys.modules.pop("streamlit", None)
        os.environ.pop("TASK_INTEL_DATA_DIR", None)
        self.tmp.cleanup()

    def pages(self):
        return [ROOT / "app.py"] + sorted((ROOT / "pages").glob("*.py"), key=lambda p: int(p.name.split("_")[0]))

    def run_all(self):
        for p in self.pages():
            with self.subTest(page=p.name):
                self.stub.session_state.clear()
                runpy.run_path(str(p), run_name="__main__")

    def test_all_pages_run_on_empty_data(self):
        self.run_all()

    def test_all_pages_run_with_demo_data_and_schedule(self):
        from src.services.app_service import AppService
        from src.storage.json_store import JsonStore
        from src.utils.demo import load_demo
        store = JsonStore(self.tmp.name)
        load_demo(store)
        svc = AppService(store)
        res, _ = svc.propose_schedule()
        svc.apply_schedule(res, "smoke")
        svc.mark_missed(svc.tasks()[0].id)
        self.run_all()
