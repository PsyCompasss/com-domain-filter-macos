import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from com_domain_filter.web_ui import WebApi
from com_domain_filter.tlds import COMMON_TLDS, IDN_TLDS, suffix_catalog_groups


class FakeWindow:
    def __init__(self, selected_path: Path):
        self.selected_path = selected_path

    def create_file_dialog(self, *_args, **_kwargs):
        return (str(self.selected_path),)


class WebApiImportTests(unittest.TestCase):
    def test_rule_page_places_suffix_pool_before_character_pool_and_uses_short_name(self):
        html = (Path(__file__).parents[1] / "web_ui" / "index.html").read_text(encoding="utf-8")
        suffix_position = html.index("<h2>后缀池</h2>")
        character_position = html.index("<h2>字符池</h2>")
        containment_position = html.index("<h2>至少包含</h2>")
        self.assertLess(suffix_position, character_position)
        self.assertLess(character_position, containment_position)
        self.assertNotIn("域名后缀池", html)

    def test_suffix_catalog_has_common_categories_and_readable_idn_labels(self):
        groups = suffix_catalog_groups()
        self.assertEqual(tuple(item["value"] for item in groups["common"]), COMMON_TLDS)
        self.assertEqual(len(groups["common"]), 30)
        self.assertTrue(groups["public"])
        self.assertTrue(groups["country"])
        self.assertEqual(len(groups["idn"]), len(IDN_TLDS))
        self.assertIn({"value": ".中国", "label": ".中国"}, groups["idn"])
        self.assertFalse(
            any("xn--" in f'{item["label"]}{item["value"]}' for items in groups.values() for item in items)
        )

    def test_preview_exposes_all_selected_suffixes_for_one_stem(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("com_domain_filter.web_ui.default_app_data_dir", return_value=root / "data"):
                api = WebApi()
            result = api.preview(
                {
                    "characters": ["a", "b", "c"],
                    "blocks": [{"kind": "unlimited", "length": 3}],
                    "domain_suffixes": [".com", ".net", ".cc"],
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(
                result["sample_groups"][0],
                [
                    f"{result['samples'][0].split('.', 1)[0]}.com",
                    f"{result['samples'][0].split('.', 1)[0]}.net",
                    f"{result['samples'][0].split('.', 1)[0]}.cc",
                ],
            )

    def test_text_import_accepts_words_and_com_domains_and_skips_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "domains.txt"
            source.write_text("domain\nAlpha\nbeta.com\nwrong.net\nalpha.com\n", encoding="utf-8")
            with patch("com_domain_filter.web_ui.default_app_data_dir", return_value=root / "data"):
                api = WebApi()
            api.set_window(FakeWindow(source))

            result = api.import_domains()

            self.assertTrue(result["ok"])
            self.assertEqual(result["domains"], ["alpha.com", "beta.com"])
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["skipped_count"], 2)

    def test_text_import_expands_words_to_selected_suffix_pool(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "domains.txt"
            source.write_text("Alpha\nbeta.net\nwrong.org\n", encoding="utf-8")
            with patch("com_domain_filter.web_ui.default_app_data_dir", return_value=root / "data"):
                api = WebApi()
            api.set_window(FakeWindow(source))

            result = api.import_domains([".com", ".net"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["domains"], ["alpha.com", "alpha.net", "beta.net"])
            self.assertEqual(result["skipped_count"], 1)


if __name__ == "__main__":
    unittest.main()
