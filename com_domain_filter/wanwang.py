from __future__ import annotations

import re
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


WANWANG_UNAVAILABLE_MARKERS = (
    "已注册",
    "委托询价",
    "Whois信息",
    "WHOIS信息",
    "一口价域名售卖中",
    "立即下单购买",
    "域名售卖中",
    "立即购买",
)
EMPTY_RESULT_STABLE_SECONDS = 8.0


def _wanwang_unavailable(text: str) -> bool:
    return any(marker in text for marker in WANWANG_UNAVAILABLE_MARKERS)


def _is_exact_wanwang_card(query: str, card: dict) -> bool:
    """判断卡片是否对应用户查询的完整 .com 域名。

    万网会把“已注册”等状态标签放进域名标题本身。即使页面提取出的
    ``name`` 被这些标签污染，也可以用卡片正文中的完整域名作可靠兜底。
    边界判断可避免把 ``myabc.com`` 误当成 ``abc.com``。
    """
    normalized_domain = query.strip().lower()
    if not normalized_domain.endswith(".com"):
        normalized_domain = f"{normalized_domain}.com"
    search_term = normalized_domain[:-4]
    suffix = str(card.get("suffix", "")).strip().lower()
    if suffix != ".com":
        return False
    name = str(card.get("name", "")).strip().lower()
    if name == search_term:
        return True
    text = str(card.get("text", "")).lower()
    return re.search(
        rf"(?<![a-z0-9-]){re.escape(normalized_domain)}(?![a-z0-9-])",
        text,
    ) is not None


def classify_wanwang_cards(query: str, cards: list[dict]) -> QueryResult:
    normalized_domain = query.strip().lower()
    search_term = normalized_domain[:-4] if normalized_domain.endswith(".com") else normalized_domain
    exact = next(
        (
            card for card in cards
            if _is_exact_wanwang_card(normalized_domain, card)
        ),
        None,
    )
    if exact:
        if exact.get("registerHref"):
            return QueryResult(normalized_domain, STATUS_EXACT_AVAILABLE, normalized_domain, True, True)
        if _wanwang_unavailable(str(exact.get("text", ""))):
            return QueryResult(normalized_domain, STATUS_EXACT_UNAVAILABLE, normalized_domain, False, False)
    mismatch = next(
        (
            card for card in cards
            if not _is_exact_wanwang_card(normalized_domain, card)
            and str(card.get("suffix", "")).strip().lower() == ".com"
            and card.get("registerHref")
        ),
        None,
    )
    if mismatch:
        returned = f"{mismatch['name']}.com"
        return QueryResult(normalized_domain, STATUS_AVAILABLE_MISMATCH, returned, True, True)
    return QueryResult(normalized_domain, STATUS_NO_COM, "", False, False)


def confirmed_wanwang_result(query: str, cards: list[dict]) -> QueryResult | None:
    """只在精确的 .com 条目已经给出明确注册状态时返回结果。"""
    normalized_domain = query.strip().lower()
    search_term = normalized_domain[:-4] if normalized_domain.endswith(".com") else normalized_domain
    exact = next(
        (
            card for card in cards
            if _is_exact_wanwang_card(normalized_domain, card)
        ),
        None,
    )
    if exact is None:
        return None
    if exact.get("registerHref"):
        return QueryResult(
            normalized_domain,
            STATUS_EXACT_AVAILABLE,
            normalized_domain,
            True,
            True,
            "万网精确 .com 条目显示立即注册",
        )
    text = str(exact.get("text", ""))
    if _wanwang_unavailable(text):
        return QueryResult(
            normalized_domain,
            STATUS_EXACT_UNAVAILABLE,
            normalized_domain,
            False,
            False,
            "万网精确 .com 条目显示已注册或正在出售",
        )
    return None


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
              // 域名主体是 h4 的直接文本节点；“已注册”“推荐”等是子元素。
              // 不能直接删除 em 后读取整个 h4，否则会得到 rbcf已注册。
              const directName = Array.from(heading.childNodes)
                .filter((node) => node.nodeType === Node.TEXT_NODE)
                .map((node) => node.textContent || '')
                .join('')
                .trim()
                .toLowerCase();
              const headingText = (heading.textContent || '').trim().toLowerCase();
              const domainMatch = headingText.match(/([a-z0-9-]+)\s*(\.[a-z0-9-]+)\b/i);
              const name = directName || (domainMatch?.[1] || '').trim().toLowerCase();
              const container = heading.closest('.msea-domain-homon__domain-check') || heading.parentElement;
              let registerHref = '';
              const text = container?.innerText || heading.innerText || '';
              const actions = Array.from(container?.querySelectorAll('a, button') || []);
              const link = actions.find((action) => {
                const href = action.getAttribute('href') || '';
                const label = (action.innerText || action.textContent || '').trim();
                return href.includes('commonbuy') || label === '立即注册';
              });
              if (link) registerHref = link.getAttribute('href') || 'action:立即注册';
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
            confirmed = None
            consecutive_confirmations = 0
            empty_since = None
            exact_com_seen = False
            while time.monotonic() < deadline:
                if self.verification_present():
                    raise VerificationRequired("阿里云万网要求进行安全验证。")
                cards = self._result_cards()
                if any(
                    _is_exact_wanwang_card(normalized_domain, card)
                    for card in cards
                ):
                    exact_com_seen = True
                candidate = confirmed_wanwang_result(normalized_domain, cards)
                if candidate is not None:
                    if confirmed is not None and candidate.status == confirmed.status:
                        consecutive_confirmations += 1
                    else:
                        confirmed = candidate
                        consecutive_confirmations = 1
                    if consecutive_confirmations >= 2:
                        return candidate
                else:
                    confirmed = None
                    consecutive_confirmations = 0

                # 万网加载结果时会短暂显示“暂无内容”。只有它连续稳定显示足够久，
                # 并且本次查询从未出现过精确 .com 条目，才能判定为没有 .com 结果。
                if self._empty_result_present() and not exact_com_seen:
                    if empty_since is None:
                        empty_since = time.monotonic()
                    elif time.monotonic() - empty_since >= EMPTY_RESULT_STABLE_SECONDS:
                        return QueryResult(
                            normalized_domain,
                            STATUS_NO_COM,
                            "",
                            False,
                            False,
                            f"万网连续 {EMPTY_RESULT_STABLE_SECONDS:g} 秒明确显示暂无搜索内容",
                        )
                else:
                    empty_since = None
                self.page.wait_for_timeout(500)
            else:
                raise TransientPageError(
                    "万网尚未给出精确 .com 的明确注册状态；将刷新并重试当前域名。"
                )
        except self._timeout_error as exc:
            if self.verification_present():
                raise VerificationRequired("阿里云万网要求进行安全验证。") from exc
            raise TransientPageError("万网页面加载超时。") from exc
        except (VerificationRequired, TransientPageError, PageStructureChanged):
            raise
        except Exception as exc:
            raise TransientPageError(f"万网查询页面暂时加载失败：{exc}") from exc

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
