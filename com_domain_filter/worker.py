from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .cloudflare import (
    STATUS_EXACT_AVAILABLE,
    CloudflareError,
    PageStructureChanged,
    TransientPageError,
    VerificationRequired,
)
from .excel_store import ExcelStore, ExcelStoreError
from .patterns import BIND_INDEPENDENT, BlockPatternGenerator, PatternGenerator
from .storage import HistoryStore
from .sites import create_checker


@dataclass(frozen=True)
class RunConfig:
    site_url: str
    characters: tuple[str, ...]
    patterns: tuple[str, ...]
    prefix: str
    suffix: str
    unlimited_length: int
    interval_seconds: float
    retry_interval_seconds: float
    limit_tests_enabled: bool
    limit_tests: int
    limit_found_enabled: bool
    limit_found: int
    run_until_stopped: bool
    excel_path: Path
    profile_dir: Path
    blocks: tuple[dict, ...] = ()
    binding_mode: str = BIND_INDEPENDENT
    preferred_page_url: str = ""


class SearchWorker:
    def __init__(
        self,
        config: RunConfig,
        history: HistoryStore,
        emit: Callable[[str, dict], None],
        checker_factory=create_checker,
    ) -> None:
        self.config = config
        self.history = history
        self.emit = emit
        self.checker_factory = checker_factory
        self.stop_event = threading.Event()
        self.run_gate = threading.Event()
        self.run_gate.set()
        self.thread: threading.Thread | None = None
        self.checked = 0
        self.found = 0
        self.keep_browser_open_after_stop = True
        self._generation_lock = threading.Lock()
        self._pending_generation: tuple[tuple[str, ...], tuple[dict, ...], str] | None = None

    @property
    def is_alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        if self.is_alive:
            return
        self.thread = threading.Thread(target=self._run, name="domain-search", daemon=True)
        self.thread.start()

    def pause(self) -> None:
        self.run_gate.clear()
        self.emit("status", {"message": "已暂停"})

    def resume(self) -> None:
        self.run_gate.set()
        self.emit("status", {"message": "正在查询"})

    def update_rules(
        self,
        characters: tuple[str, ...],
        blocks: tuple[dict, ...],
        binding_mode: str,
    ) -> None:
        # 先构造一次，确保错误规则不会在后台线程里才爆出。
        BlockPatternGenerator(characters, blocks, binding_mode)
        with self._generation_lock:
            self._pending_generation = (characters, blocks, binding_mode)

    def _take_pending_generation(self):
        with self._generation_lock:
            pending = self._pending_generation
            self._pending_generation = None
        if pending is None:
            return None
        characters, blocks, binding_mode = pending
        return BlockPatternGenerator(characters, blocks, binding_mode), "", ""

    def stop(self, keep_browser_open: bool = True) -> None:
        # 停止查询不等于关闭 Chrome。保留参数仅为兼容旧调用方。
        self.keep_browser_open_after_stop = True
        self.stop_event.set()
        self.run_gate.set()

    def _stop_reason(self) -> str | None:
        if self.config.limit_tests_enabled and self.checked >= self.config.limit_tests:
            return f"已完成指定的 {self.config.limit_tests} 次检测"
        if self.config.limit_found_enabled and self.found >= self.config.limit_found:
            return f"已找到指定的 {self.config.limit_found} 个可注册域名"
        return None

    def _emit_manual_stop(self) -> None:
        message = "已手动停止"
        if self.keep_browser_open_after_stop:
            message += "；Chrome浏览器保持打开"
        self.emit("finished", {"message": message, "checked": self.checked, "found": self.found})

    def _next_untested(self, generator):
        for _ in range(2000):
            item = generator.generate()
            if not self.history.has_tested(item.domain):
                return item
        return None

    def _run(self) -> None:
        checker = None
        try:
            if self.config.blocks:
                generator = BlockPatternGenerator(
                    self.config.characters,
                    self.config.blocks,
                    self.config.binding_mode,
                )
                metadata_prefix = ""
                metadata_suffix = ""
            else:
                generator = PatternGenerator(
                    self.config.characters,
                    self.config.patterns,
                    self.config.prefix,
                    self.config.suffix,
                    self.config.unlimited_length,
                )
                metadata_prefix = generator.prefix
                metadata_suffix = generator.suffix
            excel = ExcelStore(self.config.excel_path)
            excel.sync_found_rows(self.history.found_rows(), self.config.site_url)
            checker = self.checker_factory(self.config.site_url, self.config.profile_dir)
            self.emit("status", {"message": "正在连接已经打开的 Chrome…"})
            startup_failures = 0
            while not self.stop_event.is_set():
                try:
                    try:
                        checker.start(
                            allow_launch=False,
                            preferred_page_url=self.config.preferred_page_url or None,
                        )
                    except TypeError as exc:
                        if "unexpected keyword" not in str(exc):
                            raise
                        checker.start()
                    break
                except TransientPageError as exc:
                    startup_failures += 1
                    if startup_failures >= 3:
                        raise CloudflareError(
                            "连续3次无法连接已经打开的 Chrome。请回到运行设置重新点击“打开/连接 Chrome”。"
                        ) from exc
                    retry_seconds = self.config.retry_interval_seconds
                    message = f"Chrome连接暂时中断，{retry_seconds:g}秒后重试（第{startup_failures}次）"
                    logging.getLogger(__name__).warning("%s：%s", message, exc)
                    self.emit("status", {"message": message})
                    try:
                        checker.close(keep_browser=True)
                    except Exception:
                        pass
                    if self.stop_event.wait(retry_seconds):
                        self._emit_manual_stop()
                        return
                    checker = self.checker_factory(self.config.site_url, self.config.profile_dir)
            if self.stop_event.is_set():
                self._emit_manual_stop()
                return
            consecutive_verifications = 0
            if checker.verification_present():
                consecutive_verifications += 1
                self.emit("verification", {"message": "查询网站在首次打开时要求进行安全验证。"})
                if not checker.wait_for_verification(self.stop_event):
                    if self.stop_event.is_set():
                        self._emit_manual_stop()
                        return
                    raise CloudflareError("等待验证超过10分钟，任务已停止。")
            self.emit("status", {"message": "正在查询"})
            consecutive_page_failures = 0

            while not self.stop_event.is_set():
                self.run_gate.wait()
                if self.stop_event.is_set():
                    break
                changed = self._take_pending_generation()
                if changed is not None:
                    generator, metadata_prefix, metadata_suffix = changed
                    self.emit("status", {"message": "新规则已生效，正在按修改后的规则继续查询"})
                reason = self._stop_reason()
                if reason:
                    self.emit("finished", {"message": reason, "checked": self.checked, "found": self.found})
                    return
                item = self._next_untested(generator)
                if item is None:
                    self.emit(
                        "finished",
                        {"message": "连续生成的域名都已检测过，当前组合可能已经用尽。", "checked": self.checked, "found": self.found},
                    )
                    return

                # 必须在网页查询前写入去重数据库。否则用户在查询返回前后点击停止，
                # 或软件意外退出时，这个已经发给网站的域名会在下次运行时再次出现。
                started_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if not self.history.reserve(
                    item.domain,
                    started_at,
                    item.pattern,
                    metadata_prefix,
                    metadata_suffix,
                    site=self.config.site_url,
                ):
                    continue

                result = None
                skipped_after_failures = False
                while not self.stop_event.is_set():
                    try:
                        self.emit("current", {"domain": item.domain, "pattern": item.pattern})
                        result = checker.query(item.domain)
                        consecutive_page_failures = 0
                        break
                    except TransientPageError as exc:
                        consecutive_page_failures += 1
                        if consecutive_page_failures >= 3:
                            failed_at = datetime.now().astimezone().isoformat(timespec="seconds")
                            self.history.finalize(
                                item.domain,
                                "query_failed",
                                failed_at,
                                item.pattern,
                                metadata_prefix,
                                metadata_suffix,
                                str(exc),
                                self.config.site_url,
                            )
                            self.checked += 1
                            self.emit(
                                "status",
                                {"message": f"{item.domain} 连续3次未能确认查询结果，已记录失败并已跳过，继续下一个"},
                            )
                            self.emit(
                                "progress",
                                {"checked": self.checked, "found": self.found, "last_status": "query_failed"},
                            )
                            consecutive_page_failures = 0
                            skipped_after_failures = True
                            break
                        retry_seconds = self.config.retry_interval_seconds
                        message = (
                            f"查询结果暂时无法确认，{retry_seconds:g}秒后自动刷新重试"
                            f"（第{consecutive_page_failures}次）"
                        )
                        logging.getLogger(__name__).warning("%s：%s", message, exc)
                        self.emit("status", {"message": message})
                        if self.stop_event.wait(retry_seconds):
                            break
                        try:
                            checker.recover_page()
                        except TransientPageError as recovery_exc:
                            logging.getLogger(__name__).warning("自动刷新暂时失败：%s", recovery_exc)
                        continue
                    except VerificationRequired as exc:
                        consecutive_verifications += 1
                        if consecutive_verifications >= 2:
                            raise CloudflareError(
                                "查询网站连续要求真人验证，任务已停止。请稍后重试；如果仍然循环，请检查代理、VPN或更换网络。"
                            ) from exc
                        self.emit("verification", {"message": str(exc)})
                        if not checker.wait_for_verification(self.stop_event):
                            if self.stop_event.is_set():
                                self._emit_manual_stop()
                                return
                            raise CloudflareError("等待验证超过10分钟，任务已停止。")
                        self.emit("status", {"message": "验证已完成，正在继续"})

                consecutive_verifications = 0

                if skipped_after_failures:
                    reason = self._stop_reason()
                    if reason:
                        self.emit("finished", {"message": reason, "checked": self.checked, "found": self.found})
                        return
                    if self.stop_event.wait(self.config.interval_seconds):
                        break
                    continue
                if result is None:
                    break
                checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if result.status == STATUS_EXACT_AVAILABLE:
                    excel.append_if_new(
                        item.domain,
                        checked_at,
                        item.pattern,
                        metadata_prefix,
                        metadata_suffix,
                        self.config.site_url,
                    )
                    self.found += 1
                    self.emit(
                        "found",
                        {"domain": item.domain, "pattern": item.pattern, "checked_at": checked_at, "found": self.found},
                    )
                self.history.finalize(
                    item.domain,
                    result.status,
                    checked_at,
                    item.pattern,
                    metadata_prefix,
                    metadata_suffix,
                    json.dumps({"returned_name": result.returned_name}, ensure_ascii=False),
                    self.config.site_url,
                )
                self.checked += 1
                self.emit(
                    "progress",
                    {"checked": self.checked, "found": self.found, "last_status": result.status},
                )
                if self.stop_event.is_set():
                    break
                reason = self._stop_reason()
                if reason:
                    self.emit("finished", {"message": reason, "checked": self.checked, "found": self.found})
                    return
                if self.stop_event.wait(self.config.interval_seconds):
                    break
            self._emit_manual_stop()
        except (CloudflareError, PageStructureChanged, ExcelStoreError, Exception) as exc:
            if logging.getLogger().handlers:
                logging.getLogger(__name__).exception("查询任务停止")
            self.emit("error", {"message": str(exc), "checked": self.checked, "found": self.found})
        finally:
            if checker:
                try:
                    checker.close(keep_browser=True)
                except Exception:
                    pass
