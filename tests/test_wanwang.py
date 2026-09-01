import unittest

from com_domain_filter.cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
    TransientPageError,
)
from com_domain_filter.wanwang import (
    WanwangChecker,
    classify_wanwang_cards,
    confirmed_wanwang_result,
)


class BrokenPage:
    def goto(self, *args, **kwargs):
        raise OSError("temporary network failure")


class FakeTextLocator:
    def __init__(self, visible):
        self.visible = visible
        self.first = self

    def count(self):
        return int(self.visible)

    def is_visible(self):
        return self.visible


class EmptyResultPage:
    def get_by_text(self, text, exact=True):
        return FakeTextLocator(text == "暂无搜索内容")


class DynamicCardsLocator:
    def __init__(self, page):
        self.page = page

    def evaluate_all(self, _script):
        return self.page.cards[min(self.page.step, len(self.page.cards) - 1)]


class DynamicResultPage:
    def __init__(self):
        self.step = 0
        self.cards = [
            [{"name": "abc", "suffix": ".xyz", "text": "立即注册", "registerHref": "/commonbuy"}],
            [
                {"name": "abc", "suffix": ".xyz", "text": "立即注册", "registerHref": "/commonbuy"},
                {"name": "abc", "suffix": ".vip", "text": "立即注册", "registerHref": "/commonbuy"},
                {"name": "abc", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"},
            ],
            [
                {"name": "abc", "suffix": ".xyz", "text": "立即注册", "registerHref": "/commonbuy"},
                {"name": "abc", "suffix": ".vip", "text": "立即注册", "registerHref": "/commonbuy"},
                {"name": "abc", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"},
            ],
        ]

    def goto(self, url, **_kwargs):
        self.last_url = url
        return None

    def get_by_text(self, _text, exact=True):
        return FakeTextLocator(False)

    def locator(self, selector):
        if selector == "main h4":
            return DynamicCardsLocator(self)
        raise AssertionError(selector)

    def wait_for_timeout(self, _milliseconds):
        self.step += 1


class TransientEmptyThenResultPage:
    def __init__(self):
        self.step = 0
        self.elapsed = 0.0

    def goto(self, *_args, **_kwargs):
        return None

    def get_by_text(self, text, exact=True):
        return FakeTextLocator(text == "暂无搜索内容" and self.step < 5)

    def locator(self, selector):
        if selector != "main h4":
            raise AssertionError(selector)
        return DynamicCardsLocator(self)

    @property
    def cards(self):
        if self.step < 5:
            return [[]]
        return [[{"name": "abc", "suffix": ".com", "text": "abc.com 已注册", "registerHref": ""}]]

    def wait_for_timeout(self, milliseconds):
        self.step += 1
        self.elapsed += milliseconds / 1000


class StableEmptyResultPage(TransientEmptyThenResultPage):
    def get_by_text(self, text, exact=True):
        return FakeTextLocator(text == "暂无搜索内容")

    @property
    def cards(self):
        return [[]]


class GroupScrollResultPage:
    """模拟万网先显示常用后缀，滚动后再加载更多后缀。"""

    def __init__(self):
        self.elapsed = 0.0
        self.scrolls = 0
        self.first_scroll_elapsed = None
        self.last_url = ""

    def goto(self, url, **_kwargs):
        self.last_url = url

    def get_by_text(self, _text, exact=True):
        return FakeTextLocator(False)

    def locator(self, selector):
        if selector != "main h4":
            raise AssertionError(selector)
        return DynamicCardsLocator(self)

    @property
    def step(self):
        return 0

    @property
    def cards(self):
        cards = [
            {"name": "abc", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"},
        ]
        if self.scrolls:
            cards.append(
                {"name": "abc", "suffix": ".net", "text": "abc.net 已注册", "registerHref": ""}
            )
        return [cards]

    def evaluate(self, script):
        if "document.readyState" in script:
            return "complete"
        if "return [" in script:
            if self.scrolls:
                return [1000, 800, 1800]
            return [0, 800, 2200]
        if "scrollTo" in script:
            if self.first_scroll_elapsed is None:
                self.first_scroll_elapsed = self.elapsed
            self.scrolls += 1
            return None
        raise AssertionError(script)

    def wait_for_timeout(self, milliseconds):
        self.elapsed += milliseconds / 1000


class SlowLoadingGroupPage(GroupScrollResultPage):
    """主文档尚未完成时即使出现首批卡片也绝不能滚动。"""

    def evaluate(self, script):
        if "document.readyState" in script:
            return "loading" if self.elapsed < 7 else "complete"
        return super().evaluate(script)


class WanwangClassificationTests(unittest.TestCase):
    def test_exact_available(self):
        cards = [{"name": "abc123", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"}]
        self.assertEqual(classify_wanwang_cards("abc123.com", cards).status, STATUS_EXACT_AVAILABLE)

    def test_exact_net_available(self):
        cards = [{"name": "abc123", "suffix": ".net", "text": "立即注册", "registerHref": "/commonbuy"}]
        result = classify_wanwang_cards("abc123.net", cards)
        self.assertEqual(result.status, STATUS_EXACT_AVAILABLE)
        self.assertEqual(result.returned_name, "abc123.net")

    def test_readable_chinese_suffix_matches_wanwang_card(self):
        cards = [{"name": "abc", "suffix": ".中国", "text": "abc.中国 立即注册", "registerHref": "/commonbuy"}]
        result = confirmed_wanwang_result("abc.中国", cards)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, STATUS_EXACT_AVAILABLE)
        self.assertEqual(result.query, "abc.中国")

    def test_old_punycode_query_matches_readable_wanwang_card(self):
        cards = [{"name": "abc", "suffix": ".中国", "text": "abc.中国 已注册", "registerHref": ""}]
        result = confirmed_wanwang_result("abc.xn--fiqs8s", cards)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, STATUS_EXACT_UNAVAILABLE)
        self.assertEqual(result.query, "abc.中国")

    def test_readable_query_matches_punycode_card_if_provider_returns_it(self):
        cards = [{"name": "abc", "suffix": ".xn--fiqs8s", "text": "abc.xn--fiqs8s 已注册", "registerHref": ""}]
        result = confirmed_wanwang_result("abc.中国", cards)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, STATUS_EXACT_UNAVAILABLE)
        self.assertEqual(result.query, "abc.中国")

    def test_exact_unavailable(self):
        cards = [{"name": "abc", "suffix": ".com", "text": "abc.com 已注册", "registerHref": ""}]
        self.assertEqual(classify_wanwang_cards("abc.com", cards).status, STATUS_EXACT_UNAVAILABLE)

    def test_registered_target_wins_over_recommended_register_action_in_same_row(self):
        cards = [
            {
                "name": "myqcq",
                "suffix": ".com",
                "headingText": "myqcq.com 推荐 换一换 qcq.com 已注册",
                "text": "myqcq.com 推荐 换一换 qcq.com 已注册 .com批量注册 立即注册",
                "registerHref": "action:立即注册",
            }
        ]
        classified = classify_wanwang_cards("qcq.com", cards)
        confirmed = confirmed_wanwang_result("qcq.com", cards)
        self.assertEqual(classified.status, STATUS_EXACT_UNAVAILABLE)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.status, STATUS_EXACT_UNAVAILABLE)

    def test_unrelated_registered_domain_does_not_block_exact_available_target(self):
        cards = [
            {
                "name": "abc",
                "suffix": ".com",
                "headingText": "abc.com",
                "text": "abc.com 立即注册 other.net 已注册",
                "registerHref": "/commonbuy",
            }
        ]
        result = confirmed_wanwang_result("abc.com", cards)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, STATUS_EXACT_AVAILABLE)

    def test_exact_registered_when_status_pollutes_extracted_name(self):
        cards = [
            {
                "name": "rbcf已注册",
                "suffix": ".com",
                "text": "rbcf.com 已注册 Whois信息 委托询价",
                "registerHref": "",
            }
        ]
        classified = classify_wanwang_cards("rbcf.com", cards)
        confirmed = confirmed_wanwang_result("rbcf.com", cards)
        self.assertEqual(classified.status, STATUS_EXACT_UNAVAILABLE)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed.status, STATUS_EXACT_UNAVAILABLE)

    def test_recommended_domain_is_not_mistaken_for_exact_domain(self):
        cards = [
            {
                "name": "myrbcf",
                "suffix": ".com",
                "text": "myrbcf.com 推荐 立即注册",
                "registerHref": "action:立即注册",
            }
        ]
        self.assertIsNone(confirmed_wanwang_result("rbcf.com", cards))
        result = classify_wanwang_cards("rbcf.com", cards)
        self.assertEqual(result.status, STATUS_AVAILABLE_MISMATCH)
        self.assertEqual(result.returned_name, "myrbcf.com")

    def test_exact_marketplace_domain_is_unavailable(self):
        cards = [
            {
                "name": "zkxj",
                "suffix": ".com",
                "text": "zkxj.com 一口价域名售卖中，立即下单购买，成交效率高 立即购买",
                "registerHref": "",
            }
        ]
        self.assertEqual(classify_wanwang_cards("zkxj.com", cards).status, STATUS_EXACT_UNAVAILABLE)
        result = confirmed_wanwang_result("zkxj.com", cards)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, STATUS_EXACT_UNAVAILABLE)

    def test_available_mismatch(self):
        cards = [{"name": "abc-new", "suffix": ".com", "text": "立即注册", "registerHref": "/commonbuy"}]
        result = classify_wanwang_cards("abc.com", cards)
        self.assertEqual(result.status, STATUS_AVAILABLE_MISMATCH)
        self.assertEqual(result.returned_name, "abc-new.com")

    def test_no_com(self):
        cards = [{"name": "abc", "suffix": ".cn", "text": "立即注册", "registerHref": "/commonbuy"}]
        self.assertEqual(classify_wanwang_cards("abc.com", cards).status, STATUS_NO_COM)

    def test_partial_cards_are_not_treated_as_final_result(self):
        cards = [{"name": "abc", "suffix": ".xyz", "text": "立即注册", "registerHref": "/commonbuy"}]
        self.assertIsNone(confirmed_wanwang_result("abc.com", cards))

    def test_query_waits_for_exact_com_even_when_it_appears_third(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        checker.page = DynamicResultPage()
        checker._timeout_error = TimeoutError
        checker.verification_present = lambda: False
        result = checker.query("abc.com")
        self.assertEqual(result.status, STATUS_EXACT_AVAILABLE)
        self.assertGreaterEqual(checker.page.step, 2)

    def test_query_uses_selected_net_suffix_in_url_and_cards(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        page = DynamicResultPage()
        page.cards = [[{"name": "abc", "suffix": ".net", "text": "立即注册", "registerHref": "/commonbuy"}]]
        checker.page = page
        checker._timeout_error = TimeoutError
        checker.verification_present = lambda: False
        result = checker.query("abc.net")
        self.assertEqual(result.status, STATUS_EXACT_AVAILABLE)
        self.assertIn("keyword=abc", page.last_url)
        self.assertIn("suffix=.net", page.last_url)

    def test_group_query_searches_stem_once_scrolls_and_records_missing_suffix(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        page = GroupScrollResultPage()
        checker.page = page
        checker._timeout_error = TimeoutError
        checker.verification_present = lambda: False
        module = __import__("com_domain_filter.wanwang", fromlist=["time"])
        original_monotonic = module.time.monotonic
        try:
            module.time.monotonic = lambda: page.elapsed
            results = checker.query_group("abc", (".com", ".net", ".cc"))
        finally:
            module.time.monotonic = original_monotonic

        self.assertIn("keyword=abc", page.last_url)
        self.assertIn("suffix=", page.last_url)
        self.assertNotIn("suffix=.cc", page.last_url)
        self.assertEqual(page.scrolls, 1)
        self.assertGreaterEqual(page.first_scroll_elapsed, 5)
        self.assertEqual(results["abc.com"].status, STATUS_EXACT_AVAILABLE)
        self.assertEqual(results["abc.net"].status, STATUS_EXACT_UNAVAILABLE)
        self.assertEqual(results["abc.cc"].status, STATUS_NO_COM)
        self.assertIn("页面无 .cc 后缀结果", results["abc.cc"].detail)

    def test_group_query_never_scrolls_before_document_and_initial_results_settle(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        page = SlowLoadingGroupPage()
        checker.page = page
        checker._timeout_error = TimeoutError
        checker.verification_present = lambda: False
        module = __import__("com_domain_filter.wanwang", fromlist=["time"])
        original_monotonic = module.time.monotonic
        try:
            module.time.monotonic = lambda: page.elapsed
            checker.query_group("abc", (".com", ".net", ".cc"))
        finally:
            module.time.monotonic = original_monotonic

        self.assertGreaterEqual(page.first_scroll_elapsed, 12)

    def test_transient_empty_result_is_not_mistaken_for_final_no_com(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        page = TransientEmptyThenResultPage()
        checker.page = page
        checker._timeout_error = TimeoutError
        checker.verification_present = lambda: False
        original_monotonic = __import__("com_domain_filter.wanwang", fromlist=["time"]).time.monotonic
        try:
            __import__("com_domain_filter.wanwang", fromlist=["time"]).time.monotonic = lambda: page.elapsed
            result = checker.query("abc.com")
        finally:
            __import__("com_domain_filter.wanwang", fromlist=["time"]).time.monotonic = original_monotonic
        self.assertEqual(result.status, STATUS_EXACT_UNAVAILABLE)
        self.assertGreaterEqual(page.step, 6)

    def test_stable_empty_result_is_final_no_com(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        page = StableEmptyResultPage()
        checker.page = page
        checker._timeout_error = TimeoutError
        checker.verification_present = lambda: False
        original_monotonic = __import__("com_domain_filter.wanwang", fromlist=["time"]).time.monotonic
        try:
            __import__("com_domain_filter.wanwang", fromlist=["time"]).time.monotonic = lambda: page.elapsed
            result = checker.query("abc.com")
        finally:
            __import__("com_domain_filter.wanwang", fromlist=["time"]).time.monotonic = original_monotonic
        self.assertEqual(result.status, STATUS_NO_COM)
        self.assertGreaterEqual(page.elapsed, 8)

    def test_network_failure_is_marked_for_automatic_refresh(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        checker.page = BrokenPage()
        checker._timeout_error = TimeoutError
        with self.assertRaises(TransientPageError):
            checker.query("abc.com")

    def test_visible_empty_result_is_detected(self):
        checker = WanwangChecker("https://wanwang.aliyun.com/domain", "/tmp/test-profile")
        checker.page = EmptyResultPage()
        self.assertTrue(checker._empty_result_present())


if __name__ == "__main__":
    unittest.main()
