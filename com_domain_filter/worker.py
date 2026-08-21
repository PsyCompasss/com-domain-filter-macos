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
    CloudflareChecker,
    CloudflareError,
    PageStructureChanged,
    VerificationRequired,
)
from .excel_store import ExcelStore, ExcelStoreError
from .patterns import PatternGenerator
from .storage import HistoryStore


@dataclass(frozen=True)
class RunConfig:
    site_url: str
    characters: tuple[str, ...]
    patterns: tuple[str, ...]
    prefix: str
    suffix: str
    unlimited_length: int
    interval_seconds: float
    limit_tests_enabled: bool
    limit_tests: int
    limit_found_enabled: bool
    limit_found: int
    run_until_stopped: bool
    excel_path: Path
    profile_dir: Path


class SearchWorker:
    def __init__(
        self,
        config: RunConfig,
        history: HistoryStore,
        emit: Callable[[str, dict], None],
        checker_factory=CloudflareChecker,
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

    def stop(self) -> None:
        self.stop_event.set()
        self.run_gate.set()

    def _stop_reason(self) -> str | None:
        if self.config.limit_tests_enabled and self.checked >= self.config.limit_tests:
            return f"已完成指定的 {self.config.limit_tests} 次检测"
        if self.config.limit_found_enabled and self.found >= self.config.limit_found:
            return f"已找到指定的 {self.config.limit_found} 个可注册域名"
        return None

    def _next_untested(self, generator: PatternGenerator):
        for _ in range(2000):
            item = generator.generate()
            if not self.history.has_tested(item.domain):
                return item
        return None

    def _run(self) -> None:
        checker = None
        try:
            generator = PatternGenerator(
                self.config.characters,
                self.config.patterns,
                self.config.prefix,
                self.config.suffix,
                self.config.unlimited_length,
            )
            excel = ExcelStore(self.config.excel_path)
            excel.sync_found_rows(self.history.found_rows(), self.config.site_url)
            checker = self.checker_factory(self.config.site_url, self.config.profile_dir)
            self.emit("status", {"message": "正在启动后台浏览器…"})
            checker.start()
            consecutive_verifications = 0
            if checker.verification_present():
                consecutive_verifications += 1
                self.emit("verification", {"message": "Cloudflare在首次打开时要求进行安全验证。"})
                if not checker.wait_for_verification(self.stop_event):
                    if self.stop_event.is_set():
                        return
                    raise CloudflareError("等待验证超过10分钟，任务已停止。")
            self.emit("status", {"message": "正在查询"})

            while not self.stop_event.is_set():
                self.run_gate.wait()
                if self.stop_event.is_set():
                    break
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

                while not self.stop_event.is_set():
                    try:
                        self.emit("current", {"domain": item.domain, "pattern": item.pattern})
                        result = checker.query(item.domain)
                        break
                    except VerificationRequired as exc:
                        consecutive_verifications += 1
                        if consecutive_verifications >= 2:
                            raise CloudflareError(
                                "Cloudflare连续要求真人验证，任务已停止。请稍后重试；如果仍然循环，请检查代理、VPN或更换网络。"
                            ) from exc
                        self.emit("verification", {"message": str(exc)})
                        if not checker.wait_for_verification(self.stop_event):
                            if self.stop_event.is_set():
                                return
                            raise CloudflareError("等待验证超过10分钟，任务已停止。")
                        self.emit("status", {"message": "验证已完成，正在继续"})

                consecutive_verifications = 0

                if self.stop_event.is_set():
                    break
                checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if result.status == STATUS_EXACT_AVAILABLE:
                    excel.append_if_new(
                        item.domain,
                        checked_at,
                        item.pattern,
                        generator.prefix,
                        generator.suffix,
                        self.config.site_url,
                    )
                    self.found += 1
                    self.emit(
                        "found",
                        {"domain": item.domain, "pattern": item.pattern, "checked_at": checked_at, "found": self.found},
                    )
                self.history.record(
                    item.domain,
                    result.status,
                    checked_at,
                    item.pattern,
                    generator.prefix,
                    generator.suffix,
                    json.dumps({"returned_name": result.returned_name}, ensure_ascii=False),
                )
                self.checked += 1
                self.emit(
                    "progress",
                    {"checked": self.checked, "found": self.found, "last_status": result.status},
                )
                reason = self._stop_reason()
                if reason:
                    self.emit("finished", {"message": reason, "checked": self.checked, "found": self.found})
                    return
                if self.stop_event.wait(self.config.interval_seconds):
                    break
            self.emit("finished", {"message": "已手动停止", "checked": self.checked, "found": self.found})
        except (CloudflareError, PageStructureChanged, ExcelStoreError, Exception) as exc:
            if logging.getLogger().handlers:
                logging.getLogger(__name__).exception("查询任务停止")
            self.emit("error", {"message": str(exc), "checked": self.checked, "found": self.found})
        finally:
            if checker:
                try:
                    checker.close()
                except Exception:
                    pass
