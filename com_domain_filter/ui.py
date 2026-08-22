from __future__ import annotations

import queue
import random
import string
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

from . import __version__
from .browser_connection import open_or_connect_browser
from .patterns import (
    ALLOWED_CHARACTERS, BIND_INDEPENDENT, BIND_SHARED, BLOCK_COMMON,
    BLOCK_CUSTOM, BLOCK_FIXED, BLOCK_UNLIMITED, PATTERNS,
    BlockPatternGenerator, PatternBlock, PatternConfigurationError,
)
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
TYPE_LABELS = {
    BLOCK_FIXED: "固定文字", BLOCK_COMMON: "常用规律",
    BLOCK_CUSTOM: "自定义规律", BLOCK_UNLIMITED: "不限随机",
}
LABEL_TYPES = {label: kind for kind, label in TYPE_LABELS.items()}
COMMON_PATTERNS = tuple(item for item in PATTERNS if item != "不限")

BG = "#F5F7FB"
SURFACE = "#FFFFFF"
SIDEBAR = "#F8FAFD"
BORDER = "#DDE3EC"
TEXT = "#151B26"
MUTED = "#667085"
BLUE = "#126BEE"
BLUE_DARK = "#0E56C5"
BLUE_LIGHT = "#EAF2FF"
GREEN = "#168A4A"
GREEN_LIGHT = "#EAF8F0"
PURPLE = "#7652D6"
PURPLE_LIGHT = "#F2EEFF"
ORANGE = "#B54708"
RED = "#D92D20"


class DomainFilterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.report_callback_exception = self._report_callback_exception
        self.app_title = f"COM域名筛选器 v{__version__}"
        self.root.title(self.app_title)
        self.root.geometry("1280x820")
        self.root.minsize(900, 590)
        self.root.configure(background=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.app_data = default_app_data_dir()
        self.app_data.mkdir(parents=True, exist_ok=True)
        self.history = HistoryStore(self.app_data / "state.db")
        self.settings_store = SettingsStore(self.app_data / "settings.json")
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.worker: SearchWorker | None = None
        self.sites = [dict(item) for item in DEFAULT_SITES]
        self.browser_ready = False
        self.connected_site_url = ""
        self.preferred_page_url = ""
        self.page_label_to_url: dict[str, str] = {}
        self.page_canvases: dict[str, tk.Canvas] = {}
        self.page_frames: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.active_page = "rules"
        self.block_states: list[dict[str, tk.Variable]] = []
        self._loading_settings = False
        self.char_vars: dict[str, tk.BooleanVar] = {}
        self.char_buttons: dict[str, tk.Button] = {}

        self._build_style()
        self._build_ui()
        self._load_settings()
        if not self.block_states:
            self._append_block(BLOCK_COMMON, "AAA", 3)
        self._render_blocks()
        self._refresh_preview()
        self._set_ready(False, "未连接 Chrome")
        self._load_existing_results()
        self.root.bind_all("<MouseWheel>", self._global_wheel, add="+")
        self.root.bind_all("<Button-4>", self._global_wheel, add="+")
        self.root.bind_all("<Button-5>", self._global_wheel, add="+")
        self.root.bind("<Command-Return>", lambda _event: self._start())
        self.root.bind("<Control-Return>", lambda _event: self._start())
        self.root.after(150, self._poll_events)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Helvetica Neue", 12), background=BG, foreground=TEXT)
        style.configure("App.TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Sidebar.TFrame", background=SIDEBAR)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Helvetica Neue", 25, "bold"))
        style.configure("PageTitle.TLabel", background=BG, foreground=TEXT, font=("Helvetica Neue", 22, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Helvetica Neue", 11))
        style.configure("CardTitle.TLabel", background=SURFACE, foreground=TEXT, font=("Helvetica Neue", 14, "bold"))
        style.configure("CardText.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED, font=("Helvetica Neue", 10))
        style.configure("Status.TLabel", background=BLUE_LIGHT, foreground=BLUE_DARK, font=("Helvetica Neue", 11, "bold"), padding=(12, 7))
        style.configure("Ready.TLabel", background=GREEN_LIGHT, foreground=GREEN, font=("Helvetica Neue", 11, "bold"), padding=(12, 7))
        style.configure("Metric.TLabel", background=SURFACE, foreground=TEXT, font=("Helvetica Neue", 20, "bold"))
        style.configure("Primary.TButton", background=BLUE, foreground="white", bordercolor=BLUE, focusthickness=0, padding=(18, 10), font=("Helvetica Neue", 12, "bold"))
        style.map("Primary.TButton", background=[("active", BLUE_DARK), ("disabled", "#B8C7DA")], foreground=[("disabled", "#F4F6F8")])
        style.configure("Secondary.TButton", background=SURFACE, foreground=TEXT, bordercolor=BORDER, focusthickness=0, padding=(12, 8))
        style.map("Secondary.TButton", background=[("active", BLUE_LIGHT)])
        style.configure("Danger.TButton", background=SURFACE, foreground=RED, bordercolor="#F5C2BE", focusthickness=0, padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#FFF0EF")])
        style.configure("Nav.TButton", background=SIDEBAR, foreground=MUTED, borderwidth=0, focusthickness=0, anchor="w", padding=(18, 13), font=("Helvetica Neue", 12))
        style.map("Nav.TButton", background=[("active", BLUE_LIGHT)])
        style.configure("NavActive.TButton", background=BLUE_LIGHT, foreground=BLUE_DARK, borderwidth=0, focusthickness=0, anchor="w", padding=(18, 13), font=("Helvetica Neue", 12, "bold"))
        style.map("NavActive.TButton", background=[("active", BLUE_LIGHT)])
        style.configure("Tool.TButton", background="#F7F9FC", foreground=TEXT, bordercolor=BORDER, focusthickness=0, padding=(10, 7), font=("Helvetica Neue", 10))
        style.map("Tool.TButton", background=[("active", BLUE_LIGHT)])
        style.configure("TEntry", fieldbackground=SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=8)
        style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, bordercolor=BORDER, arrowsize=14, padding=7)
        style.configure("TSpinbox", fieldbackground=SURFACE, bordercolor=BORDER, padding=7)
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
        style.configure("TRadiobutton", background=SURFACE, foreground=TEXT)
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, rowheight=34, bordercolor=BORDER, borderwidth=1)
        style.configure("Treeview.Heading", background="#F7F9FC", foreground=MUTED, font=("Helvetica Neue", 10, "bold"), padding=8)
        style.map("Treeview", background=[("selected", BLUE_LIGHT)], foreground=[("selected", BLUE_DARK)])

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)

        sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.pack(fill="x", padx=20, pady=(24, 30))
        tk.Label(brand, text="COM", bg=BLUE, fg="white", font=("Helvetica Neue", 13, "bold"), padx=9, pady=7).pack(side="left")
        tk.Label(brand, text="域名筛选器", bg=SIDEBAR, fg=TEXT, font=("Helvetica Neue", 15, "bold")).pack(side="left", padx=8)
        for key, icon, label in (("rules", "▦", "生成规则"), ("run", "⚙", "运行设置"), ("results", "✓", "可注册结果")):
            button = ttk.Button(sidebar, text=f"{icon}   {label}", command=lambda name=key: self._show_page(name), style="Nav.TButton")
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[key] = button
        ttk.Frame(sidebar, style="Sidebar.TFrame").pack(fill="both", expand=True)
        tk.Label(sidebar, text=f"版本 {__version__}", bg=SIDEBAR, fg=MUTED, font=("Helvetica Neue", 10)).pack(anchor="w", padx=25, pady=(0, 8))
        tk.Label(sidebar, text="仅查询 .com\n结果自动去重并保存", bg=SIDEBAR, fg=MUTED, justify="left", font=("Helvetica Neue", 9)).pack(anchor="w", padx=25, pady=(0, 22))

        main = ttk.Frame(shell, style="App.TFrame")
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main, style="App.TFrame", padding=(28, 20, 28, 14))
        header.pack(fill="x")
        title_box = ttk.Frame(header, style="App.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        self.page_title_var = tk.StringVar(value="生成规则")
        self.page_subtitle_var = tk.StringVar(value="选择字符池，像搭积木一样组合域名规律")
        ttk.Label(title_box, text=self.app_title, style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, textvariable=self.page_title_var, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, textvariable=self.page_subtitle_var, style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))
        self.status_var = tk.StringVar(value="未连接 Chrome")
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side="right", padx=(18, 0))

        self.page_host = ttk.Frame(main, style="App.TFrame")
        self.page_host.pack(fill="both", expand=True, padx=28)
        self.rules_tab = self._create_scrollable_page("rules")
        self.run_tab = self._create_scrollable_page("run")
        self.results_tab = self._create_scrollable_page("results")
        self._build_rules_tab()
        self._build_run_tab()
        self._build_results_tab()
        self._build_action_bar(main)
        self._show_page("rules")

    def _build_action_bar(self, parent) -> None:
        bar = ttk.Frame(parent, style="Surface.TFrame", padding=(28, 12))
        bar.pack(fill="x", side="bottom")
        metric = ttk.Frame(bar, style="Surface.TFrame")
        metric.pack(side="left")
        ttk.Label(metric, text="当前任务", style="Muted.TLabel").pack(anchor="w")
        self.footer_task_var = tk.StringVar(value="尚未开始")
        ttk.Label(metric, textvariable=self.footer_task_var, style="CardText.TLabel").pack(anchor="w", pady=(2, 0))
        self.start_button = ttk.Button(bar, text="开始查询", command=self._start, style="Primary.TButton", state="disabled")
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(bar, text="停止", command=self._stop, style="Danger.TButton", state="disabled")
        self.stop_button.pack(side="right", padx=8)
        self.resume_button = ttk.Button(bar, text="继续", command=self._resume, style="Secondary.TButton", state="disabled")
        self.resume_button.pack(side="right")
        self.pause_button = ttk.Button(bar, text="暂停", command=self._pause, style="Secondary.TButton", state="disabled")
        self.pause_button.pack(side="right", padx=8)

    def _create_scrollable_page(self, key: str):
        page = ttk.Frame(self.page_host, style="App.TFrame")
        self.page_frames[key] = page
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0, background=BG)
        self.page_canvases[key] = canvas
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, style="App.TFrame", padding=(0, 2, 12, 20))
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        return content

    def _show_page(self, key: str) -> None:
        titles = {
            "rules": ("生成规则", "选择字符池，像搭积木一样组合域名规律"),
            "run": ("运行设置", "连接 Chrome、选择网站，并设置查询与保存方式"),
            "results": ("可注册结果", "这里只显示名称完全一致且可注册的 .com 域名"),
        }
        self.active_page = key
        for name, frame in self.page_frames.items():
            frame.pack_forget()
            self.nav_buttons[name].configure(style="NavActive.TButton" if name == key else "Nav.TButton")
        self.page_frames[key].pack(fill="both", expand=True)
        self.page_title_var.set(titles[key][0])
        self.page_subtitle_var.set(titles[key][1])

    def _global_wheel(self, event):
        canvas = self.page_canvases.get(self.active_page)
        if canvas is None:
            return None
        if getattr(event, "num", None) == 4:
            units = -2
        elif getattr(event, "num", None) == 5:
            units = 2
        else:
            units = -2 if getattr(event, "delta", 0) > 0 else 2
        canvas.yview_scroll(units, "units")
        return "break"

    def _card(self, parent, title: str, subtitle: str = "") -> ttk.Frame:
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0)
        heading = tk.Frame(card, bg=SURFACE)
        heading.pack(fill="x", padx=18, pady=(16, 10 if subtitle else 14))
        tk.Label(heading, text=title, bg=SURFACE, fg=TEXT, font=("Helvetica Neue", 14, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(heading, text=subtitle, bg=SURFACE, fg=MUTED, font=("Helvetica Neue", 10)).pack(anchor="w", pady=(4, 0))
        body = ttk.Frame(card, style="Surface.TFrame", padding=(18, 0, 18, 18))
        body.pack(fill="both", expand=True)
        card.body = body
        return card

    def _build_rules_tab(self) -> None:
        char_card = self._card(self.rules_tab, "字符池", "点击字符即可选择或取消；.com 主体只允许字母、数字和半角连字符")
        char_card.pack(fill="x", pady=(0, 14))
        char_frame = char_card.body
        actions = ttk.Frame(char_frame, style="Surface.TFrame")
        actions.pack(fill="x", pady=(0, 10))
        for label, chars in (("全选", ALLOWED_CHARACTERS), ("只选字母", string.ascii_lowercase), ("只选数字", string.digits), ("清空", "")):
            ttk.Button(actions, text=label, command=lambda value=chars: self._set_chars(value), style="Tool.TButton").pack(side="left", padx=(0, 6))
        char_grid = tk.Frame(char_frame, bg=SURFACE)
        char_grid.pack(fill="x")
        for index, char in enumerate(ALLOWED_CHARACTERS):
            variable = tk.BooleanVar(value=True)
            self.char_vars[char] = variable
            button = tk.Button(char_grid, text=char, width=3, height=1, relief="flat", bd=0, font=("Helvetica Neue", 11, "bold"), command=lambda value=char: self._toggle_char(value))
            button.grid(row=index // 12, column=index % 12, sticky="ew", padx=4, pady=4)
            char_grid.columnconfigure(index % 12, weight=1)
            self.char_buttons[char] = button
            variable.trace_add("write", lambda *_args, value=char: (self._paint_char_chip(value), self._refresh_preview()))
            self._paint_char_chip(char)
        ttk.Label(char_frame, text="连字符不能位于开头或结尾；下划线 _ 不是合法域名字符。", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        composer_card = self._card(self.rules_tab, "域名组合", "各规律块按从左到右顺序拼接；每一块都可在固定、常用、自定义和不限之间切换")
        composer_card.pack(fill="x", pady=(0, 14))
        composer = composer_card.body
        tools = ttk.Frame(composer, style="Surface.TFrame")
        tools.pack(fill="x", pady=(0, 8))
        for label, kind, value, length in (("＋ 固定文字", BLOCK_FIXED, "abc", 3), ("＋ 常用规律", BLOCK_COMMON, "AAA", 3), ("＋ 自定义规律", BLOCK_CUSTOM, "ABCDDDD", 3), ("＋ 不限随机", BLOCK_UNLIMITED, "", 4)):
            ttk.Button(tools, text=label, command=lambda k=kind, v=value, n=length: self._append_and_render(k, v, n), style="Tool.TButton").pack(side="left", padx=(0, 6))
        self.blocks_container = ttk.Frame(composer, style="Surface.TFrame")
        self.blocks_container.pack(fill="x")

        binding_card = self._card(self.rules_tab, "字符绑定方式", "决定不同规律块中的 A、B、C 是否代表同一组字符")
        binding_card.pack(fill="x", pady=(0, 14))
        binding = binding_card.body
        self.binding_var = tk.StringVar(value=BIND_INDEPENDENT)
        self.binding_var.trace_add("write", lambda *_args: self._refresh_preview())
        ttk.Radiobutton(binding, text="每个规律块独立随机", variable=self.binding_var, value=BIND_INDEPENDENT).pack(side="left")
        ttk.Radiobutton(binding, text="全部规律块共用字符", variable=self.binding_var, value=BIND_SHARED).pack(side="left", padx=28)
        ttk.Label(binding, text="固定文字和不限随机不参与绑定。", style="Muted.TLabel").pack(side="left")

        preview_card = self._card(self.rules_tab, "实时预览", "系统按当前设置固定生成三条示例，不会消耗正式查询序列")
        preview_card.pack(fill="x")
        preview = preview_card.body
        self.preview_var = tk.StringVar(value="")
        self.space_var = tk.StringVar(value="")
        preview_box = tk.Frame(preview, bg="#F7FAFF", highlightbackground="#8BB7F7", highlightthickness=1)
        preview_box.pack(fill="x")
        tk.Label(preview_box, textvariable=self.preview_var, bg="#F7FAFF", fg=BLUE_DARK, font=("Menlo", 14, "bold"), anchor="w").pack(fill="x", padx=14, pady=(13, 6))
        tk.Label(preview_box, textvariable=self.space_var, bg="#F7FAFF", fg=MUTED, font=("Helvetica Neue", 10), anchor="w").pack(fill="x", padx=14, pady=(0, 13))

    def _toggle_char(self, char: str) -> None:
        self.char_vars[char].set(not self.char_vars[char].get())

    def _paint_char_chip(self, char: str) -> None:
        button = self.char_buttons.get(char)
        variable = self.char_vars.get(char)
        if not button or variable is None:
            return
        selected = variable.get()
        button.configure(bg=BLUE if selected else "#F4F6FA", fg="white" if selected else MUTED, activebackground=BLUE_DARK if selected else BLUE_LIGHT, activeforeground="white" if selected else BLUE_DARK)

    def _new_block_state(self, kind: str, value: str, length: int) -> dict[str, tk.Variable]:
        state: dict[str, tk.Variable] = {"kind": tk.StringVar(value=kind), "value": tk.StringVar(value=value), "length": tk.StringVar(value=str(length))}
        state["value"].trace_add("write", lambda *_args: self._refresh_preview())
        state["length"].trace_add("write", lambda *_args: self._refresh_preview())
        return state

    def _append_block(self, kind: str, value: str, length: int) -> None:
        self.block_states.append(self._new_block_state(kind, value, length))

    def _append_and_render(self, kind: str, value: str, length: int) -> None:
        self._append_block(kind, value, length)
        self._render_blocks()
        self._refresh_preview()

    def _render_blocks(self) -> None:
        if not hasattr(self, "blocks_container"):
            return
        for child in self.blocks_container.winfo_children():
            child.destroy()
        for index, state in enumerate(self.block_states):
            grid_row, position = divmod(index, 4)
            grid_column = position * 2
            kind = str(state["kind"].get())
            color = {BLOCK_FIXED: BLUE, BLOCK_COMMON: PURPLE, BLOCK_CUSTOM: PURPLE, BLOCK_UNLIMITED: GREEN}[kind]
            card = tk.Frame(self.blocks_container, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, width=220, height=186)
            card.grid(row=grid_row, column=grid_column, sticky="nsew", padx=(0, 8), pady=7)
            card.grid_propagate(False)
            self.blocks_container.columnconfigure(grid_column, weight=1, uniform="blocks")
            header = tk.Frame(card, bg=color, height=34)
            header.pack(fill="x")
            header.pack_propagate(False)
            tk.Label(header, text=f"{index + 1:02d}  {TYPE_LABELS[kind]}", bg=color, fg="white", font=("Helvetica Neue", 11, "bold")).pack(side="left", padx=10, pady=7)
            body = ttk.Frame(card, style="Surface.TFrame", padding=(10, 9))
            body.pack(fill="both", expand=True)
            label_var = tk.StringVar(value=TYPE_LABELS[str(state["kind"].get())])
            kind_combo = ttk.Combobox(body, textvariable=label_var, values=tuple(LABEL_TYPES), state="readonly")
            kind_combo.pack(fill="x")
            kind_combo.bind("<<ComboboxSelected>>", lambda _event, i=index, v=label_var: self._switch_block_kind(i, LABEL_TYPES[v.get()]))
            field = ttk.Frame(body, style="Surface.TFrame")
            field.pack(fill="x", pady=(8, 0))
            if kind == BLOCK_COMMON:
                ttk.Combobox(field, textvariable=state["value"], values=COMMON_PATTERNS, state="readonly").pack(fill="x")
            elif kind == BLOCK_CUSTOM:
                ttk.Entry(field, textvariable=state["value"]).pack(fill="x")
            elif kind == BLOCK_FIXED:
                ttk.Entry(field, textvariable=state["value"]).pack(fill="x")
            else:
                ttk.Label(field, text="随机长度", style="Muted.TLabel").pack(side="left")
                ttk.Spinbox(field, from_=1, to=63, textvariable=state["length"], width=6).pack(side="right")
            hint = {BLOCK_COMMON: "从常用规律中选择", BLOCK_CUSTOM: "例：ABCDDDD、ABCBA", BLOCK_FIXED: "内容原样写入域名", BLOCK_UNLIMITED: "这就是“不限”的位数选择"}[kind]
            ttk.Label(body, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
            operations = ttk.Frame(body, style="Surface.TFrame")
            operations.pack(fill="x", side="bottom")
            ttk.Button(operations, text="↑", width=2, command=lambda i=index: self._move_block(i, -1), style="Tool.TButton").pack(side="left")
            ttk.Button(operations, text="↓", width=2, command=lambda i=index: self._move_block(i, 1), style="Tool.TButton").pack(side="left", padx=3)
            ttk.Button(operations, text="复制", command=lambda i=index: self._duplicate_block(i), style="Tool.TButton").pack(side="left")
            ttk.Button(operations, text="删除", command=lambda i=index: self._delete_block(i), style="Danger.TButton").pack(side="right")
            if position < 3 and index < len(self.block_states) - 1:
                tk.Label(self.blocks_container, text="+", bg=SURFACE, fg="#9AA4B2", font=("Helvetica Neue", 18, "bold"), width=2).grid(row=grid_row, column=grid_column + 1, pady=7)

    def _switch_block_kind(self, index: int, kind: str) -> None:
        state = self.block_states[index]
        old_kind, old_value = str(state["kind"].get()), str(state["value"].get())
        state["kind"].set(kind)
        if kind == BLOCK_COMMON and old_value.upper() not in COMMON_PATTERNS:
            state["value"].set("AAA")
        elif kind == BLOCK_CUSTOM and old_kind == BLOCK_COMMON:
            state["value"].set(old_value.upper())
        elif kind == BLOCK_FIXED and old_kind != BLOCK_FIXED:
            state["value"].set("")
        self._render_blocks()
        self._refresh_preview()

    def _move_block(self, index: int, offset: int) -> None:
        target = index + offset
        if 0 <= target < len(self.block_states):
            self.block_states[index], self.block_states[target] = self.block_states[target], self.block_states[index]
            self._render_blocks()
            self._refresh_preview()

    def _duplicate_block(self, index: int) -> None:
        state = self.block_states[index]
        try:
            length = int(str(state["length"].get()) or "1")
        except ValueError:
            length = 1
        self.block_states.insert(index + 1, self._new_block_state(str(state["kind"].get()), str(state["value"].get()), length))
        self._render_blocks()
        self._refresh_preview()

    def _delete_block(self, index: int) -> None:
        del self.block_states[index]
        self._render_blocks()
        self._refresh_preview()

    def _selected_characters(self) -> tuple[str, ...]:
        return tuple(char for char, variable in self.char_vars.items() if variable.get())

    def _block_payloads(self) -> tuple[dict, ...]:
        payloads = []
        for state in self.block_states:
            try:
                length = int(str(state["length"].get()).strip())
            except ValueError as exc:
                raise PatternConfigurationError("不限随机块的长度必须是整数。") from exc
            payloads.append(PatternBlock(str(state["kind"].get()), str(state["value"].get()), length).normalized().to_dict())
        return tuple(payloads)

    def _refresh_preview(self) -> None:
        if self._loading_settings or not hasattr(self, "preview_var"):
            return
        try:
            generator = BlockPatternGenerator(self._selected_characters(), self._block_payloads(), self.binding_var.get(), rng=random.Random(7))
            samples = [generator.generate().domain for _ in range(3)]
            self.preview_var.set("   ".join(samples))
            self.space_var.set(f"当前组合总长度：{len(samples[0]) - 4} 位；理论组合空间约：{generator.estimated_space():,}")
        except Exception as exc:
            self.preview_var.set(f"当前设置暂不能生成：{exc}")
            self.space_var.set("")

    def _build_run_tab(self) -> None:
        grid = ttk.Frame(self.run_tab, style="App.TFrame")
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1, uniform="run")
        grid.columnconfigure(1, weight=1, uniform="run")

        browser_card = self._card(grid, "Chrome 连接", "先连接浏览器，再开始查询；只有本按钮会自动打开 Chrome")
        browser_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        browser_frame = browser_card.body
        self.connect_button = ttk.Button(browser_frame, text="打开 / 连接 Chrome", command=self._open_connect_chrome, style="Primary.TButton")
        self.connect_button.grid(row=0, column=0, sticky="w")
        self.connection_detail_var = tk.StringVar(value="未连接。软件启动时不会自动打开 Chrome。")
        ttk.Label(browser_frame, textvariable=self.connection_detail_var, style="Muted.TLabel").grid(row=0, column=1, columnspan=2, sticky="w", padx=14)
        ttk.Label(browser_frame, text="指定网页", style="CardText.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 0))
        self.page_choice_var = tk.StringVar(value="连接后显示网页列表")
        self.page_combo = ttk.Combobox(browser_frame, textvariable=self.page_choice_var, state="readonly")
        self.page_combo.grid(row=1, column=1, sticky="ew", padx=14, pady=(14, 0))
        self.page_combo.bind("<<ComboboxSelected>>", self._page_selected)
        ttk.Button(browser_frame, text="连接所选网页", command=self._open_connect_chrome, style="Secondary.TButton").grid(row=1, column=2, pady=(14, 0))
        browser_frame.columnconfigure(1, weight=1)
        ttk.Label(browser_frame, text="开始、暂停、继续、停止和退出软件都不会缩小、置前、移动或关闭 Chrome。", style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))

        site_card = self._card(grid, "查询网站", "已适配 Cloudflare 和阿里云万网")
        site_card.grid(row=1, column=0, sticky="nsew", padx=(0, 7), pady=(0, 14))
        site_frame = site_card.body
        ttk.Label(site_frame, text="网站名称").grid(row=0, column=0, sticky="w")
        self.site_name_var = tk.StringVar(value="Cloudflare")
        self.site_combo = ttk.Combobox(site_frame, textvariable=self.site_name_var, state="normal", width=22)
        self.site_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.site_combo.bind("<<ComboboxSelected>>", self._on_site_selected)
        ttk.Button(site_frame, text="保存", command=self._upsert_site, style="Secondary.TButton").grid(row=0, column=2, padx=(0, 6))
        ttk.Button(site_frame, text="删除", command=self._delete_site, style="Danger.TButton").grid(row=0, column=3)
        ttk.Label(site_frame, text="网址").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.site_url_var = tk.StringVar(value="https://domains.cloudflare.com/")
        self.site_url_var.trace_add("write", lambda *_args: self._site_changed())
        ttk.Entry(site_frame, textvariable=self.site_url_var).grid(row=1, column=1, columnspan=3, sticky="ew", padx=8, pady=(10, 0))
        site_frame.columnconfigure(1, weight=1)
        ttk.Label(site_frame, text="自定义网站可保存；自动查询需要单独适配。", style="Muted.TLabel").grid(row=2, column=1, columnspan=3, sticky="w", padx=8, pady=(8, 0))

        speed_card = self._card(grid, "查询速度", "时间均可自由填写，单位为秒")
        speed_card.grid(row=1, column=1, sticky="nsew", padx=(7, 0), pady=(0, 14))
        speed_frame = speed_card.body
        ttk.Label(speed_frame, text="每次查询间隔").grid(row=0, column=0, sticky="w")
        self.interval_var = tk.StringVar(value="5")
        ttk.Entry(speed_frame, textvariable=self.interval_var, width=10).grid(row=0, column=1, padx=8)
        ttk.Label(speed_frame, text="秒").grid(row=0, column=2)
        ttk.Label(speed_frame, text="页面异常后刷新").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.retry_interval_var = tk.StringVar(value="10")
        ttk.Entry(speed_frame, textvariable=self.retry_interval_var, width=10).grid(row=1, column=1, padx=8, pady=(12, 0))
        ttk.Label(speed_frame, text="秒").grid(row=1, column=2, pady=(12, 0))
        ttk.Label(speed_frame, text="连续失败 3 次后自动记录并跳过。", style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))

        stop_card = self._card(grid, "停止条件", "可多选；任一条件先达到即停止")
        stop_card.grid(row=2, column=0, sticky="nsew", padx=(0, 7), pady=(0, 14))
        stop_frame = stop_card.body
        self.limit_tests_enabled_var, self.limit_tests_var = tk.BooleanVar(value=True), tk.StringVar(value="10000")
        ttk.Checkbutton(stop_frame, text="检测指定数量", variable=self.limit_tests_enabled_var).grid(row=0, column=0, sticky="w")
        ttk.Entry(stop_frame, textvariable=self.limit_tests_var, width=12).grid(row=0, column=1, padx=8)
        ttk.Label(stop_frame, text="个域名后停止").grid(row=0, column=2, sticky="w")
        self.limit_found_enabled_var, self.limit_found_var = tk.BooleanVar(value=False), tk.StringVar(value="100")
        ttk.Checkbutton(stop_frame, text="找到指定数量", variable=self.limit_found_enabled_var).grid(row=1, column=0, sticky="w", pady=8)
        ttk.Entry(stop_frame, textvariable=self.limit_found_var, width=12).grid(row=1, column=1, padx=8, pady=8)
        ttk.Label(stop_frame, text="个完全一致且可注册的 .com 后停止").grid(row=1, column=2, sticky="w", pady=8)
        self.run_until_stopped_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(stop_frame, text="一直运行，直到手动停止", variable=self.run_until_stopped_var).grid(row=2, column=0, columnspan=3, sticky="w")

        excel_card = self._card(grid, "Excel 结果文件", "只保存名称完全一致且可注册的 .com，自动追加并去重")
        excel_card.grid(row=2, column=1, sticky="nsew", padx=(7, 0), pady=(0, 14))
        excel_frame = excel_card.body
        self.excel_path_var = tk.StringVar(value=str(Path.home() / "Documents" / "可注册COM域名.xlsx"))
        ttk.Entry(excel_frame, textvariable=self.excel_path_var).pack(fill="x")
        excel_actions = ttk.Frame(excel_frame, style="Surface.TFrame")
        excel_actions.pack(fill="x", pady=(12, 0))
        ttk.Button(excel_actions, text="选择路径…", command=self._choose_excel, style="Secondary.TButton").pack(side="left")
        ttk.Button(excel_actions, text="打开 Excel", command=self._open_excel, style="Secondary.TButton").pack(side="left", padx=8)

        progress_card = self._card(grid, "当前进度", "运行日志会自动滚动到最新一条")
        progress_card.grid(row=3, column=0, columnspan=2, sticky="nsew")
        progress = progress_card.body
        self.current_domain_var, self.checked_var, self.found_var = tk.StringVar(value="尚未开始"), tk.StringVar(value="0"), tk.StringVar(value="0")
        for column, title, variable in ((0, "正在查询", self.current_domain_var), (1, "本次已检测", self.checked_var), (2, "本次找到", self.found_var)):
            box = ttk.Frame(progress, style="Surface.TFrame")
            box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
            ttk.Label(box, text=title, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=variable, style="Metric.TLabel" if column else "CardText.TLabel").pack(anchor="w", pady=(5, 0))
            progress.columnconfigure(column, weight=1)
        self.log = tk.Text(progress, height=7, wrap="word", state="disabled", bg="#F8FAFC", fg=TEXT, relief="flat", highlightbackground=BORDER, highlightthickness=1, padx=10, pady=8, font=("Menlo", 10))
        self.log.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(14, 0))

    def _build_results_tab(self) -> None:
        stats = ttk.Frame(self.results_tab, style="App.TFrame")
        stats.pack(fill="x", pady=(0, 14))
        self.result_total_var = tk.StringVar(value="0")
        for column, title, value, color in ((0, "已保存域名", self.result_total_var, BLUE), (1, "本次找到", self.found_var, GREEN), (2, "输出格式", tk.StringVar(value="Excel"), PURPLE)):
            card = tk.Frame(stats, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 7, 0 if column == 2 else 7))
            stats.columnconfigure(column, weight=1, uniform="stats")
            tk.Label(card, text=title, bg=SURFACE, fg=MUTED, font=("Helvetica Neue", 10)).pack(anchor="w", padx=16, pady=(14, 4))
            tk.Label(card, textvariable=value, bg=SURFACE, fg=color, font=("Helvetica Neue", 21, "bold")).pack(anchor="w", padx=16, pady=(0, 14))

        result_card = self._card(self.results_tab, "可注册域名", "所有任务共用同一个历史记录，并按域名自动去重")
        result_card.pack(fill="both", expand=True)
        toolbar = result_card.body
        self.result_filter_var = tk.StringVar()
        self.result_filter_var.trace_add("write", lambda *_args: self._apply_result_filter())
        top = ttk.Frame(toolbar, style="Surface.TFrame")
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="搜索", style="CardText.TLabel").pack(side="left")
        ttk.Entry(top, textvariable=self.result_filter_var, width=28).pack(side="left", padx=8)
        ttk.Button(top, text="打开 Excel", command=self._open_excel, style="Secondary.TButton").pack(side="right")
        ttk.Button(top, text="打开所在文件夹", command=self._open_excel_folder, style="Secondary.TButton").pack(side="right", padx=8)
        self.result_rows: list[tuple[str, str, str, str]] = []
        columns = ("domain", "pattern", "site", "time")
        tree_frame = ttk.Frame(toolbar, style="Surface.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.result_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=17)
        for key, title, width in (("domain", "域名", 300), ("pattern", "规律组合", 340), ("site", "查询网站", 190), ("time", "查询时间", 210)):
            self.result_tree.heading(key, text=title)
            self.result_tree.column(key, width=width, anchor="w" if key in {"domain", "pattern"} else "center")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scroll.set)
        self.result_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _set_chars(self, selected: str) -> None:
        selected_set = set(selected)
        for char, variable in self.char_vars.items():
            variable.set(char in selected_set)

    def _refresh_site_combo(self) -> None:
        self.site_combo.configure(values=tuple(item["name"] for item in self.sites))

    def _on_site_selected(self, _event=None) -> None:
        selected = next((item for item in self.sites if item["name"] == self.site_name_var.get().strip()), None)
        if selected:
            self.site_url_var.set(selected["url"])

    def _site_changed(self) -> None:
        if not self._loading_settings and self.connected_site_url and self.site_url_var.get().strip().rstrip("/") != self.connected_site_url.rstrip("/"):
            self._set_ready(False, "网站已改变，请重新连接 Chrome")

    def _validated_site(self) -> tuple[str, Path]:
        site_url = self.site_url_var.get().strip()
        parsed = urlparse(site_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("请输入完整的查询网址。")
        checker_for_url(site_url)
        return site_url, self.app_data / "google-chrome-profile" / parsed.hostname

    def _upsert_site(self) -> None:
        name, url = self.site_name_var.get().strip(), self.site_url_var.get().strip()
        parsed = urlparse(url)
        if not name or parsed.scheme not in ("http", "https") or not parsed.hostname:
            messagebox.showerror("网站设置有误", "请输入网站名称和完整网址。")
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

    def _open_connect_chrome(self) -> None:
        if self.worker and self.worker.is_alive:
            messagebox.showinfo("查询正在运行", "请先停止当前查询，再重新选择 Chrome 网页。")
            return
        try:
            site_url, profile_dir = self._validated_site()
        except ValueError as exc:
            messagebox.showerror("网站设置有误", str(exc))
            return
        selected_url = self.page_label_to_url.get(self.page_choice_var.get(), self.preferred_page_url)
        self.browser_ready = False
        self.connect_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status_var.set("正在打开/连接 Chrome…")
        self.connection_detail_var.set("每隔 1 秒检测原来的调试端口，最长等待 120 秒。")

        def task() -> None:
            try:
                result = open_or_connect_browser(site_url, profile_dir, selected_url)
                self._emit("browser_connection", {"result": result, "site_url": site_url})
            except Exception as exc:
                self._emit("browser_error", {"message": str(exc)})
        threading.Thread(target=task, name="browser-connection", daemon=True).start()

    def _page_selected(self, _event=None) -> None:
        self.preferred_page_url = self.page_label_to_url.get(self.page_choice_var.get(), "")
        self._set_ready(False, "已选择网页，请点击“连接所选网页”确认")

    def _set_ready(self, ready: bool, message: str) -> None:
        self.browser_ready = ready
        self.status_var.set(message)
        if hasattr(self, "footer_task_var") and not (self.worker and self.worker.is_alive):
            self.footer_task_var.set("浏览器已就绪" if ready else "等待连接 Chrome")
        self.status_label.configure(style="Ready.TLabel" if ready else "Status.TLabel")
        running = bool(self.worker and self.worker.is_alive)
        self.start_button.configure(state="normal" if ready and not running else "disabled")

    def _choose_excel(self) -> None:
        current = Path(self.excel_path_var.get()).expanduser()
        path = filedialog.asksaveasfilename(title="选择 Excel 结果文件", defaultextension=".xlsx", filetypes=(("Excel 工作簿", "*.xlsx"),), initialfile=current.name or "可注册COM域名.xlsx", initialdir=str(current.parent))
        if path:
            self.excel_path_var.set(path)

    def _open_excel(self) -> None:
        path = Path(self.excel_path_var.get()).expanduser()
        if not path.exists():
            messagebox.showinfo("提示", "Excel 文件尚未生成，找到第一个可注册域名后会自动创建。")
            return
        subprocess.Popen(["open", str(path)])

    def _open_excel_folder(self) -> None:
        path = Path(self.excel_path_var.get()).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(path.parent)])

    @staticmethod
    def _positive_int(value: str, name: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{name}必须是整数。") from exc
        if number < 1:
            raise ValueError(f"{name}必须大于0。")
        return number

    def _make_config(self) -> RunConfig:
        if not self.browser_ready:
            raise ValueError("Chrome 还没有准备就绪。请先到运行设置点击“打开/连接 Chrome”。")
        site_url, profile_dir = self._validated_site()
        if site_url.rstrip("/") != self.connected_site_url.rstrip("/"):
            raise ValueError("当前网站与已连接的网站不一致，请重新连接 Chrome。")
        blocks = self._block_payloads()
        BlockPatternGenerator(self._selected_characters(), blocks, self.binding_var.get())
        try:
            interval, retry_interval = float(self.interval_var.get()), float(self.retry_interval_var.get())
        except ValueError as exc:
            raise ValueError("查询间隔和自动刷新间隔必须是数字。") from exc
        if interval <= 0 or retry_interval <= 0:
            raise ValueError("查询间隔和自动刷新间隔必须大于0秒。")
        if not any((self.limit_tests_enabled_var.get(), self.limit_found_enabled_var.get(), self.run_until_stopped_var.get())):
            raise ValueError("请至少勾选一种停止方式。")
        excel_path = Path(self.excel_path_var.get()).expanduser()
        if excel_path.suffix.lower() != ".xlsx":
            raise ValueError("Excel 结果文件必须使用 .xlsx 后缀。")
        return RunConfig(
            site_url=site_url, characters=self._selected_characters(), patterns=(), prefix="", suffix="", unlimited_length=1,
            interval_seconds=interval, retry_interval_seconds=retry_interval,
            limit_tests_enabled=self.limit_tests_enabled_var.get(), limit_tests=self._positive_int(self.limit_tests_var.get(), "检测数量"),
            limit_found_enabled=self.limit_found_enabled_var.get(), limit_found=self._positive_int(self.limit_found_var.get(), "目标数量"),
            run_until_stopped=self.run_until_stopped_var.get(), excel_path=excel_path, profile_dir=profile_dir,
            blocks=blocks, binding_mode=self.binding_var.get(), preferred_page_url=self.preferred_page_url,
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
        self.current_domain_var.set("正在连接…")
        self.footer_task_var.set("正在启动查询")
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
            self.status_var.set("正在停止查询；Chrome 保持原样")

    def _set_running_buttons(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running or not self.browser_ready else "normal")
        self.pause_button.configure(state="normal" if running else "disabled")
        self.resume_button.configure(state="disabled")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.connect_button.configure(state="disabled" if running else "normal")

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
        if event_type == "browser_connection":
            result = payload["result"]
            self.connect_button.configure(state="normal")
            self.connected_site_url, self.preferred_page_url = payload["site_url"], result.selected_url
            self.page_label_to_url, labels = {}, []
            for index, page in enumerate(result.pages, start=1):
                label = f"{index}. {page['title']} — {page['url']}"
                labels.append(label)
                self.page_label_to_url[label] = page["url"]
            self.page_combo.configure(values=labels)
            selected_label = next((label for label, url in self.page_label_to_url.items() if url == result.selected_url), labels[0] if labels else "")
            self.page_choice_var.set(selected_label or "没有找到网页")
            self.connection_detail_var.set(f"已连接：{result.selected_url}" if result.selected_url else result.message)
            self._set_ready(result.ready, result.message)
            if result.verification_required:
                self.root.bell()
        elif event_type == "browser_error":
            self.connect_button.configure(state="normal")
            self.connection_detail_var.set(payload["message"])
            self._set_ready(False, "Chrome 未连接")
            messagebox.showerror("Chrome 连接失败", payload["message"])
        elif event_type == "status":
            self.status_var.set(payload["message"])
            self._append_log(payload["message"])
        elif event_type == "current":
            self.current_domain_var.set(payload["domain"])
            self.footer_task_var.set(f"正在查询 {payload['domain']}")
        elif event_type == "progress":
            self.checked_var.set(str(payload["checked"]))
            self.found_var.set(str(payload["found"]))
            self._append_log(f"已检测 {payload['checked']} 个；上一结果：{STATUS_NAMES.get(payload.get('last_status'), payload.get('last_status', ''))}")
        elif event_type == "found":
            self.found_var.set(str(payload["found"]))
            self.result_rows.insert(0, (payload["domain"], payload["pattern"], self.site_name_var.get(), payload["checked_at"]))
            self._apply_result_filter()
            self._append_log(f"已保存：{payload['domain']}")
        elif event_type == "verification":
            self.status_var.set("需要验证；查询正在等待")
            self.root.bell()
            messagebox.showwarning("需要网站安全验证", f"{payload['message']}\n\n请在已经打开的 Chrome 中手动完成验证。软件不会自动切换窗口，也不会绕过验证。")
        elif event_type == "finished":
            self.status_var.set("准备就绪")
            self.footer_task_var.set("任务已结束")
            self._set_running_buttons(False)
            self._append_log(payload["message"])
        elif event_type == "error":
            message = payload["message"]
            if "Chrome" in message or "浏览器" in message:
                self._set_ready(False, "Chrome 连接已中断")
            else:
                self.status_var.set("查询发生错误")
            self._set_running_buttons(False)
            self._append_log(f"错误：{message}")
            messagebox.showerror("查询已停止", message)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _load_existing_results(self) -> None:
        try:
            rows = self.history.found_rows()
        except Exception:
            return
        for row in reversed(rows):
            domain, checked_at, pattern, _prefix, _suffix = row[:5]
            site = row[5] if len(row) > 5 and row[5] else "历史记录"
            self.result_rows.append((domain, pattern, site, checked_at))
        self._apply_result_filter()

    def _apply_result_filter(self) -> None:
        if not hasattr(self, "result_tree"):
            return
        query = self.result_filter_var.get().strip().lower()
        if hasattr(self, "result_total_var"):
            self.result_total_var.set(str(len(self.result_rows)))
        self.result_tree.delete(*self.result_tree.get_children())
        for row in self.result_rows:
            if not query or any(query in str(value).lower() for value in row):
                self.result_tree.insert("", "end", values=row)

    def _settings_dict(self) -> dict:
        blocks = [{"kind": str(state["kind"].get()), "value": str(state["value"].get()), "length": str(state["length"].get())} for state in self.block_states]
        return {
            "characters": list(self._selected_characters()), "blocks": blocks, "binding_mode": self.binding_var.get(),
            "site_name": self.site_name_var.get(), "site_url": self.site_url_var.get(), "sites": self.sites,
            "preferred_page_url": self.preferred_page_url, "interval": self.interval_var.get(), "retry_interval": self.retry_interval_var.get(),
            "limit_tests_enabled": self.limit_tests_enabled_var.get(), "limit_tests": self.limit_tests_var.get(),
            "limit_found_enabled": self.limit_found_enabled_var.get(), "limit_found": self.limit_found_var.get(),
            "run_until_stopped": self.run_until_stopped_var.get(), "excel_path": self.excel_path_var.get(),
        }

    def _save_settings(self) -> None:
        self.settings_store.save(self._settings_dict())

    def _load_settings(self) -> None:
        settings = self.settings_store.load()
        if not settings:
            self._refresh_site_combo()
            return
        self._loading_settings = True
        try:
            saved_sites = settings.get("sites")
            if isinstance(saved_sites, list):
                valid_sites = [{"name": str(item.get("name", "")).strip(), "url": str(item.get("url", "")).strip()} for item in saved_sites if isinstance(item, dict) and str(item.get("name", "")).strip() and str(item.get("url", "")).strip()]
                if valid_sites:
                    self.sites = valid_sites
            self._refresh_site_combo()
            selected_chars = set(settings.get("characters", ALLOWED_CHARACTERS))
            for char, variable in self.char_vars.items():
                variable.set(char in selected_chars)
            blocks = settings.get("blocks")
            if isinstance(blocks, list) and blocks:
                for item in blocks:
                    self._append_block(str(item.get("kind", BLOCK_COMMON)), str(item.get("value", "AAA")), int(item.get("length", 3)))
            else:
                prefix, suffix = str(settings.get("prefix", "")), str(settings.get("suffix", ""))
                patterns = settings.get("patterns") or ["AAA"]
                selected = str(patterns[0])
                if prefix:
                    self._append_block(BLOCK_FIXED, prefix, 1)
                if selected == "不限":
                    self._append_block(BLOCK_UNLIMITED, "", int(settings.get("unlimited_length", 6)))
                else:
                    self._append_block(BLOCK_COMMON, selected if selected in COMMON_PATTERNS else "AAA", 1)
                if suffix:
                    self._append_block(BLOCK_FIXED, suffix, 1)
            self.binding_var.set(str(settings.get("binding_mode", BIND_INDEPENDENT)))
            for variable, key in ((self.site_name_var, "site_name"), (self.site_url_var, "site_url"), (self.interval_var, "interval"), (self.retry_interval_var, "retry_interval"), (self.limit_tests_var, "limit_tests"), (self.limit_found_var, "limit_found"), (self.excel_path_var, "excel_path")):
                if key in settings:
                    variable.set(settings[key])
            for variable, key in ((self.limit_tests_enabled_var, "limit_tests_enabled"), (self.limit_found_enabled_var, "limit_found_enabled"), (self.run_until_stopped_var, "run_until_stopped")):
                if key in settings:
                    variable.set(bool(settings[key]))
            self.preferred_page_url = str(settings.get("preferred_page_url", ""))
        finally:
            self._loading_settings = False

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        import logging
        logging.getLogger(__name__).error("界面回调异常", exc_info=(exc_type, exc_value, exc_traceback))
        messagebox.showerror("软件发生错误", f"{exc_value}\n\n错误已经写入：{self.app_data / 'app.log'}")

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive:
            if not messagebox.askyesno("退出软件", "查询仍在运行。是否只停止查询并退出？Chrome 会保持原样。"):
                return
            self.worker.stop(keep_browser_open=True)
        try:
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    DomainFilterApp(root)
    root.mainloop()
