from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlparse


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


def find_system_chrome() -> Path | None:
    override = os.environ.get("COM_DOMAIN_FILTER_CHROME_PATH")
    if override:
        override_path = Path(override).expanduser()
        if override_path.is_file() and os.access(override_path, os.X_OK):
            return override_path
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    try:
        result = subprocess.run(
            ["osascript", "-e", 'POSIX path of (path to application id "com.google.Chrome")'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout.strip():
            candidates.append(Path(result.stdout.strip()) / "Contents/MacOS/Google Chrome")
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemCFBundleIdentifier == 'com.google.Chrome'"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            app_path = Path(line.strip())
            if app_path.suffix == ".app":
                candidates.append(app_path / "Contents/MacOS/Google Chrome")
    except Exception:
        pass
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class CloudflareChecker:
    site_name = "Cloudflare"

    def __init__(self, site_url: str, profile_dir: Path) -> None:
        self.site_url = site_url.rstrip("/") + "/"
        self.profile_dir = Path(profile_dir)
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._chrome_process: subprocess.Popen | None = None
        self._chrome_log = None
        self._pid_file = self.profile_dir.parent / "chrome-browser.pid"
        self._cdp_session = None
        self._window_id: int | None = None

    def start(self) -> None:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CloudflareError("缺少网页自动化组件，请重新安装软件。") from exc
        self._timeout_error = PlaywrightTimeoutError
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        chrome_path = find_system_chrome()
        if not chrome_path:
            raise CloudflareError("没有找到系统 Google Chrome，请先安装 Chrome 后再查询。")
        logging.getLogger(__name__).info("使用系统 Google Chrome：%s", chrome_path)
        self._stop_stale_chrome()
        port = _available_local_port()
        log_path = self.profile_dir.parent / "chrome-browser.log"
        self._chrome_log = log_path.open("a", encoding="utf-8")
        command = [
            str(chrome_path),
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            self.site_url,
        ]
        try:
            self._chrome_process = subprocess.Popen(
                command,
                stdout=self._chrome_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._pid_file.write_text(str(self._chrome_process.pid), encoding="utf-8")
            self._wait_for_debug_endpoint(port)
        except Exception as exc:
            self._close_chrome_process()
            raise CloudflareError(f"无法启动系统 Google Chrome：{exc}") from exc
        try:
            self._playwright = sync_playwright().start()
            self.browser = self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            self.context = self.browser.contexts[0]
            site_host = urlparse(self.site_url).hostname or ""
            self.page = next(
                (page for page in self.context.pages if site_host in page.url),
                self.context.pages[0] if self.context.pages else self.context.new_page(),
            )
            self._cdp_session = self.context.new_cdp_session(self.page)
            window = self._cdp_session.send("Browser.getWindowForTarget")
            self._window_id = int(window["windowId"])
        except Exception as exc:
            if self._playwright:
                self._playwright.stop()
            self._playwright = None
            self._close_chrome_process()
            raise CloudflareError(f"无法连接系统 Google Chrome：{exc}") from exc
        try:
            if self.page.url.rstrip("/") != self.site_url.rstrip("/"):
                self.page.goto(self.site_url, wait_until="domcontentloaded", timeout=60_000)
            self.page.wait_for_load_state("load", timeout=60_000)
            # Cloudflare 的搜索表单需要等客户端脚本接管；过早点击会退化为普通页面跳转，
            # 不会调用 /api/search。
            self.page.wait_for_timeout(3_000)
            logging.getLogger(__name__).info(
                "系统 Chrome 已连接，webdriver=%s，页面=%s",
                self.page.evaluate("navigator.webdriver"),
                self.page.url,
            )
        except Exception as exc:
            raise CloudflareError(f"无法打开{self.site_name}域名页面：{exc}") from exc

    def _wait_for_debug_endpoint(self, port: int) -> None:
        endpoint = f"http://127.0.0.1:{port}/json/version"
        deadline = time.monotonic() + 20
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._chrome_process and self._chrome_process.poll() is not None:
                raise CloudflareError("Google Chrome 启动后立即退出。")
            try:
                with urllib.request.urlopen(endpoint, timeout=1) as response:
                    if response.status == 200:
                        return
            except Exception as exc:
                last_error = exc
            time.sleep(0.2)
        raise CloudflareError(f"等待 Google Chrome 启动超时：{last_error or '未知原因'}")

    def _stop_stale_chrome(self) -> None:
        if not self._pid_file.exists():
            return
        try:
            pid = int(self._pid_file.read_text(encoding="utf-8").strip())
            command = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            if str(self.profile_dir) in command and "Google Chrome" in command:
                os.kill(pid, 15)
                for _ in range(20):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
        except Exception:
            pass
        finally:
            self._pid_file.unlink(missing_ok=True)

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
        if self._cdp_session and self._window_id is not None:
            try:
                self._cdp_session.send(
                    "Browser.setWindowBounds",
                    {"windowId": self._window_id, "bounds": {"windowState": "minimized"}},
                )
            except Exception:
                pass

    def show_browser(self) -> None:
        if self._cdp_session and self._window_id is not None:
            try:
                self._cdp_session.send(
                    "Browser.setWindowBounds",
                    {"windowId": self._window_id, "bounds": {"windowState": "normal"}},
                )
            except Exception:
                pass
        if self.page:
            try:
                self.page.bring_to_front()
            except Exception:
                pass
        if self._chrome_process and self._chrome_process.poll() is None:
            self._run_applescript(
                'tell application "System Events" to set frontmost of first process whose unix id is '
                + str(self._chrome_process.pid)
                + " to true"
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
            if self._cdp_session:
                try:
                    self._cdp_session.detach()
                except Exception:
                    pass
            if self.browser:
                self.browser.close()
        finally:
            self.browser = None
            self.context = None
            self.page = None
            self._cdp_session = None
            self._window_id = None
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
            self._close_chrome_process()

    def _close_chrome_process(self) -> None:
        process = self._chrome_process
        self._chrome_process = None
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                logging.getLogger(__name__).warning("无法正常关闭专用 Chrome 进程", exc_info=True)
        self._pid_file.unlink(missing_ok=True)
        if self._chrome_log:
            try:
                self._chrome_log.close()
            except Exception:
                pass
            self._chrome_log = None

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
                    return True
            except Exception:
                pass
            stop_event.wait(1.0)
        return False
