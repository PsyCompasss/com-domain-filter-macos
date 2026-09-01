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
from .tlds import ascii_domain, ascii_tld, unicode_domain, unicode_tld


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
GROUP_RESULT_STABLE_SECONDS = 8.0
INITIAL_RESULT_SETTLE_SECONDS = 5.0
AFTER_SCROLL_SETTLE_SECONDS = 3.0
MAX_GROUP_SCROLLS = 12
_ASCII_DOMAIN_TOKEN = re.compile(
    r"(?<![\w-])(?:[a-z0-9-]+\.)+[a-z0-9-]+(?![\w-])",
    re.IGNORECASE,
)


def _wanwang_unavailable(text: str) -> bool:
    return any(marker in text for marker in WANWANG_UNAVAILABLE_MARKERS)


def _split_query(query: str) -> tuple[str, str, str]:
    normalized_domain = unicode_domain(query)
    if "." in normalized_domain:
        search_term, raw_suffix = normalized_domain.rsplit(".", 1)
        target_suffix = unicode_tld(f".{raw_suffix}")
    else:
        search_term = normalized_domain
        target_suffix = ".com"
        normalized_domain = f"{normalized_domain}{target_suffix}"
    return normalized_domain, search_term, target_suffix


def _is_exact_wanwang_card(query: str, card: dict) -> bool:
    """判断卡片是否对应用户查询的完整目标后缀域名。

    万网会把“已注册”等状态标签放进域名标题本身。即使页面提取出的
    ``name`` 被这些标签污染，也可以用卡片正文中的完整域名作可靠兜底。
    边界判断可避免把 ``myabc.com`` 误当成 ``abc.com``。
    """
    normalized_domain, search_term, target_suffix = _split_query(query)
    suffix = unicode_tld(str(card.get("suffix", "")))
    if ascii_tld(suffix) != ascii_tld(target_suffix):
        return False
    name = unicode_domain(str(card.get("name", "")))
    if name == search_term:
        return True
    text = str(card.get("text", "")).lower()
    candidates = tuple(dict.fromkeys((unicode_domain(normalized_domain), ascii_domain(normalized_domain))))
    return any(
        re.search(rf"(?<![\w-]){re.escape(candidate)}(?![\w-])", text) is not None
        for candidate in candidates
    )


def _exact_wanwang_unavailable(query: str, card: dict) -> bool:
    """只把紧跟在目标域名后的不可注册标记归给目标域名。

    万网有时会把 ``myqcq.com（推荐、立即注册）`` 和
    ``qcq.com（已注册）`` 合并进同一个标题及结果行。不能因为这一行里
    存在推荐域名的注册按钮，就把目标域名判成可注册。
    """
    normalized_domain, search_term, _target_suffix = _split_query(query)
    candidates = tuple(
        dict.fromkeys((unicode_domain(normalized_domain), ascii_domain(normalized_domain)))
    )
    saw_target_in_text = False
    for raw_text in (card.get("headingText", ""), card.get("text", "")):
        text = str(raw_text).lower()
        for candidate in candidates:
            match = re.search(
                rf"(?<![\w-]){re.escape(candidate)}(?![\w-])",
                text,
            )
            if match is None:
                continue
            saw_target_in_text = True
            tail = text[match.end():]
            next_domain = _ASCII_DOMAIN_TOKEN.search(tail)
            if next_domain is not None:
                tail = tail[:next_domain.start()]
            if _wanwang_unavailable(tail[:240]):
                return True

    # 兼容旧页面：标题只提供分离后的 name/suffix，状态文字中不再重复域名。
    name = unicode_domain(str(card.get("name", "")))
    if not saw_target_in_text and name == search_term:
        return _wanwang_unavailable(str(card.get("text", "")))
    return False


