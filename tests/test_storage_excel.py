import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from com_domain_filter.excel_store import ExcelStore, HEADERS, SHEET_NAME
from com_domain_filter.storage import HistoryStore, SettingsStore


class StorageAndExcelTests(unittest.TestCase):
    def test_history_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "state.db")
            args = ("abc.com", "exact_unavailable", "2026-08-21T12:00:00+08:00", "AAA", "", "", "")
            self.assertTrue(store.record(*args))
            self.assertFalse(store.record(*args))
            self.assertTrue(store.has_tested("ABC.COM"))
            self.assertEqual(store.total_count(), 1)

    def test_query_is_reserved_before_result_and_then_finalized(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "state.db")
            self.assertTrue(store.reserve("abc.com", "start", "AAA", "", ""))
            self.assertTrue(store.has_tested("abc.com"))
            self.assertFalse(store.reserve("abc.com", "start", "AAA", "", ""))
            self.assertTrue(
                store.finalize("abc.com", "exact_unavailable", "finish", "AAA", "", "", "done")
            )
            with store._connect() as connection:
                row = connection.execute(
                    "SELECT status, checked_at, detail FROM tested_domains WHERE domain = 'abc.com'"
                ).fetchone()
            self.assertEqual(row, ("exact_unavailable", "finish", "done"))

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp) / "settings.json")
            store.save({"patterns": ["AAA"], "interval": "5"})
            self.assertEqual(store.load()["patterns"], ["AAA"])

    def test_excel_append_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "results.xlsx"
            store = ExcelStore(path)
            args = (
                "abc123.com",
                "2026-08-21T12:00:00+08:00",
                "ABC",
                "",
                "",
                "https://domains.cloudflare.com/",
            )
            self.assertTrue(store.append_if_new(*args))
            self.assertFalse(store.append_if_new(*args))
            workbook = load_workbook(path)
            sheet = workbook[SHEET_NAME]
            self.assertEqual(tuple(cell.value for cell in sheet[1]), HEADERS)
            self.assertEqual(sheet.max_row, 2)
            self.assertEqual(sheet["A2"].value, "abc123.com")
            self.assertEqual(sheet.freeze_panes, "A2")


if __name__ == "__main__":
    unittest.main()
