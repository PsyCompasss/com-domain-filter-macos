import unittest

from com_domain_filter.cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
    TransientPageError,
)
from com_domain_filter.wanwang import WanwangChecker, classify_wanwang_cards


class BrokenPage:
    def goto(self, *args, **kwargs):
        raise OSError("temporary network failure")


class WanwangClassificationTests(unittest.TestCase):
    def test_exact_available(self):
        cards = [{"name": "abc123", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"}]
        self.assertEqual(classify_wanwang_cards("abc123.com", cards).status, STATUS_EXACT_AVAILABLE)

    def test_exact_unavailable(self):
        cards = [{"name": "abc", "suffix": ".com", "text": "abc.com 已注册", "registerHref": ""}]
        self.assertEqual(classify_wanwang_cards("abc.com", cards).status, STATUS_EXACT_UNAVAILABLE)

    def test_available_mismatch(self):
        cards = [{"name": "abc-new", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"}]
        result = classify_wanwang_cards("abc.com", cards)
        self.assertEqual(result.status, STATUS_AVAILABLE_MISMATCH)
        self.assertEqual(result.returned_name, "abc-new.com")

    def test_no_com(self):
        cards = [{"name": "abc", "suffix": ".cn", "text": "立即注册", "registerHref": "/commonbuy"}]
        self.assertEqual(classify_wanwang_cards("abc.com", cards).status, STATUS_NO_COM)

    def test_network_failure_is_marked_for_automatic_refresh(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        checker.page = BrokenPage()
        checker._timeout_error = TimeoutError
        with self.assertRaises(TransientPageError):
            checker.query("abc.com")


if __name__ == "__main__":
    unittest.main()