def classify_wanwang_cards(query: str, cards: list[dict]) -> QueryResult:
    normalized_domain, _search_term, target_suffix = _split_query(query)
    exact = next(
        (
            card for card in cards
            if _is_exact_wanwang_card(normalized_domain, card)
        ),
        None,
    )
    if exact:
        if _exact_wanwang_unavailable(normalized_domain, exact):
            return QueryResult(normalized_domain, STATUS_EXACT_UNAVAILABLE, normalized_domain, False, False)
        if exact.get("registerHref"):
            return QueryResult(normalized_domain, STATUS_EXACT_AVAILABLE, normalized_domain, True, True)
    mismatch = next(
        (
            card for card in cards
            if not _is_exact_wanwang_card(normalized_domain, card)
            and ascii_tld(str(card.get("suffix", ""))) == ascii_tld(target_suffix)
            and card.get("registerHref")
        ),
        None,
    )
    if mismatch:
        returned = f"{mismatch['name']}{target_suffix}"
        return QueryResult(normalized_domain, STATUS_AVAILABLE_MISMATCH, returned, True, True)
    return QueryResult(normalized_domain, STATUS_NO_COM, "", False, False)


def confirmed_wanwang_result(query: str, cards: list[dict]) -> QueryResult | None:
    """只在精确的目标后缀条目已经给出明确注册状态时返回结果。"""
    normalized_domain, _search_term, target_suffix = _split_query(query)
    exact = next(
        (
            card for card in cards
            if _is_exact_wanwang_card(normalized_domain, card)
        ),
        None,
    )
    if exact is None:
        return None
    if _exact_wanwang_unavailable(normalized_domain, exact):
        return QueryResult(
            normalized_domain,
            STATUS_EXACT_UNAVAILABLE,
            normalized_domain,
            False,
            False,
            f"万网精确 {target_suffix} 条目显示已注册或正在出售",
        )
    if exact.get("registerHref"):
        return QueryResult(
            normalized_domain,
            STATUS_EXACT_AVAILABLE,
            normalized_domain,
            True,
            True,
            f"万网精确 {target_suffix} 条目显示立即注册",
        )
    return None


