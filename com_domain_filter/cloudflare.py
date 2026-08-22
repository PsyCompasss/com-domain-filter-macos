from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


STATUS_EXACT_AVAILABLE = "exact_available"
STATUS_AVAILABLE_MISMATCH = "available_mismatch"
STATUS_EXACT_UNAVAILABLE = "exact_unavailable"
STATUS_NO_COM = "no_com"


class CloudflareError(RuntimeError):
    pass


class VerificationRequired(CloudflareError):
    pass


class TransientPageError(CloudflareError):
    """普通网络或页面加载故障，可以自动刷新后继续。"""


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


def chrome_app_bundle(executable: Path) -> Path | None:
    return next((parent for parent in executable.parents if parent.suffix == ".app"), None)


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
        self._chrome_pid: int | None = None
        self._chrome_log = None
        session_dir = self.profile_dir.parent / ".browser-sessions"
        self._pid_file = session_dir / f"{self.profile_dir.name}.pid"
        self._port_file = session_dir / f"{self.profile_dir.name}.port"
        self._reused_existing = False

    def start(
        self,
        allow_launch: bool = True,
        preferred_page_url: str | None = None,
        wait_seconds: float = 90,
    ) -> None:
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
        existing = self._find_existing_chrome()
        if not existing:
            pending = self._find_pending_chrome()
            if pending:
                # Chrome 窗口可能已经出现，但调试端口在机器繁忙时会晚几十秒才开放。
                # 后续重试必须继续等待这个进程原来分配的端口，不能另选新端口；
                # 否则 Chrome 会把新请求转交给旧进程，而程序会永远等待错误端口。
                self._chrome_pid, port = pending
                self._reused_existing = True
                self._write_session_files(port)
                logging.getLogger(__name__).info(
                    "等待现有专用 Chrome 完成启动：pid=%s，port=%s",
                    self._chrome_pid,
                    port,
                )
                try:
                    self._wait_for_debug_endpoint(port, wait_seconds)
                except Exception as exc:
                    self._release_chrome_process()
                    raise TransientPageError(
                        f"专用 Chrome 正在启动，继续等待原端口 {port}：{exc}"
                    ) from exc
                existing = (self._chrome_pid, port)
        if existing:
            self._chrome_pid, port = existing
            self._reused_existing = True
            self._write_session_files(port)
            logging.getLogger(__name__).info(
                "重新连接现有专用 Chrome：pid=%s，port=%s",
                self._chrome_pid,
                port,
            )
        elif not allow_launch:
            raise CloudflareError("尚未连接专用 Chrome。请先到“运行设置”点击“打开/连接 Chrome”。")
        else:
            # Chrome 在这台 Mac 上有时需要二三十秒才真正建立调试端口。
            # 上一次启动已经把端口写入会话文件时，后续重试必须继续使用它；
            # 否则每次重试都会生成新端口，而稍后才启动完成的 Chrome 仍监听旧端口。
            port = self._recorded_free_port() or _available_local_port()
            log_path = self.profile_dir.parent / "chrome-browser.log"
            self._chrome_log = log_path.open("a", encoding="utf-8")
            app_bundle = chrome_app_bundle(chrome_path)
            if not app_bundle:
                raise CloudflareError("无法确定系统 Google Chrome 的应用程序位置。")
            command = [
                "open",
                "-na",
                str(app_bundle),
                "--args",
                f"--user-data-dir={self.profile_dir}",
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                self.site_url,
            ]
            try:
                launched = subprocess.run(
                    command,
                    stdout=self._chrome_log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=15,
                )
                if launched.returncode != 0:
                    raise CloudflareError(f"macOS 无法新建 Chrome 实例，退出码 {launched.returncode}。")
                # 这个端口是本次启动时由程序亲自分配的，直接等它开放最可靠。
                # 不能依赖 ps 命令行扫描：GUI 应用环境下长命令曾被截断，
                # 导致 Chrome 明明已经监听端口，软件仍误判为启动失败。
                self._port_file.parent.mkdir(parents=True, exist_ok=True)
                self._port_file.write_text(str(port), encoding="utf-8")
                self._wait_for_debug_endpoint(port, wait_seconds)
                self._chrome_pid = self._pid_listening_on_port(port)
                self._write_session_files(port)
                logging.getLogger(__name__).info(
                    "新建专用 Chrome 成功：pid=%s，port=%s",
                    self._chrome_pid if self._chrome_pid is not None else "暂未识别",
                    port,
                )
                self._close_chrome_log()
            except Exception as exc:
                delegated = self._wait_for_existing_chrome(timeout_seconds=5)
                if delegated:
                    self._chrome_process = None
                    self._chrome_pid, port = delegated
                    self._reused_existing = True
                    self._write_session_files(port)
                    self._close_chrome_log()
                    logging.getLogger(__name__).info(
                        "新进程已转交给现有专用 Chrome：pid=%s，port=%s",
                        self._chrome_pid,
                        port,
                    )
                else:
                    pending = self._find_pending_chrome(preferred_port=port)
                    if pending:
                        # 启动超时不等于 Chrome 启动失败。保留已经出现的专用进程、
                        # PID 和原端口，交给下一次自动重试继续等待。
                        self._chrome_pid, port = pending
                        self._write_session_files(port)
                        self._release_chrome_process()
                        raise TransientPageError(
                            f"专用 Chrome 启动较慢，继续等待原端口 {port}。"
                        ) from exc
                    self._release_chrome_process()
                    raise TransientPageError(f"系统 Google Chrome 暂时未就绪：{exc}") from exc
        try:
            self._ensure_page_target(port, allow_create=allow_launch)
            self._playwright = sync_playwright().start()
            # no_defaults=True 让 Playwright 保留系统 Chrome 的默认下载行为。
            # 否则部分系统 Chrome 会拒绝 Browser.setDownloadBehavior，造成
            # “Browser context management is not supported”并在已经打开后仍误报失败。
            self.browser = self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                no_defaults=True,
            )
            self.context = self.browser.contexts[0]
            site_host = urlparse(self.site_url).hostname or ""
            pages = list(self.context.pages)
            self.page = next(
                (page for page in pages if preferred_page_url and page.url == preferred_page_url),
                next((page for page in pages if site_host in page.url), pages[0] if pages else None),
            )
            if self.page is None:
                if not allow_launch:
                    raise CloudflareError("专用 Chrome 中没有可连接的网页，请重新点击“打开/连接 Chrome”。")
                self.page = self.context.new_page()
        except Exception as exc:
            if self._playwright:
                self._playwright.stop()
            self._playwright = None
            # 调试连接本身可能只是短暂中断。保留 Chrome，让 worker 自动重连，
            # 不再关闭窗口，也不弹出“无法启动 Chrome”的错误框。
            self._release_chrome_process()
            raise TransientPageError(f"系统 Google Chrome 暂时无法连接：{exc}") from exc
        try:
            current_host = urlparse(self.page.url).hostname or ""
            site_host = urlparse(self.site_url).hostname or ""
            if current_host != site_host:
                if not allow_launch:
                    raise CloudflareError("已选网页不是当前查询网站，请重新选择网页并连接。")
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
            raise TransientPageError(f"{self.site_name}页面暂时无法打开：{exc}") from exc

    def _wait_for_debug_endpoint(self, port: int, wait_seconds: float = 90) -> None:
        deadline = time.monotonic() + max(1.0, wait_seconds)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._chrome_process and self._chrome_process.poll() is not None:
                raise CloudflareError("Google Chrome 启动后立即退出。")
            try:
                if self._debug_endpoint_available(port):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(1.0)
        raise CloudflareError(f"等待 Google Chrome 启动超时：{last_error or '未知原因'}")

    @staticmethod
    def _debug_endpoint_available(port: int) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("webSocketDebuggerUrl")) and "Chrome" in str(payload.get("Browser", ""))
        except Exception:
            return False

    def _recorded_free_port(self) -> int | None:
        """复用仍空闲的会话端口，让慢启动 Chrome 与后续重试始终对准同一端口。"""
        try:
            port = int(self._port_file.read_text(encoding="utf-8").strip())
            if not 1 <= port <= 65535:
                return None
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", port))
            logging.getLogger(__name__).info("沿用上次启动端口：port=%s", port)
            return port
        except Exception:
            return None

    def _ensure_page_target(self, port: int, allow_create: bool = True) -> None:
        """Chrome 进程还在但所有窗口都已关掉时，先重新创建网页标签。"""
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
                targets = json.loads(response.read().decode("utf-8"))
            if any(item.get("type") == "page" for item in targets):
                return
            if not allow_create:
                raise CloudflareError("专用 Chrome 中没有网页，请先点击“打开/连接 Chrome”。")
            encoded_url = quote(self.site_url, safe="")
            request = Request(
                f"http://127.0.0.1:{port}/json/new?{encoded_url}",
                method="PUT",
            )
            with urlopen(request, timeout=5) as response:
                created = json.loads(response.read().decode("utf-8"))
            if created.get("type") != "page":
                raise ValueError("Chrome 没有返回新网页标签")
            logging.getLogger(__name__).info("专用 Chrome 没有网页窗口，已自动重新打开查询页")
        except Exception as exc:
            raise TransientPageError(f"专用 Chrome 暂时无法重新打开查询页：{exc}") from exc

    def _matching_chrome_processes(self) -> list[tuple[int, str]]:
        try:
            output = subprocess.run(
                ["/bin/ps", "-axww", "-o", "pid=,command="],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            return []
        marker = f"--user-data-dir={self.profile_dir}"
        matches = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            pid_text, _, command = stripped.partition(" ")
            if (
                pid_text.isdigit()
                and marker in command
                and "Google Chrome" in command
                and "--type=" not in command
            ):
                matches.append((int(pid_text), command))
        return matches

    def _find_pending_chrome(self, preferred_port: int | None = None) -> tuple[int, int] | None:
        """找到已经启动但调试端口尚未开放的专用 Chrome。"""
        matches = self._matching_chrome_processes()
        if not matches:
            return None

        recorded_port = preferred_port
        if recorded_port is None:
            try:
                recorded_port = int(self._port_file.read_text(encoding="utf-8").strip())
            except Exception:
                recorded_port = None

        candidates: list[tuple[int, int]] = []
        for pid, command in matches:
            port_match = re.search(r"--remote-debugging-port=(\d+)", command)
            if not port_match:
                continue
            port = int(port_match.group(1))
            if recorded_port is not None and port == recorded_port:
                return pid, port
            candidates.append((pid, port))
        return candidates[0] if candidates else None

    def _pid_listening_on_port(self, port: int) -> int | None:
        """用监听端口反查 Chrome 主进程，避免长命令行被截断后无法识别。"""
        try:
            output = subprocess.run(
                ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            output = ""
        for pid_text in output.splitlines():
            if not pid_text.strip().isdigit():
                continue
            pid = int(pid_text.strip())
            try:
                command = subprocess.run(
                    ["/bin/ps", "-p", str(pid), "-o", "command="],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                ).stdout
            except Exception:
                continue
            if str(self.profile_dir) in command and "Google Chrome" in command and "--type=" not in command:
                return pid
        for pid, command in self._matching_chrome_processes():
            if f"--remote-debugging-port={port}" in command:
                return pid
        return None

    def _find_existing_chrome(self) -> tuple[int | None, int] | None:
        try:
            recorded_port = int(self._port_file.read_text(encoding="utf-8").strip())
            if self._debug_endpoint_available(recorded_port):
                listening_pid = self._pid_listening_on_port(recorded_port)
                if listening_pid is None:
                    try:
                        recorded_pid = int(self._pid_file.read_text(encoding="utf-8").strip())
                        command = subprocess.run(
                            ["ps", "-p", str(recorded_pid), "-o", "command="],
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=3,
                        ).stdout
                        if str(self.profile_dir) in command and "Google Chrome" in command:
                            listening_pid = recorded_pid
                    except Exception:
                        pass
                # 端口文件由本软件创建。调试端点已经响应时，即使 macOS 暂时
                # 无法通过 ps/lsof 返回 PID，也可以直接连接；PID 只用于唤醒窗口，
                # 不应成为浏览器连接的前置条件。
                return listening_pid, recorded_port
        except Exception:
            pass
        try:
            recorded_pid = int(self._pid_file.read_text(encoding="utf-8").strip())
            recorded_port = int(self._port_file.read_text(encoding="utf-8").strip())
            if self._debug_endpoint_available(recorded_port):
                command = subprocess.run(
                    ["ps", "-p", str(recorded_pid), "-o", "command="],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                ).stdout
                if str(self.profile_dir) in command and "Google Chrome" in command:
                    return recorded_pid, recorded_port
        except Exception:
            pass
        for pid, command in self._matching_chrome_processes():
            port_match = re.search(r"--remote-debugging-port=(\d+)", command)
            if not port_match:
                continue
            port = int(port_match.group(1))
            if self._debug_endpoint_available(port):
                return pid, port
        return None

    def _wait_for_existing_chrome(self, timeout_seconds: float) -> tuple[int | None, int] | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            existing = self._find_existing_chrome()
            if existing:
                return existing
            time.sleep(0.2)
        return None

    def _write_session_files(self, port: int) -> None:
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)
        if self._chrome_pid is not None:
            self._pid_file.write_text(str(self._chrome_pid), encoding="utf-8")
        else:
            self._pid_file.unlink(missing_ok=True)
        self._port_file.write_text(str(port), encoding="utf-8")

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

    def recover_page(self) -> None:
        if not self.context:
            raise TransientPageError("浏览器连接暂时不可用。")
        try:
            if not self.page or self.page.is_closed():
                self.page = self.context.new_page()
            current_url = self.page.url
            target_url = current_url if current_url.startswith(("http://", "https://")) else self.site_url
            try:
                self.page.reload(wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                self.page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            self.page.wait_for_timeout(3_000)
        except Exception as exc:
            raise TransientPageError(f"自动刷新暂时失败：{exc}") from exc

    def page_options(self) -> list[dict[str, str]]:
        """返回当前专用 Chrome 的网页，供用户确认软件应连接哪一个。"""
        if not self.context:
            return []
        options: list[dict[str, str]] = []
        for page in self.context.pages:
            try:
                title = page.title().strip() or "无标题网页"
            except Exception:
                title = "无标题网页"
            options.append({"title": title, "url": page.url})
        return options

    def close(self, keep_browser: bool = False) -> None:
        """只断开自动化连接，永不关闭、缩小或移动 Chrome。"""
        try:
            pass
        finally:
            self.browser = None
            self.context = None
            self.page = None
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
            self._release_chrome_process()

    def _release_chrome_process(self) -> None:
        """断开自动化连接，但把用户要求保留的专用 Chrome 留在前台。"""
        self._chrome_process = None
        self._close_chrome_log()

    def _close_chrome_log(self) -> None:
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
                if self.verification_present():
                    raise VerificationRequired("Cloudflare要求进行安全验证。")
                raise TransientPageError("Cloudflare搜索按钮暂时不可用。")
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
                    raise TransientPageError("Cloudflare查询结果暂时没有加载出来。")
                response = responses[-1]
            finally:
                self.page.remove_listener("response", capture_response)
            if response.status in (403, 429):
                raise VerificationRequired("Cloudflare要求进行验证或暂时限制了查询。")
            if response.status != 200:
                raise TransientPageError(f"Cloudflare查询暂时返回HTTP {response.status}。")
            payload = response.json()
        except self._timeout_error as exc:
            if self.verification_present():
                raise VerificationRequired("Cloudflare要求进行安全验证。") from exc
            raise TransientPageError("Cloudflare页面加载超时。") from exc
        except VerificationRequired:
            raise
        except TransientPageError:
            raise
        except PageStructureChanged:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "turnstile" in message or "challenge" in message:
                raise VerificationRequired("Cloudflare验证尚未完成。") from exc
            raise TransientPageError(f"Cloudflare查询页面暂时加载失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise CloudflareError("Cloudflare返回了无法识别的数据。")
        return classify_response(normalized_domain, payload)

    def wait_for_verification(self, stop_event: Event, timeout_seconds: int = 600) -> bool:
        if not self.page:
            return False
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
