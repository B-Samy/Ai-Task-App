import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.app_service import AppService  # noqa: E402
from src.storage.json_store import JsonStore  # noqa: E402


class TmpStoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = JsonStore(self.dir)
        self.svc = AppService(self.store)

    def tearDown(self):
        self._tmp.cleanup()