def confirmed_wanwang_group(
    stem: str,
    suffixes: tuple[str, ...],
    cards: list[dict],
) -> dict[str, QueryResult]:
    """从同一页卡片中提取已经明确显示注册状态的目标后缀。"""
    normalized_stem = stem.strip().lower().strip(".")
    confirmed: dict[str, QueryResult] = {}
    for suffix in suffixes:
        domain = f"{normalized_stem}{suffix}"
        result = confirmed_wanwang_result(domain, cards)
        if result is not None:
            confirmed[domain] = result
    return confirmed


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
              // mainContent 是单个结果行。外层 domain-check 可能同时容纳推荐域名
              // 和目标域名，不能从那个大区域借用“立即注册”按钮。
              const container = heading.closest('.domain-check__mainContent')
                || heading.closest('.msea-domain-homon__domain-check')
                || heading.parentElement;
              let registerHref = '';
              const text = container?.innerText || heading.innerText || '';
              const actions = Array.from(container?.querySelectorAll('a, button') || []);
              const link = actions.find((action) => {
                const href = action.getAttribute('href') || '';
                const label = (action.innerText || action.textContent || '').trim();
                return href.includes('commonbuy') || label === '立即注册';
              });
              if (link) registerHref = link.getAttribute('href') || 'action:立即注册';
              return {name, suffix, headingText, text, registerHref};
            })
            """
        )

    def _empty_result_present(self) -> bool:
        for text in ("暂无搜索内容", "暂无内容", "暂无数据"):
            locator = self.page.get_by_text(text, exact=True)
            if locator.count() and locator.first.is_visible():
                return True
        return False

    def _page_scroll_state(self) -> tuple[int, int, int]:
        try:
            state = self.page.evaluate(
                """
                () => {
                  const root = document.scrollingElement || document.documentElement;
                  return [Math.round(root.scrollTop), root.clientHeight, root.scrollHeight];
                }
                """
            )
            return int(state[0]), int(state[1]), int(state[2])
        except Exception:
            return 0, 0, 0

    def _document_ready(self) -> bool:
        """只有主文档完成加载后，才允许检查空结果或启动瀑布流滚动。"""
        try:
            return self.page.evaluate("() => document.readyState") == "complete"
        except Exception:
            return False

    def _scroll_result_page(self) -> None:
        try:
            self.page.evaluate(
                """
                () => {
                  const root = document.scrollingElement || document.documentElement;
                  const step = Math.max(Math.floor(root.clientHeight * 0.82), 420);
                  root.scrollTo({top: Math.min(root.scrollTop + step, root.scrollHeight), behavior: 'instant'});
                }
                """
            )
        except Exception as exc:
            raise TransientPageError(f"万网结果页暂时无法滚动：{exc}") from exc

    @staticmethod
    def _missing_group_results(
        stem: str,
        suffixes: tuple[str, ...],
        confirmed: dict[str, QueryResult],
        detail: str,
    ) -> dict[str, QueryResult]:
        results = dict(confirmed)
        for suffix in suffixes:
            domain = f"{stem}{suffix}"
            results.setdefault(
                domain,
                QueryResult(domain, STATUS_NO_COM, "", False, False, f"页面无 {suffix} 后缀结果；{detail}"),
            )
        return results

    def query_group(self, stem: str, suffixes: tuple[str, ...]) -> dict[str, QueryResult]:
        """搜索一次主体，通过等待和有限滚动读取全部所选后缀。

        页面确实没有某个后缀时只记录“页面无该后缀结果”，不会再对完整域名
        发起第二次查询，也不会把“未显示”误判为“已注册”。
        """
        if not self.page:
            raise CloudflareError("浏览器尚未启动。")
        normalized_stem = stem.strip().lower().strip(".")
        normalized_suffixes = tuple(dict.fromkeys(unicode_tld(item) for item in suffixes))
        url = f"{self.result_base}?keyword={quote(normalized_stem)}&suffix="
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + 60
            confirmed: dict[str, QueryResult] = {}
            candidate_state: dict[str, tuple[str, int]] = {}
            frozen_confirmed: set[str] = set()
            last_signature = None
            stable_since = time.monotonic()
            empty_since = None
            scroll_count = 0
            saw_result_cards = False
            initial_results_ready = False
            initial_ready_since = None
            last_page_snapshot = None

            while time.monotonic() < deadline:
                if self.verification_present():
                    raise VerificationRequired("阿里云万网要求进行安全验证。")
                cards = self._result_cards()
                saw_result_cards = saw_result_cards or bool(cards)
                document_ready = self._document_ready()
                scroll_top, client_height, scroll_height = self._page_scroll_state()
                signature = tuple(
                    (
                        str(card.get("name", "")),
                        str(card.get("suffix", "")),
                        str(card.get("registerHref", "")),
                        str(card.get("text", "")),
                    )
                    for card in cards
                )
                page_snapshot = (signature, scroll_height)
                if page_snapshot != last_page_snapshot:
                    last_page_snapshot = page_snapshot
                    initial_ready_since = None
                    if scroll_count == 0:
                        initial_results_ready = False
                        candidate_state.clear()
                        confirmed.clear()
                if signature != last_signature:
                    last_signature = signature
                    stable_since = time.monotonic()

                now = time.monotonic()
                stable_for = now - stable_since
                if document_ready and saw_result_cards and not initial_results_ready:
                    if initial_ready_since is None:
                        initial_ready_since = now
                    elif now - initial_ready_since >= INITIAL_RESULT_SETTLE_SECONDS:
                        initial_results_ready = True

                # 加载完成且首屏结果稳定之前不采信卡片状态。慢网速下页面会先
                # 短暂出现一部分推荐项，此时既不能滚动，也不能提前结束查询。
                current_candidates: dict[str, QueryResult] = {}
                if initial_results_ready:
                    current_candidates = confirmed_wanwang_group(
                        normalized_stem, normalized_suffixes, cards
                    )
                    for domain in tuple(candidate_state):
                        if domain not in current_candidates and domain not in frozen_confirmed:
                            candidate_state.pop(domain, None)
                            confirmed.pop(domain, None)
                    for domain, candidate in current_candidates.items():
                        previous_status, previous_count = candidate_state.get(domain, ("", 0))
                        count = previous_count + 1 if previous_status == candidate.status else 1
                        candidate_state[domain] = (candidate.status, count)
                        if count >= 2:
                            confirmed[domain] = candidate

                if (
                    initial_results_ready
                    and stable_for >= AFTER_SCROLL_SETTLE_SECONDS
                    and len(confirmed) == len(normalized_suffixes)
                ):
                    return confirmed

                if document_ready and self._empty_result_present() and not saw_result_cards:
                    if empty_since is None:
                        empty_since = now
                    elif now - empty_since >= EMPTY_RESULT_STABLE_SECONDS:
                        return self._missing_group_results(
                            normalized_stem,
                            normalized_suffixes,
                            confirmed,
                            f"万网连续 {EMPTY_RESULT_STABLE_SECONDS:g} 秒明确显示暂无搜索内容",
                        )
                else:
                    empty_since = None

                at_bottom = scroll_height > 0 and scroll_top + client_height >= scroll_height - 8
                unresolved = len(normalized_suffixes) - len(confirmed)
                unsettled_visible = any(
                    domain not in confirmed for domain in current_candidates
                )

                if (
                    unresolved
                    and initial_results_ready
                    and stable_for >= AFTER_SCROLL_SETTLE_SECONDS
                    and not unsettled_visible
                    and scroll_count < MAX_GROUP_SCROLLS
                    and not at_bottom
                ):
                    frozen_confirmed.update(confirmed)
                    self._scroll_result_page()
                    scroll_count += 1
                    stable_since = now
                    last_page_snapshot = None
                elif (
                    unresolved
                    and saw_result_cards
                    and stable_for >= GROUP_RESULT_STABLE_SECONDS
                    and (at_bottom or scroll_count >= MAX_GROUP_SCROLLS)
                ):
                    return self._missing_group_results(
                        normalized_stem,
                        normalized_suffixes,
                        confirmed,
                        f"已检查当前结果并有限滚动 {scroll_count} 次",
                    )

                self.page.wait_for_timeout(500)

            if saw_result_cards:
                return self._missing_group_results(
                    normalized_stem,
                    normalized_suffixes,
                    confirmed,
                    f"等待 60 秒并有限滚动 {scroll_count} 次后页面仍未显示",
                )
            raise TransientPageError("万网查询结果页在60秒内没有加载出任何可识别内容。")
        except self._timeout_error as exc:
            if self.verification_present():
                raise VerificationRequired("阿里云万网要求进行安全验证。") from exc
            raise TransientPageError("万网页面加载超时。") from exc
        except (VerificationRequired, TransientPageError, PageStructureChanged):
            raise
        except Exception as exc:
            raise TransientPageError(f"万网查询页面暂时加载失败：{exc}") from exc

    def query(self, domain: str) -> QueryResult:
        if not self.page:
            raise CloudflareError("浏览器尚未启动。")
        normalized_domain, search_term, target_suffix = _split_query(domain)
        url = f"{self.result_base}?keyword={quote(search_term)}&suffix={quote(target_suffix)}"
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + 60
            confirmed = None
            consecutive_confirmations = 0
            empty_since = None
            exact_suffix_seen = False
            while time.monotonic() < deadline:
                if self.verification_present():
                    raise VerificationRequired("阿里云万网要求进行安全验证。")
                cards = self._result_cards()
                if any(
                    _is_exact_wanwang_card(normalized_domain, card)
                    for card in cards
                ):
                    exact_suffix_seen = True
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
                # 并且本次查询从未出现过精确目标后缀条目，才能判定为没有目标结果。
                if self._empty_result_present() and not exact_suffix_seen:
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
                    f"万网尚未给出精确 {target_suffix} 的明确注册状态；将刷新并重试当前域名。"
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
