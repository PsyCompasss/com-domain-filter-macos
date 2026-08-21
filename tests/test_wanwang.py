import unittest

from com_domain_filter.cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
)
from com_domain_filter.wanwang import classify_wanwang_cards


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


if __name__ == "__main__":
    unittest.main()
