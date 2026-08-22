from __future__ import annotations

import os
import queue
import string
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

from .patterns import ALLOWED_CHARACTERS, PATTERNS, PatternConfigurationError, PatternGenerator, normalize_custom
from .sites import DEFAULT_SITES, checker_for_url
from .storage import HistoryStore, SettingsStore, default_app_data_dir
from .worker import RunConfig, SearchWorker


STATUS_NAMES = {
    "exact_available": "完全一致且可注册",
    "available_mismatch": "可注册但名称不一致",
    "exact_unavailable": "完全一致但不可注册",
    "no_com": "结果中没有.com",
    "query_failed": "页面连续失败，已跳过",
}


class DomainFilterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.report_callback_exception = self._report_callback_exception
        self.root.title("COM域名筛选器")
        self.root.geometry("980x720")
        self.root.minsize(860, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Command-Return>", lambda _event: self._start())
        self.root.bind("<Control-Return>", lambda _event: self._start())

        self.app_data = default_app_data_dir()
        self.app_data.mkdir(parents=True, exist_ok=True)
        self.history = HistoryStore(self.app_data / "state.db")
        self.settings_store = SettingsStore(self.app_data / "settings.json")
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.worker: SearchWorker | None = None
        self.sites = [dict(item) for item in DEFAULT_SITES]

        self.char_vars: dict[str, tk.BooleanVar] = {}
        self.pattern_vars: dict[str, tk.BooleanVar] = {}
        self._build_style()
        self._build_ui()
        self._load_settings()
        self.root.after(150, self._poll_events)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("aqua")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Helvetica Neue", 24, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5F6368")
        style.configure("Accent.TButton", font=("Helvetica Neue", 13, "bold"))
        style.configure("Status.TLabel", font=("Helvetica Neue", 12, "bold"), foreground="#1F4E78")
        style.configure("TLabelframe.Label", font=("Helvetica Neue", 12, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(18, 14))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", padx=42, pady=(0, 10))
        ttk.Label(header, text="COM域名筛选器", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="随机生成 · 多网站验证 · 自动写入Excel",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=18, pady=(8, 0))
        self.status_var = tk.StringVar(value="准备就绪")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side="right", pady=(8, 0))

        self._build_bottom_bar(outer)

        notebook = ttk.Notebook(outer)
        self.notebook = notebook
        self.tab_canvases = []
        notebook.pack(fill="both", expand=True)
        rules_page, self.rules_tab = self._create_scrollable_tab(notebook)
        run_page, self.run_tab = self._create_scrollable_tab(notebook)
        results_page, self.results_tab = self._create_scrollable_tab(notebook)
        notebook.add(rules_page, text="  生成规则  ")
        notebook.add(run_page, text="  运行设置  ")
        notebook.add(results_page, text="  可注册结果  ")

        self._build_rules_tab()
        self._build_run_tab()
        self._build_results_tab()

    def _create_scrollable_tab(self, notebook):
        page = ttk.Frame(notebook)
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0)
        self.tab_canvases.append(canvas)
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=14)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_width(event):
            canvas.itemconfigure(window, width=event.width)

        def wheel(event):
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        content.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_width)
        page.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", wheel))
        page.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))
        return page, content

    def _build_rules_tab(self) -> None:
        char_frame = ttk.LabelFrame(self.rules_tab, text="字符池（可多选）", padding=10)
        char_frame.pack(fill="x", pady=(0, 12))
        actions = ttk.Frame(char_frame)
        actions.pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="全选", command=lambda: self._set_chars(ALLOWED_CHARACTERS, True)).pack(side="left")
        ttk.Button(actions, text="只选字母", command=self._letters_only).pack(side="left", padx=6)
        ttk.Button(actions, text="只选数字", command=self._digits_only).pack(side="left")
        ttk.Button(actions, text="清空", command=lambda: self._set_chars("", False)).pack(side="left", padx=6)

        char_grid = ttk.Frame(char_frame)
        char_grid.pack(fill="x")
        for index, char in enumerate(ALLOWED_CHARACTERS):
            variable = tk.BooleanVar(value=True)
            self.char_vars[char] = variable
            ttk.Checkbutton(char_grid, text=char, variable=variable, width=3).grid(
                row=index // 12, column=index % 12, sticky="w", padx=5, pady=3
            )
        ttk.Label(
            char_frame,
            text=".com 名称可使用英文字母、数字和半角连字符 -；连字符不能位于开头或结尾。下划线 _ 不合法。",
            foreground="#5F6368",
        ).pack(anchor="w", pady=(6, 0))

        custom_frame = ttk.LabelFrame(self.rules_tab, text="固定内容与随机长度", padding=10)
        custom_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(custom_frame, text="固定开头").grid(row=0, column=0, sticky="w")
        self.prefix_var = tk.StringVar()
        ttk.Entry(custom_frame, textvariable=self.prefix_var, width=24).grid(row=0, column=1, sticky="w", padx=(8, 24))
        ttk.Label(custom_frame, text="随机规律").grid(row=0, column=2)
        ttk.Label(custom_frame, text="固定结尾").grid(row=0, column=3, padx=(24, 0))
        self.suffix_var = tk.StringVar()
        ttk.Entry(custom_frame, textvariable=self.suffix_var, width=24).grid(row=0, column=4, sticky="w", padx=8)
        ttk.Label(custom_frame, text="“不限”随机部分长度").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.unlimited_length_var = tk.StringVar(value="6")
        ttk.Spinbox(custom_frame, from_=1, to=63, width=8, textvariable=self.unlimited_length_var).grid(
            row=1, column=1, sticky="w", padx=8, pady=(10, 0)
        )
        ttk.Label(
            custom_frame,
            text="示例：开头 abc ＋ 规律 AAA ＋ 结尾 88 → abcxxx88.com",
            foreground="#5F6368",
        ).grid(row=1, column=2, columnspan=3, sticky="w", pady=(10, 0))

        pattern_frame = ttk.LabelFrame(self.rules_tab, text="规律选号（可多选）", padding=10)
        pattern_frame.pack(fill="x")
        pattern_actions = ttk.Frame(pattern_frame)
        pattern_actions.pack(fill="x", pady=(0, 6))
        ttk.Button(pattern_actions, text="全选规律", command=lambda: self._set_patterns(True)).pack(side="left")
        ttk.Button(pattern_actions, text="清空规律", command=lambda: self._set_patterns(False)).pack(side="left", padx=6)
        pattern_grid = ttk.Frame(pattern_frame)
        pattern_grid.pack(fill="both", expand=True)
        for index, pattern in enumerate(PATTERNS):
            variable = tk.BooleanVar(value=pattern == "AAA")
            self.pattern_vars[pattern] = variable
            ttk.Checkbutton(pattern_grid, text=pattern, variable=variable, width=13).grid(
                row=index // 4, column=index % 4, sticky="w", padx=10, pady=5
            )

    def _build_run_tab(self) -> None:
        site_frame = ttk.LabelFrame(self.run_tab, text="查询网站", padding=12)
        site_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(site_frame, text="网站名称").grid(row=0, column=0, sticky="w")
        self.site_name_var = tk.StringVar(value="Cloudflare")
        self.site_combo = ttk.Combobox(
            site_frame,
            textvariable=self.site_name_var,
            values=tuple(item["name"] for item in self.sites),
            state="normal",
            width=24,
        )
        self.site_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.site_combo.bind("<<ComboboxSelected>>", self._on_site_selected)
        ttk.Button(site_frame, text="添加/保存", command=self._upsert_site).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(site_frame, text="删除", command=self._delete_site).grid(
            row=0, column=3
        )
        ttk.Label(site_frame, text="网址").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.site_url_var = tk.StringVar(value="https://domains.cloudflare.com/")
        ttk.Entry(site_frame, textvariable=self.site_url_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=8, pady=(10, 0)
        )
        site_frame.columnconfigure(1, weight=1)
        ttk.Label(
            site_frame,
            text="名称可以直接输入；填写网址后点“添加/保存”。当前已适配 Cloudflare 和阿里云万网。",
            foreground="#5F6368",
        ).grid(row=2, column=1, columnspan=3, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(
            site_frame,
            text="浏览器：系统 Google Chrome（使用独立资料目录和独立窗口，不操作你现有的标签页）",
            foreground="#22577A",
        ).grid(row=3, column=1, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        speed_frame = ttk.LabelFrame(self.run_tab, text="查询速度", padding=12)
        speed_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(speed_frame, text="每次查询间隔").pack(side="left")
        self.interval_var = tk.StringVar(value="5")
        ttk.Entry(speed_frame, textvariable=self.interval_var, width=10).pack(side="left", padx=8)
        ttk.Label(speed_frame, text="秒").pack(side="left")
        ttk.Label(speed_frame, text="页面异常重试间隔").pack(side="left", padx=(24, 0))
        self.retry_interval_var = tk.StringVar(value="10")
        ttk.Entry(speed_frame, textvariable=self.retry_interval_var, width=10).pack(side="left", padx=8)
        ttk.Label(speed_frame, text="秒").pack(side="left")
        ttk.Label(speed_frame, text="连续失败3次会跳过该域名。", foreground="#9A6700").pack(side="left", padx=18)

        stop_frame = ttk.LabelFrame(self.run_tab, text="停止条件（可多选，任一条件先达到即停止）", padding=12)
        stop_frame.pack(fill="x", pady=(0, 12))
        self.limit_tests_enabled_var = tk.BooleanVar(value=True)
        self.limit_tests_var = tk.StringVar(value="10000")
        ttk.Checkbutton(stop_frame, text="检测指定数量", variable=self.limit_tests_enabled_var).grid(row=0, column=0, sticky="w")
        ttk.Entry(stop_frame, textvariable=self.limit_tests_var, width=12).grid(row=0, column=1, padx=8)
        ttk.Label(stop_frame, text="个域名后停止").grid(row=0, column=2, sticky="w")

        self.limit_found_enabled_var = tk.BooleanVar(value=False)
        self.limit_found_var = tk.StringVar(value="100")
        ttk.Checkbutton(stop_frame, text="找到指定数量", variable=self.limit_found_enabled_var).grid(row=1, column=0, sticky="w", pady=8)
        ttk.Entry(stop_frame, textvariable=self.limit_found_var, width=12).grid(row=1, column=1, padx=8, pady=8)
        ttk.Label(stop_frame, text="个完全一致且可注册的.com后停止").grid(row=1, column=2, sticky="w", pady=8)

        self.run_until_stopped_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(stop_frame, text="一直运行，直到手动停止", variable=self.run_until_stopped_var).grid(
            row=2, column=0, columnspan=3, sticky="w"
        )

        excel_frame = ttk.LabelFrame(self.run_tab, text="Excel结果文件", padding=12)
        excel_frame.pack(fill="x", pady=(0, 12))
        default_excel = str(Path.home() / "Documents" / "可注册COM域名.xlsx")
        self.excel_path_var = tk.StringVar(value=default_excel)
        ttk.Entry(excel_frame, textvariable=self.excel_path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(excel_frame, text="选择路径…", command=self._choose_excel).pack(side="left", padx=8)
        ttk.Button(excel_frame, text="打开Excel", command=self._open_excel).pack(side="left")
        ttk.Label(
            excel_frame,
            text="只写入“名称完全一致且可注册”的.com域名；所有任务追加到同一个文件并自动去重。",
            foreground="#5F6368",
        ).pack(side="bottom", anchor="w", pady=(10, 0))

        progress_frame = ttk.LabelFrame(self.run_tab, text="当前进度", padding=12)
        progress_frame.pack(fill="x")
        self.current_domain_var = tk.StringVar(value="尚未开始")
        self.checked_var = tk.StringVar(value="0")
        self.found_var = tk.StringVar(value="0")
        ttk.Label(progress_frame, text="正在查询：").grid(row=0, column=0, sticky="w")
        ttk.Label(progress_frame, textvariable=self.current_domain_var, font=("Menlo", 13)).grid(row=0, column=1, sticky="w")
        ttk.Label(progress_frame, text="本次已检测：").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Label(progress_frame, textvariable=self.checked_var).grid(row=1, column=1, sticky="w", pady=8)
        ttk.Label(progress_frame, text="本次找到：").grid(row=2, column=0, sticky="w")
        ttk.Label(progress_frame, textvariable=self.found_var).grid(row=2, column=1, sticky="w")
        self.log = tk.Text(progress_frame, height=8, wrap="word", state="disabled")
        self.log.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        progress_frame.columnconfigure(2, weight=1)
        progress_frame.rowconfigure(3, weight=1)

    def _build_results_tab(self) -> None:
        toolbar = ttk.Frame(self.results_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="本次找到的可注册域名会立即写入Excel。", foreground="#5F6368").pack(side="left")
        ttk.Button(toolbar, text="打开Excel", command=self._open_excel).pack(side="right")
        ttk.Button(toolbar, text="打开所在文件夹", command=self._open_excel_folder).pack(side="right", padx=8)

        columns = ("domain", "pattern", "time")
        tree_frame = ttk.Frame(self.results_tab)
        tree_frame.pack(fill="both", expand=True)
        self.result_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=24)
        self.result_tree.heading("domain", text="域名")
        self.result_tree.heading("pattern", text="规律")
        self.result_tree.heading("time", text="查询时间")
        self.result_tree.column("domain", width=460)
        self.result_tree.column("pattern", width=150, anchor="center")
        self.result_tree.column("time", width=220, anchor="center")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scroll.set)
        self.result_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_bottom_bar(self, parent) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=42, pady=(0, 10))
        self.start_button = ttk.Button(bar, text="开始查询", command=self._start, style="Accent.TButton")
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(bar, text="暂停", command=self._pause, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        self.resume_button = ttk.Button(bar, text="继续", command=self._resume, state="disabled")
        self.resume_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="停止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(
            bar,
            text="Chrome 不会自动缩小；可手动最小化，查询仍会继续。",
            foreground="#5F6368",
        ).pack(side="right")

    def _set_chars(self, selected: str, value: bool) -> None:
        selected_set = set(selected)
        for char, variable in self.char_vars.items():
            variable.set(char in selected_set if value else False)

    def _letters_only(self) -> None:
        self._set_chars(string.ascii_lowercase, True)

    def _digits_only(self) -> None:
        self._set_chars(string.digits, True)

    def _set_patterns(self, value: bool) -> None:
        for variable in self.pattern_vars.values():
            variable.set(value)

    def _refresh_site_combo(self) -> None:
        self.site_combo.configure(values=tuple(item["name"] for item in self.sites))

    def _on_site_selected(self, _event=None) -> None:
        name = self.site_name_var.get().strip()
        selected = next((item for item in self.sites if item["name"] == name), None)
        if selected:
            self.site_url_var.set(selected["url"])

    def _upsert_site(self) -> None:
        name = self.site_name_var.get().strip()
        url = self.site_url_var.get().strip()
        parsed = urlparse(url)
        if not name:
            messagebox.showerror("网站名称有误", "请输入网站名称。")
            return
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            messagebox.showerror("网址有误", "请输入完整网址，例如 https://wanwang.aliyun.com/domain")
            return
        existing = next((item for item in self.sites if item["name"] == name), None)
        if existing:
            existing["url"] = url
        else:
            self.sites.append({"name": name, "url": url})
        self._refresh_site_combo()
        self._save_settings()
        self.status_var.set(f"已保存网站：{name}")

    def _delete_site(self) -> None:
        name = self.site_name_var.get().strip()
        if len(self.sites) <= 1:
            messagebox.showinfo("不能删除", "至少保留一个查询网站。")
            return
        remaining = [item for item in self.sites if item["name"] != name]
        if len(remaining) == len(self.sites):
            messagebox.showinfo("未找到", "列表中没有这个网站。")
            return
        self.sites = remaining
        self._refresh_site_combo()
        self.site_name_var.set(self.sites[0]["name"])
        self.site_url_var.set(self.sites[0]["url"])
        self._save_settings()
        self.status_var.set(f"已删除网站：{name}")

    def _choose_excel(self) -> None:
        path = filedialog.asksaveasfilename(
            title="选择Excel结果文件",
            defaultextension=".xlsx",
            filetypes=(("Excel工作簿", "*.xlsx"),),
            initialfile=Path(self.excel_path_var.get()).name or "可注册COM域名.xlsx",
            initialdir=str(Path(self.excel_path_var.get()).expanduser().parent),
        )
        if path:
            self.excel_path_var.set(path)

    def _open_excel(self) -> None:
        path = Path(self.excel_path_var.get()).expanduser()
        if not path.exists():
            messagebox.showinfo("提示", "Excel文件尚未生成，找到第一个可注册域名后会自动创建。")
            return
        subprocess.Popen(["open", str(path)])

    def _open_excel_folder(self) -> None:
        path = Path(self.excel_path_var.get()).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(path.parent)])

    def _positive_int(self, value: str, name: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{name}必须是整数。") from exc
        if number < 1:
            raise ValueError(f"{name}必须大于0。")
        return number

    def _make_config(self) -> RunConfig:
        characters = tuple(char for char, variable in self.char_vars.items() if variable.get())
        patterns = tuple(pattern for pattern, variable in self.pattern_vars.items() if variable.get())
        prefix = normalize_custom(self.prefix_var.get())
        suffix = normalize_custom(self.suffix_var.get())
        unlimited_length = self._positive_int(self.unlimited_length_var.get(), "随机部分长度")
        try:
            interval = float(self.interval_var.get())
        except ValueError as exc:
            raise ValueError("查询间隔必须是数字。") from exc
        if interval <= 0:
            raise ValueError("查询间隔必须大于0秒。")
        try:
            retry_interval = float(self.retry_interval_var.get())
        except ValueError as exc:
            raise ValueError("页面异常重试间隔必须是数字。") from exc
        if retry_interval <= 0:
            raise ValueError("页面异常重试间隔必须大于0秒。")
        if not any(
            (self.limit_tests_enabled_var.get(), self.limit_found_enabled_var.get(), self.run_until_stopped_var.get())
        ):
            raise ValueError("请至少勾选一种停止方式。")
        limit_tests = self._positive_int(self.limit_tests_var.get(), "检测数量")
        limit_found = self._positive_int(self.limit_found_var.get(), "目标数量")
        site_url = self.site_url_var.get().strip()
        parsed = urlparse(site_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("请输入完整的查询网址。")
        checker_for_url(site_url)
        excel_path = Path(self.excel_path_var.get()).expanduser()
        if excel_path.suffix.lower() != ".xlsx":
            raise ValueError("Excel结果文件必须使用 .xlsx 后缀。")
        PatternGenerator(characters, patterns, prefix, suffix, unlimited_length)
        return RunConfig(
            site_url=site_url,
            characters=characters,
            patterns=patterns,
            prefix=prefix,
            suffix=suffix,
            unlimited_length=unlimited_length,
            interval_seconds=interval,
            retry_interval_seconds=retry_interval,
            limit_tests_enabled=self.limit_tests_enabled_var.get(),
            limit_tests=limit_tests,
            limit_found_enabled=self.limit_found_enabled_var.get(),
            limit_found=limit_found,
            run_until_stopped=self.run_until_stopped_var.get(),
            excel_path=excel_path,
            profile_dir=self.app_data / "google-chrome-profile" / parsed.hostname,
        )

    def _start(self) -> None:
        if self.worker and self.worker.is_alive:
            return
        try:
            config = self._make_config()
        except (ValueError, PatternConfigurationError) as exc:
            messagebox.showerror("设置有误", str(exc))
            return
        self._save_settings()
        self.checked_var.set("0")
        self.found_var.set("0")
        self.current_domain_var.set("正在启动…")
        self.worker = SearchWorker(config, self.history, self._emit)
        self.worker.start()
        self._set_running_buttons(True)

    def _pause(self) -> None:
        if self.worker:
            self.worker.pause()
            self.pause_button.configure(state="disabled")
            self.resume_button.configure(state="normal")

    def _resume(self) -> None:
        if self.worker:
            self.worker.resume()
            self.pause_button.configure(state="normal")
            self.resume_button.configure(state="disabled")

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop(keep_browser_open=True)
            self.status_var.set("正在停止…Chrome将保持打开")

    def _set_running_buttons(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.pause_button.configure(state="normal" if running else "disabled")
        self.resume_button.configure(state="disabled")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _emit(self, event_type: str, payload: dict) -> None:
        self.events.put((event_type, payload))

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.events.get_nowait()
                self._handle_event(event_type, payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)

    def _handle_event(self, event_type: str, payload: dict) -> None:
        if event_type == "status":
            self.status_var.set(payload["message"])
            self._append_log(payload["message"])
        elif event_type == "current":
            self.current_domain_var.set(payload["domain"])
        elif event_type == "progress":
            self.checked_var.set(str(payload["checked"]))
            self.found_var.set(str(payload["found"]))
            status_name = STATUS_NAMES.get(payload.get("last_status"), payload.get("last_status", ""))
            self._append_log(f"已检测 {payload['checked']} 个；上一结果：{status_name}")
        elif event_type == "found":
            self.found_var.set(str(payload["found"]))
            self.result_tree.insert("", 0, values=(payload["domain"], payload["pattern"], payload["checked_at"]))
            self._append_log(f"已保存：{payload['domain']}")
        elif event_type == "verification":
            self.status_var.set("等待你完成验证")
            self.root.bell()
            messagebox.showwarning(
                "需要网站安全验证",
                f"{payload['message']}\n\n查询已经暂停。请在自动打开的系统 Google Chrome 窗口中完成验证；如果验证再次循环，请停止任务，稍后重试或更换网络。软件不会自动绕过验证。",
            )
        elif event_type == "finished":
            self.status_var.set("已停止")
            self._set_running_buttons(False)
            self._append_log(payload["message"])
            messagebox.showinfo("查询结束", f"{payload['message']}\n\n本次检测：{payload['checked']}\n本次找到：{payload['found']}")
        elif event_type == "error":
            self.status_var.set("发生错误")
            self._set_running_buttons(False)
            self._append_log(f"错误：{payload['message']}")
            messagebox.showerror("查询已停止", payload["message"])

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        import logging

        logging.getLogger(__name__).error(
            "界面回调异常",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        messagebox.showerror(
            "软件发生错误",
            f"{exc_value}\n\n错误已经写入：{self.app_data / 'app.log'}",
        )

    def _settings_dict(self) -> dict:
        return {
            "characters": [char for char, variable in self.char_vars.items() if variable.get()],
            "patterns": [pattern for pattern, variable in self.pattern_vars.items() if variable.get()],
            "prefix": self.prefix_var.get(),
            "suffix": self.suffix_var.get(),
            "unlimited_length": self.unlimited_length_var.get(),
            "site_name": self.site_name_var.get(),
            "site_url": self.site_url_var.get(),
            "sites": self.sites,
            "interval": self.interval_var.get(),
            "retry_interval": self.retry_interval_var.get(),
            "limit_tests_enabled": self.limit_tests_enabled_var.get(),
            "limit_tests": self.limit_tests_var.get(),
            "limit_found_enabled": self.limit_found_enabled_var.get(),
            "limit_found": self.limit_found_var.get(),
            "run_until_stopped": self.run_until_stopped_var.get(),
            "excel_path": self.excel_path_var.get(),
        }

    def _save_settings(self) -> None:
        self.settings_store.save(self._settings_dict())

    def _load_settings(self) -> None:
        settings = self.settings_store.load()
        if not settings:
            return
        saved_sites = settings.get("sites")
        if isinstance(saved_sites, list):
            valid_sites = [
                {"name": str(item.get("name", "")).strip(), "url": str(item.get("url", "")).strip()}
                for item in saved_sites
                if isinstance(item, dict) and str(item.get("name", "")).strip() and str(item.get("url", "")).strip()
            ]
            if valid_sites:
                self.sites = valid_sites
                self._refresh_site_combo()
        selected_chars = set(settings.get("characters", []))
        for char, variable in self.char_vars.items():
            variable.set(char in selected_chars)
        selected_patterns = set(settings.get("patterns", []))
        for pattern, variable in self.pattern_vars.items():
            variable.set(pattern in selected_patterns)
        for variable, key in (
            (self.prefix_var, "prefix"),
            (self.suffix_var, "suffix"),
            (self.unlimited_length_var, "unlimited_length"),
            (self.site_name_var, "site_name"),
            (self.site_url_var, "site_url"),
            (self.interval_var, "interval"),
            (self.retry_interval_var, "retry_interval"),
            (self.limit_tests_var, "limit_tests"),
            (self.limit_found_var, "limit_found"),
            (self.excel_path_var, "excel_path"),
        ):
            if key in settings:
                variable.set(settings[key])
        for variable, key in (
            (self.limit_tests_enabled_var, "limit_tests_enabled"),
            (self.limit_found_enabled_var, "limit_found_enabled"),
            (self.run_until_stopped_var, "run_until_stopped"),
        ):
            if key in settings:
                variable.set(bool(settings[key]))

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive:
            if not messagebox.askyesno("退出软件", "查询仍在运行。是否停止查询并退出？"):
                return
            self.worker.stop(keep_browser_open=False)
        try:
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    DomainFilterApp(root)
    root.mainloop()
