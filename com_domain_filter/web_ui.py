from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import webview

from . import __version__
from .browser_connection import open_or_connect_browser
from .excel_store import HistoryExcelStore, STATUS_LABELS
from .patterns import (
    ALLOWED_CHARACTERS,
    BIND_INDEPENDENT,
    BLOCK_COMMON,
    BlockPatternGenerator,
    PATTERNS,
    PatternBlock,
    PatternConfigurationError,
)
from .sites import DEFAULT_SITES, checker_for_url
from .storage import HistoryStore, SettingsStore, default_app_data_dir
from .worker import RunConfig, SearchWorker


def _resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


class WebApi:
    def __init__(self) -> None:
        self.window = None
        self.app_data = default_app_data_dir()
        self.app_data.mkdir(parents=True, exist_ok=True)
        self.history = HistoryStore(self.app_data / "state.db")
        self.settings_store = SettingsStore(self.app_data / "settings.json")
        self.worker: SearchWorker | None = None
        self.browser_ready = False
        self.connected_site_url = ""
        self.preferred_page_url = ""

    def set_window(self, window) -> None:
        self.window = window

    def _defaults(self) -> dict:
        return {
            "characters": list(ALLOWED_CHARACTERS),
            "blocks": [{"kind": BLOCK_COMMON, "value": "AAA", "length": 3}],
            "binding_mode": BIND_INDEPENDENT,
            "site_name": "Cloudflare",
            "site_url": "https://domains.cloudflare.com/",
            "sites": [dict(item) for item in DEFAULT_SITES],
            "preferred_page_url": "",
            "interval": "5",
            "retry_interval": "10",
            "limit_tests_enabled": True,
            "limit_tests": "10000",
            "limit_found_enabled": False,
            "limit_found": "100",
            "run_until_stopped": False,
            "excel_path": str(Path.home() / "Documents" / "可注册COM域名.xlsx"),
            "history_excel_path": str(Path.home() / "Documents" / "已查询COM域名.xlsx"),
        }

    def _settings(self) -> dict:
        data = self._defaults()
        stored = self.settings_store.load()
        if stored:
            data.update(stored)
        if not isinstance(data.get("blocks"), list) or not data["blocks"]:
            data["blocks"] = self._defaults()["blocks"]
        sites = data.get("sites")
        if not isinstance(sites, list) or not sites:
            data["sites"] = self._defaults()["sites"]
        return data

    def _results(self) -> list[dict]:
        rows = []
        for domain, checked_at, pattern, _prefix, _suffix, site in reversed(self.history.found_rows()):
            rows.append({
                "domain": domain,
                "pattern": pattern,
                "site": site or "历史记录",
                "time": checked_at,
            })
        return rows

    def _history_rows(self) -> list[dict]:
        rows = []
        for domain, status, checked_at, pattern, prefix, suffix, detail, site in self.history.history_rows():
            rows.append(
                {
                    "domain": domain,
                    "status": status,
                    "status_label": STATUS_LABELS.get(status, status),
                    "time": checked_at,
                    "pattern": pattern,
                    "prefix": prefix,
                    "suffix": suffix,
                    "detail": detail,
                    "site": site or "历史记录",
                }
            )
        return rows

    def initial_state(self) -> dict:
        return {
            "ok": True,
            "version": __version__,
            "patterns": [item for item in PATTERNS if item != "不限"],
            "allowed_characters": list(ALLOWED_CHARACTERS),
            "settings": self._settings(),
            "results": self._results(),
            "history": self._history_rows(),
            "tested_total": self.history.total_count(),
        }

    @staticmethod
    def _normalized_payload(payload: dict) -> tuple[tuple[str, ...], tuple[dict, ...], str]:
        characters = tuple(dict.fromkeys(str(item).lower() for item in payload.get("characters", [])))
        raw_blocks = payload.get("blocks", [])
        blocks = tuple(PatternBlock.from_dict(item).normalized().to_dict() for item in raw_blocks)
        binding = str(payload.get("binding_mode", BIND_INDEPENDENT))
        BlockPatternGenerator(characters, blocks, binding)
        return characters, blocks, binding

    def preview(self, payload: dict) -> dict:
        try:
            characters, blocks, binding = self._normalized_payload(payload)
            generator = BlockPatternGenerator(characters, blocks, binding, rng=random.Random(7))
            samples = [generator.generate().domain for _ in range(3)]
            length = len(samples[0]) - 4
            return {
                "ok": True,
                "samples": samples,
                "length": length,
                "space": generator.estimated_space(),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def save_settings(self, payload: dict) -> dict:
        try:
            current = self._settings()
            current.update(payload)
            self.settings_store.save(current)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    @staticmethod
    def _validated_site(site_url: str) -> tuple[str, Path]:
        site_url = site_url.strip()
        parsed = urlparse(site_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("请输入完整的查询网址。")
        checker_for_url(site_url)
        profile_dir = default_app_data_dir() / "google-chrome-profile" / parsed.hostname
        return site_url, profile_dir

    def connect_browser(self, payload: dict) -> dict:
        if self.worker and self.worker.is_alive:
            return {"ok": False, "message": "查询正在运行，请先停止当前任务。"}
        try:
            site_url, profile_dir = self._validated_site(str(payload.get("site_url", "")))
            preferred = str(payload.get("preferred_page_url", ""))
            result = open_or_connect_browser(site_url, profile_dir, preferred)
            self.browser_ready = result.ready
            self.connected_site_url = site_url if result.ready else ""
            self.preferred_page_url = result.selected_url
            return {
                "ok": True,
                "ready": result.ready,
                "message": result.message,
                "selected_url": result.selected_url,
                "verification_required": result.verification_required,
                "pages": list(result.pages),
            }
        except Exception as exc:
            self.browser_ready = False
            return {"ok": False, "ready": False, "message": str(exc)}

    @staticmethod
    def _positive_int(value, label: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}必须是整数。") from exc
        if number < 1:
            raise ValueError(f"{label}必须大于 0。")
        return number

    def start_search(self, payload: dict) -> dict:
        if self.worker and self.worker.is_alive:
            return {"ok": False, "message": "查询已经在运行。"}
        try:
            if not self.browser_ready:
                raise ValueError("Chrome 尚未连接，请先到运行设置点击“打开 / 连接 Chrome”。")
            site_url, profile_dir = self._validated_site(str(payload.get("site_url", "")))
            if site_url.rstrip("/") != self.connected_site_url.rstrip("/"):
                raise ValueError("当前网站与已连接网站不一致，请重新连接 Chrome。")
            characters, blocks, binding = self._normalized_payload(payload)
            interval = float(payload.get("interval", 5))
            retry_interval = float(payload.get("retry_interval", 10))
            if interval <= 0 or retry_interval <= 0:
                raise ValueError("查询间隔和自动刷新时间必须大于 0 秒。")
            limit_tests_enabled = bool(payload.get("limit_tests_enabled"))
            limit_found_enabled = bool(payload.get("limit_found_enabled"))
            run_until_stopped = bool(payload.get("run_until_stopped"))
            if not any((limit_tests_enabled, limit_found_enabled, run_until_stopped)):
                raise ValueError("请至少选择一种停止方式。")
            excel_path = Path(str(payload.get("excel_path", ""))).expanduser()
            if excel_path.suffix.lower() != ".xlsx":
                raise ValueError("Excel 结果文件必须使用 .xlsx 后缀。")
            config = RunConfig(
                site_url=site_url,
                characters=characters,
                patterns=(),
                prefix="",
                suffix="",
                unlimited_length=1,
                interval_seconds=interval,
                retry_interval_seconds=retry_interval,
                limit_tests_enabled=limit_tests_enabled,
                limit_tests=self._positive_int(payload.get("limit_tests"), "检测数量"),
                limit_found_enabled=limit_found_enabled,
                limit_found=self._positive_int(payload.get("limit_found"), "目标数量"),
                run_until_stopped=run_until_stopped,
                excel_path=excel_path,
                profile_dir=profile_dir,
                blocks=blocks,
                binding_mode=binding,
                preferred_page_url=str(payload.get("preferred_page_url", self.preferred_page_url)),
            )
            self.save_settings(payload)
            self.worker = SearchWorker(config, self.history, self._emit)
            self.worker.start()
            return {"ok": True}
        except (ValueError, PatternConfigurationError) as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def pause_search(self) -> dict:
        if self.worker:
            self.worker.pause()
        return {"ok": True}

    def resume_search(self, payload: dict | None = None) -> dict:
        if not self.worker or not self.worker.is_alive:
            return {"ok": False, "message": "当前没有可继续的查询任务。"}
        try:
            if payload is not None:
                characters, blocks, binding = self._normalized_payload(payload)
                self.worker.update_rules(characters, blocks, binding)
                self.save_settings(payload)
            self.worker.resume()
            return {"ok": True}
        except (ValueError, PatternConfigurationError) as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def stop_search(self) -> dict:
        if self.worker:
            self.worker.stop(keep_browser_open=True)
        return {"ok": True}

    def choose_excel(self, current_path: str) -> dict:
        try:
            current = Path(current_path).expanduser()
            result = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                directory=str(current.parent),
                save_filename=current.name or "可注册COM域名.xlsx",
                file_types=("Excel 工作簿 (*.xlsx)",),
            )
            if not result:
                return {"ok": True, "path": ""}
            path = result[0] if isinstance(result, (tuple, list)) else result
            if not str(path).lower().endswith(".xlsx"):
                path = f"{path}.xlsx"
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def choose_history_excel(self, current_path: str) -> dict:
        try:
            current = Path(current_path).expanduser()
            result = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                directory=str(current.parent),
                save_filename=current.name or "已查询COM域名.xlsx",
                file_types=("Excel 工作簿 (*.xlsx)",),
            )
            if not result:
                return {"ok": True, "path": ""}
            path = result[0] if isinstance(result, (tuple, list)) else result
            if not str(path).lower().endswith(".xlsx"):
                path = f"{path}.xlsx"
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def history_state(self) -> dict:
        return {
            "ok": True,
            "history": self._history_rows(),
            "results": self._results(),
            "tested_total": self.history.total_count(),
        }

    def export_history(self, path: str) -> dict:
        try:
            target = Path(path).expanduser()
            count = HistoryExcelStore(target).export(self.history.history_rows())
            settings = self._settings()
            settings["history_excel_path"] = str(target)
            self.settings_store.save(settings)
            return {"ok": True, "path": str(target), "count": count}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def open_history_excel(self, path: str) -> dict:
        exported = self.export_history(path)
        if not exported.get("ok"):
            return exported
        subprocess.Popen(["open", exported["path"]])
        return exported

    def delete_history(self, domains: list[str]) -> dict:
        if self.worker and self.worker.is_alive:
            return {"ok": False, "message": "请先停止当前查询，再删除已查询记录。"}
        try:
            deleted = self.history.delete_domains(domains)
            state = self.history_state()
            state.update({"deleted": deleted})
            return state
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def clear_history(self) -> dict:
        if self.worker and self.worker.is_alive:
            return {"ok": False, "message": "请先停止当前查询，再清空已查询记录。"}
        try:
            deleted = self.history.clear()
            state = self.history_state()
            state.update({"deleted": deleted})
            return state
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    @staticmethod
    def open_excel(path: str) -> dict:
        target = Path(path).expanduser()
        if not target.exists():
            return {"ok": False, "message": "Excel 文件尚未生成，找到第一个可注册域名后会自动创建。"}
        subprocess.Popen(["open", str(target)])
        return {"ok": True}

    @staticmethod
    def open_folder(path: str) -> dict:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(target.parent)])
        return {"ok": True}

    def save_site(self, payload: dict) -> dict:
        name = str(payload.get("name", "")).strip()
        url = str(payload.get("url", "")).strip()
        parsed = urlparse(url)
        if not name or parsed.scheme not in ("http", "https") or not parsed.hostname:
            return {"ok": False, "message": "请输入网站名称和完整网址。"}
        settings = self._settings()
        sites = [dict(item) for item in settings.get("sites", [])]
        existing = next((item for item in sites if item.get("name") == name), None)
        if existing:
            existing["url"] = url
        else:
            sites.append({"name": name, "url": url})
        settings.update({"sites": sites, "site_name": name, "site_url": url})
        self.settings_store.save(settings)
        return {"ok": True, "sites": sites}

    def delete_site(self, name: str) -> dict:
        settings = self._settings()
        sites = [dict(item) for item in settings.get("sites", [])]
        if len(sites) <= 1:
            return {"ok": False, "message": "至少保留一个查询网站。"}
        remaining = [item for item in sites if item.get("name") != name]
        if len(remaining) == len(sites):
            return {"ok": False, "message": "列表中没有这个网站。"}
        settings["sites"] = remaining
        self.settings_store.save(settings)
        return {"ok": True, "sites": remaining}

    def _emit(self, event_type: str, payload: dict) -> None:
        if not self.window:
            return
        message = json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False)
        try:
            self.window.run_js(f"window.handleBackendEvent({message});")
        except Exception:
            pass

    def on_closing(self, *_args) -> None:
        if self.worker and self.worker.is_alive:
            self.worker.stop(keep_browser_open=True)


def run() -> None:
    api = WebApi()
    index = _resource_path("web_ui", "index.html")
    window = webview.create_window(
        f"COM域名筛选器 v{__version__}",
        url=index.as_uri(),
        js_api=api,
        width=1440,
        height=900,
        min_size=(980, 640),
        background_color="#F6F8FC",
        text_select=True,
    )
    api.set_window(window)
    window.events.closing += api.on_closing
    webview.start(gui="cocoa", debug=False)
