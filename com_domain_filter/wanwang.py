from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from urllib.parse import quote, urlparse

from .cloudflare import (
    STATUS_AVAILABLE_MISMATCH,
    STATUS_EXACT_AVAILABLE,
    STATUS_EXACT_UNAVAILABLE,
    STATUS_NO_COM,
    CloudflareChecker,
    CloudflareError,
    PageStructureChanged,
    QueryResult,
    TransientPageError,
    VerificationRequired,
)


def classify_wanwang_cards(query: str, cards: list[dict]) -> QueryResult:
    normalized_domain = query.strip().lower()
    search_term = normalized_domain[:-4] if normalized_domain.endswith(".com") else normalized_domain
    exact = next(
        (
            card for card in cards
            if card.get("name") == search_term and card.get("suffix") == ".com"
        ),
        None,
    )
    if exact:
        if exact.get("registerHref"):
            return QueryResult(normalized_domain, STATUS_EXACT_AVAILABLE, normalized_domain, True, True)
        if "已注册" in str(exact.get("text", "")):
            return QueryResult(normalized_domain, STATUS_EXACT_UNAVAILABLE, normalized_domain, False, False)
    mismatch = next(
        (
            card for card in cards
            if card.get("name") != search_term
            and card.get("suffix") == ".com"
            and card.get("registerHref")
        ),
        None,
    )
    if mismatch:
        returned = f"{mismatch['name']}.com"
        return QueryResult(normalized_domain, STATUS_AVAILABLE_MISMATCH, returned, True, True)
    return QueryResult(normalized_domain, STATUS_NO_COM, "", False, False)


class WanwangChecker(CloudflareChecker):
    site_name = "阿里云万网"

    def __init__(self, site_url: str, profile_dir: Path) -> None:
        super().__init__(site_url, profile_dir)
        parsed = urlparse(site_url)
        self.result_base = f"{parsed.scheme or 'https'}://{parsed.netloc}/domain/searchresult/"

    def _search_box(self):
        candidates = [
            self.page.get_by_placeholder("请输入您想注册的域名，例如：wanwang"),
            self.page.get_by_placeholder("注册域名前先查询，如wanwang"),
        ]
        for candidate in candidates:
            if candidate.count() and candidate.first.is_visible():
                return candidate.first
        raise PageStructureChanged("找不到万网域名搜索输入框，页面结构可能已经变化。")

    def _search_button(self):
        candidates = [
            self.page.locator(".btn-query"),
            self.page.get_by_text("立即查询", exact=True),
        ]
        for candidate in candidates:
            if candidate.count() and candidate.first.is_visible():
                return candidate.first
        raise PageStructureChanged("找不到万网查询按钮，页面结构可能已经变化。")

    def _result_cards(self) -> list[dict]:
        cards = self.page.locator("main h4")
        return cards.evaluate_all(
            """
            (elements) => elements.map((heading) => {
              const suffix = (heading.querySelector('em')?.textContent || '').trim().toLowerCase();
              const name = Array.from(heading.childNodes)
                .filter((node) => node.nodeType === Node.TEXT_NODE)
                .map((node) => node.textContent || '')
                .join('').trim().toLowerCase();
              const container = heading.closest('.msea-domain-homon__domain-check') || heading.parentElement;
              let registerHref = '';
              const text = container?.innerText || heading.innerText || '';
              const link = container?.querySelector('a[href*="commonbuy"]');
              if (link) registerHref = link.getAttribute('href') || '';
              return {name, suffix, text, registerHref};
            })
            """
        )

    def _empty_result_present(self) -> bool:
        for text in ("暂无搜索内容", "暂无内容", "暂无数据"):
            locator = self.page.get_by_text(text, exact=True)
            if locator.count() and locator.first.is_visible():
                return True
        return False

    def query(self, domain: str) -> QueryResult:
        if not self.page:
            raise CloudflareError("浏览器尚未启动。")
        normalized_domain = domain.strip().lower()
        search_term = normalized_domain[:-4] if normalized_domain.endswith(".com") else normalized_domain
        url = f"{self.result_base}?keyword={quote(search_term)}&suffix=.com"
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + 60
            cards = []
            while time.monotonic() < deadline:
                if self.verification_present():
                    raise VerificationRequired("阿里云万网要求进行安全验证。")
                if self._empty_result_present():
                    return QueryResult(
                        normalized_domain,
                        STATUS_NO_COM,
                        "",
                        False,
                        False,
                        "万网显示暂无搜索内容",
                    )
                cards = self._result_cards()
                if cards:
                    # 万网会分批渲染卡片，精确的 .com 有时晚于其他结果出现。
                    self.page.wait_for_timeout(2_500)
                    cards = self._result_cards()
                    break
                self.page.wait_for_timeout(500)
            else:
                raise TransientPageError("万网查询结果暂时没有加载出来。")
        except self._timeout_error as exc:
            if self.verification_present():
                raise VerificationRequired("阿里云万网要求进行安全验证。") from exc
            raise TransientPageError("万网页面加载超时。") from exc
        except (VerificationRequired, TransientPageError, PageStructureChanged):
            raise
        except Exception as exc:
            raise TransientPageError(f"万网查询页面暂时加载失败：{exc}") from exc

        return classify_wanwang_cards(normalized_domain, cards)

    def wait_for_verification(self, stop_event: Event, timeout_seconds: int = 600) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while not stop_event.is_set() and time.monotonic() < deadline:
            if not self.verification_present():
                try:
                    if self.page.locator("body").is_visible():
                        return True
                except Exception:
                    pass
            stop_event.wait(1.0)
        return False
