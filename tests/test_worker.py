import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from com_domain_filter.cloudflare import QueryResult, STATUS_EXACT_AVAILABLE
from com_domain_filter.excel_store import SHEET_NAME
from com_domain_filter.storage import HistoryStore
from com_domain_filter.worker import RunConfig, SearchWorker


class FakeChecker:
    def __init__(self, site_url, profile_dir):
        self.site_url = site_url

    def start(self):
        pass

    def close(self):
        pass

    def verification_present(self):
        return False

    def query(self, domain):
        return QueryResult(domain, STATUS_EXACT_AVAILABLE, domain, True, True)


class WorkerTests(unittest.TestCase):
    def test_worker_stops_at_test_limit_and_writes_excel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = []
            config = RunConfig(
                site_url="https://domains.cloudflare.com/",
                characters=("a", "b", "c"),
                patterns=("AAA",),
                prefix="musa",
                suffix="",
                unlimited_length=6,
                interval_seconds=0.01,
                limit_tests_enabled=True,
                limit_tests=2,
                limit_found_enabled=False,
                limit_found=99,
                run_until_stopped=False,
                excel_path=root / "results.xlsx",
                profile_dir=root / "profile",
            )
            worker = SearchWorker(
                config,
                HistoryStore(root / "state.db"),
                lambda kind, payload: events.append((kind, payload)),
                checker_factory=FakeChecker,
            )
            worker.start()
            worker.thread.join(timeout=5)
            self.assertFalse(worker.is_alive)
            self.assertEqual(worker.checked, 2)
            self.assertEqual(worker.found, 2)
            self.assertTrue(any(kind == "finished" for kind, _ in events))
            workbook = load_workbook(config.excel_path)
            self.assertEqual(workbook[SHEET_NAME].max_row, 3)


if __name__ == "__main__":
    unittest.main()
