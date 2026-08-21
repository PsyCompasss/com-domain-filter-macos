from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any


STATUS_EXACT_AVAILABLE = "exact_available"
STATUS_AVAILABLE_MISMATCH = "available_mismatch"
STATUS_EXACT_UNAVAILABLE = "exact_unavailable"
STATUS_NO_COM = "no_com"


class CloudflareError(RuntimeError):
    pass


class VerificationRequired(CloudflareError):
    pass


class PageStructureChanged(CloudflareError):
    pass


@dataclass(frozen=True)
class QueryResult:
    query: str
    status: str
    returned_name: str
    available: bool
    can_register: bool
    detail: str = ""


def classify_response(query: str, payload: dict[str, Any]) -> QueryResult:
    normalized = query.strip().lower()
    check = payload.get("check_result") or {}
    returned_name = str(check.get("name") or "").lower()
    available = bool(check.get("available"))
    can_register = bool(check.get("can_register"))

    if returned_name == normalized:
        if available and can_register:
            return QueryResult(normalized, STATUS_EXACT_AVAILABLE, returned_name, True, True)
        return QueryResult(normalized, STATUS_EXACT_UNAVAILABLE, returned_name, available, can_register)

    com_domains = [
        item for item in (payload.get("domains") or [])
        if str(item.get("name") or "").lower().endswith(".com")
    ]
    available_com = [item for item in com_domains if item.get("availability") == "available"]
    if available_com:
        first_name = str(available_com[0].get("name") or "").lower()
        return QueryResult(normalized, STATUS_AVAILABLE_MISMATCH, first_name, True, True)
    return QueryResult(normalized, STATUS_NO_COM, returned_name, False, False)


def configure_bundled_browser_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        candidates.extend(
            [
                executable.parent.parent / "Resources" / "playwright-browsers",
                Path(getattr(sys, "_MEIPASS", executable.parent)) / "playwright-browsers",
            ]
        )
    project_bundle = Path(__file__).resolve().parents[1] / "playwright-browsers"
    candidates.append(project_bundle)
    for candidate in candidates:
        if candidate.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
            return


class CloudflareChecker:
    def __init__(self, site_url: str, profile_dir: Path) -> None:
        self.site_url = site_url.rstrip("/") + "/"
        self.profile_dir = Path(profile_dir)
        self._playwright = None
        self.context = None
        self.page = None

    def start(self) -> None:
        configure_bundled_browser_path()
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CloudflareError("缺少网页自动化组件，请重新安装软件。") from exc
        self._timeout_error = PlaywrightTimeoutError
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                locale="zh-CN",
                viewport={"width": 1280, "height": 850},
                args=["--disable-background-timer-throttling"],
            )
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            raise CloudflareError(f"无法启动后台浏览器：{exc}") from exc
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        try:
            self.page.goto(self.site_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            raise CloudflareError(f"无法打开Cloudflare域名页面：{exc}") from exc
        if not self.verification_present():
            self.minimize_browser()

    @staticmethod
    def _run_applescript(script: str) -> None:
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
            )
        except Exception:
            pass

    def minimize_browser(self) -> None:
        self._run_applescript('tell application "Chromium" to set miniaturized of every window to true')

    def show_browser(self) -> None:
        self._run_applescript(
            'tell application "Chromium"\nactivate\nset miniaturized of every window to false\nend tell'
        )

    def verification_present(self) -> bool:
        if not self.page:
            return False
        try:
            title = self.page.title().lower()
            if "just a moment" in title or "请稍候" in title or "security verification" in title:
                return True
            body_text = self.page.locator("body").inner_text(timeout=2_000).lower()
            markers = (
                "verify you are human",
                "正在验证您是否是真人",
                "安全验证",
                "请稍候",
                "oops. something went wrong",
                "unexpected error has occurred",
            )
            return any(marker in body_text for marker in markers)
        except Exception:
            return False

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            self.context = None
            self.page = None
            if self._playwright:
                self._playwright.stop()
                self._playwright = None

    def _search_box(self):
        candidates = [
            self.page.get_by_role("textbox", name=re.compile(r"Search for a domain name|搜索域名", re.I)),
            self.page.locator('input[type="search"]'),
            self.page.locator('input[type="text"]'),
        ]
        for candidate in candidates:
            if candidate.count() and candidate.first.is_visible():
                return candidate.first
        raise PageStructureChanged("找不到域名搜索输入框，Cloudflare页面结构可能已经变化。")

    def _search_button(self):
        candidates = [
            self.page.get_by_role("button", name=re.compile(r"Search|搜索", re.I)),
            self.page.locator('button[type="submit"]'),
        ]
        for candidate in candidates:
            if candidate.count() and candidate.first.is_visible():
                return candidate.first
        raise PageStructureChanged("找不到搜索按钮，Cloudflare页面结构可能已经变化。")

    def query(self, domain: str) -> QueryResult:
        if not self.page:
            raise CloudflareError("浏览器尚未启动。")
        normalized_domain = domain.strip().lower()
        search_term = normalized_domain[:-4] if normalized_domain.endswith(".com") else normalized_domain
        box = self._search_box()
        button = self._search_button()
        try:
            button.wait_for(state="visible", timeout=20_000)
            ready_deadline = time.monotonic() + 30
            while not button.is_enabled() and time.monotonic() < ready_deadline:
                if self.verification_present():
                    raise VerificationRequired("Cloudflare要求进行安全验证。")
                self.page.wait_for_timeout(500)
            if not button.is_enabled():
                raise VerificationRequired("搜索按钮长时间不可用，可能需要完成Cloudflare验证。")
            responses = []

            def capture_response(response):
                if response.url.rstrip("/").endswith("/api/search") and response.request.method == "POST":
                    responses.append(response)

            self.page.on("response", capture_response)
            try:
                box.fill(search_term)
                button.click(timeout=30_000)
                response_deadline = time.monotonic() + 60
                while not responses and time.monotonic() < response_deadline:
                    if self.verification_present():
                        raise VerificationRequired(
                            "Cloudflare未接受本次自动查询。请在浏览器中完成验证，或刷新页面后再继续。"
                        )
                    self.page.wait_for_timeout(500)
                if not responses:
                    raise VerificationRequired("等待查询结果超时，可能需要完成Cloudflare验证。")
                response = responses[-1]
            finally:
                self.page.remove_listener("response", capture_response)
            if response.status in (403, 429):
                raise VerificationRequired("Cloudflare要求进行验证或暂时限制了查询。")
            if response.status != 200:
                raise CloudflareError(f"Cloudflare查询返回HTTP {response.status}。")
            payload = response.json()
            self.minimize_browser()
        except self._timeout_error as exc:
            raise VerificationRequired("等待查询结果超时，可能需要完成Cloudflare验证。") from exc
        except VerificationRequired:
            raise
        except PageStructureChanged:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "turnstile" in message or "challenge" in message or "timeout" in message:
                raise VerificationRequired("Cloudflare验证尚未完成。") from exc
            raise CloudflareError(f"查询失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise CloudflareError("Cloudflare返回了无法识别的数据。")
        return classify_response(normalized_domain, payload)

    def wait_for_verification(self, stop_event: Event, timeout_seconds: int = 600) -> bool:
        if not self.page:
            return False
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        self.show_browser()
        deadline = time.monotonic() + timeout_seconds
        while not stop_event.is_set() and time.monotonic() < deadline:
            try:
                if self.verification_present():
                    stop_event.wait(1.0)
                    continue
                box = self._search_box()
                button = self._search_button()
                if box.is_visible() and button.is_visible() and button.is_enabled():
                    self.minimize_browser()
                    return True
            except Exception:
                pass
            stop_event.wait(1.0)
        return False
