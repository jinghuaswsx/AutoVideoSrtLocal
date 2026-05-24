from __future__ import annotations

import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from tools.shopify_image_localizer import api_client, cancellation, controller, settings, storage, version, ai_listing_uploader
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp




class ShopifyImageLocalizerApp:
    def __init__(self, *, prompt_on_start: bool = True) -> None:
        runtime_config = settings.load_runtime_config()
        # 确保配置文件存在；只有拿到非空凭据时才写，避免开发环境生成空 key 配置后污染打包。
        config_path = settings.config_path()
        if (
            not config_path.is_file()
            and str(runtime_config.get("api_key") or "").strip()
            and str(runtime_config.get("browser_user_data_dir") or "").strip()
        ):
            settings.save_runtime_config(
                base_url=runtime_config["base_url"],
                api_key=runtime_config["api_key"],
                browser_user_data_dir=runtime_config["browser_user_data_dir"],
                shopify_domain=runtime_config.get("shopify_domain"),
            )

        self.root = tk.Tk()
        self.root.title(f"Shopify 图片本地化替换 v{version.RELEASE_VERSION}")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = int(screen_w * 0.8)
        win_h = int(screen_h * 0.8)
        win_x = int(screen_w * 0.1)
        win_y = int(screen_h * 0.1)
        self.root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self.root.minsize(960, 680)
        self.root.resizable(True, True)

        self.base_url_var = tk.StringVar(value=runtime_config["base_url"])
        self.api_key_var = tk.StringVar(value=runtime_config["api_key"])
        self.browser_user_data_dir_var = tk.StringVar(value=runtime_config["browser_user_data_dir"])
        self.current_shopify_domain_var = tk.StringVar(
            value=runtime_config.get("shopify_domain") or settings.DEFAULT_SHOPIFY_DOMAIN
        )
        self.product_code_var = tk.StringVar()
        self.shopify_product_id_var = tk.StringVar()
        self.language_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请输入商品 ID，选择语言后开始")
        self.advanced_visible = False
        self.language_items: list[dict] = []
        self.language_label_to_code: dict[str, str] = {}
        self.language_label_to_shop_locale: dict[str, str] = {}
        self.language_label_to_shopify_name: dict[str, str] = {}
        self.domain_items: list[dict] = settings.default_domain_items()
        self._workspace_root = ""
        self._download_dir = ""
        self._current_cancel_token: cancellation.CancellationToken | None = None
        self.progress_current_var = tk.StringVar(value="当前：等待启动")
        self.progress_total_var = tk.StringVar(value="总耗时 00:00")
        self._progress_started_at: float | None = None
        self._progress_step_started_at: float | None = None
        self._progress_current_iid: str | None = None
        self._progress_tick_after_id: str | None = None
        self._progress_running: bool = False
        self._main_thread = threading.current_thread()
        self._tk_mainloop_started = False
        self._pending_ui_callbacks: list[tuple[int, object, tuple]] = []
        self._pending_ui_lock = threading.Lock()
        self._shutdown_requested = False
        # 批量语言选择
        self.batch_languages: list[str] = []  # 已选择的批量语言标签列表
        self.current_running_language: str = ""  # 当前正在运行的语言
        self.localized_links: list[dict[str, str]] = []  # 当前会话中成功换图的所有小语种链接

        # AI 自动上品相关属性
        self.ai_tasks_list: list[dict] = []
        self.ai_task_label_to_id: dict[str, int] = {}
        self.ai_task_var = tk.StringVar()
        self.ai_product_code_var = tk.StringVar()
        self.ai_product_title_var = tk.StringVar()
        self.ai_product_domain_var = tk.StringVar()
        self.ai_shopify_product_id_var = tk.StringVar()

        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=16, pady=16)

        # 引入内容容器 Frame 代替 ttk.Notebook 实现双 Tab 面板切换
        self.content_frame = tk.Frame(self.main_frame)

        self.tab_localizer = tk.Frame(self.content_frame)
        self.tab_ai_listing = tk.Frame(self.content_frame)

        self._build_form()
        self._build_ai_listing_tab()
        self.content_frame.pack(fill="both", expand=False, pady=(0, 10))
        self.switch_tab("localizer")
        self._build_summary()
        self._build_log()
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

        self._append_log("程序已启动，正在加载线上语言列表")
        self.root.after(0, self._mark_tk_mainloop_started)
        self._load_languages_async()
        self._load_domains_async()
        _ = prompt_on_start

    def switch_tab(self, tab_name: str) -> None:
        if tab_name == "localizer":
            self.tab_ai_listing.pack_forget()
            self.tab_localizer.pack(fill="both", expand=True)
            self.btn_toggle_localizer.configure(
                bg="#1976d2",
                fg="white",
                activebackground="#1565c0",
                activeforeground="white",
                font=("TkDefaultFont", 11, "bold"),
            )
            self.btn_toggle_ai_listing.configure(
                bg="#f5f5f5",
                fg="#333333",
                activebackground="#e0e0e0",
                activeforeground="#333333",
                font=("TkDefaultFont", 11),
            )
        else:
            self.tab_localizer.pack_forget()
            self.tab_ai_listing.pack(fill="both", expand=True)
            self.btn_toggle_localizer.configure(
                bg="#f5f5f5",
                fg="#333333",
                activebackground="#e0e0e0",
                activeforeground="#333333",
                font=("TkDefaultFont", 11),
            )
            self.btn_toggle_ai_listing.configure(
                bg="#1976d2",
                fg="white",
                activebackground="#1565c0",
                activeforeground="white",
                font=("TkDefaultFont", 11, "bold"),
            )

    def _mark_tk_mainloop_started(self) -> None:
        self._tk_mainloop_started = True
        with self._pending_ui_lock:
            callbacks = list(self._pending_ui_callbacks)
            self._pending_ui_callbacks.clear()
        for delay_ms, callback, args in callbacks:
            if delay_ms <= 0:
                callback(*args)
            else:
                self.root.after(delay_ms, callback, *args)

    def _ui_after(self, delay_ms: int, callback, *args):
        if threading.current_thread() is not self._main_thread and not self._tk_mainloop_started:
            with self._pending_ui_lock:
                self._pending_ui_callbacks.append((delay_ms, callback, args))
            return None
        try:
            return self.root.after(delay_ms, callback, *args)
        except RuntimeError as exc:
            if "main thread is not in main loop" not in str(exc):
                raise
            self._call_ui_inline_if_possible(callback, *args)
            return None

    def _call_ui_inline_if_possible(self, callback, *args) -> None:
        try:
            callback(*args)
        except RuntimeError as exc:
            if "main thread is not in main loop" not in str(exc):
                raise

    def _build_form(self) -> None:
        # 整个界面最左上角的状态指示：未登录显示红字，登录后显示当前域名（黑字）
        self.top_bar_frame = tk.Frame(self.main_frame)
        self.top_bar_frame.pack(fill="x", pady=(0, 4))
        self.current_login_status_var = tk.StringVar()
        self.current_login_status_label = tk.Label(
            self.top_bar_frame,
            textvariable=self.current_login_status_var,
            anchor="w",
            font=("TkDefaultFont", 14, "bold"),
            wraplength=500,
            justify="left",
        )
        self.current_login_status_label.pack(side="left", anchor="w")

        # 新增 Toggle 形式的 Tab 切换按钮组
        self.toggle_frame = tk.Frame(self.top_bar_frame, bg="#e0e0e0", padx=1, pady=1)
        self.toggle_frame.pack(side="left", padx=20)

        self.btn_toggle_localizer = tk.Button(
            self.toggle_frame,
            text=" 批量换图工具 ",
            command=lambda: self.switch_tab("localizer"),
            relief="flat",
            bd=0,
            padx=16,
            pady=4,
            cursor="hand2",
        )
        self.btn_toggle_localizer.pack(side="left")

        self.btn_toggle_ai_listing = tk.Button(
            self.toggle_frame,
            text=" 自动上品工具 ",
            command=lambda: self.switch_tab("ai_listing"),
            relief="flat",
            bd=0,
            padx=16,
            pady=4,
            cursor="hand2",
        )
        self.btn_toggle_ai_listing.pack(side="left", padx=(1, 0))

        self.close_app_button = tk.Button(
            self.top_bar_frame,
            text="关闭软件",
            command=self.close_application,
            width=10,
            bg="#c62828",
            fg="white",
            activebackground="#b71c1c",
            activeforeground="white",
        )
        self.close_app_button.pack(side="right", padx=(12, 0), anchor="ne")
        # 启动时根据当前 domain + 本地 slug 缓存决定显示——已有缓存直接显示「当前网站」，
        # 用户不必再点「已登录」（仍可手动刷新）。
        self._update_login_status(self.current_shopify_domain_var.get() or None)

        self.login_shopify_frame = tk.Frame(self.tab_localizer)
        self.login_shopify_frame.pack(fill="x", pady=(0, 10))
        initial_domains = [settings.normalize_domain(item.get("domain")) for item in self.domain_items]
        self.domain_box = ttk.Combobox(
            self.login_shopify_frame,
            textvariable=self.current_shopify_domain_var,
            state="readonly",
            values=initial_domains,
            width=28,
            height=2,
        )
        self.domain_box.pack(side="left", padx=(0, 8), ipady=12)
        self.domain_box.bind("<<ComboboxSelected>>", lambda _event: self._on_shopify_domain_selected())
        self.login_shopify_button = tk.Button(
            self.login_shopify_frame,
            text="登录shopify店铺",
            command=lambda: self.open_shopify_login(),
            width=24,
            height=2,
        )
        self.login_shopify_button.pack(side="left")
        self._refresh_login_button_text()
        self.confirm_login_button = tk.Button(
            self.login_shopify_frame,
            text="已登录",
            command=lambda: self.confirm_shopify_login(),
            width=10,
            height=2,
        )
        self.confirm_login_button.pack(side="left", padx=(8, 0))
        self._login_shopify_tip_full_text = (
            "第一步： 选域名，点登录店铺\n"
            "第二步： 登录后，选网站，点已登录"
        )
        self.login_shopify_tip_label = tk.Label(
            self.login_shopify_frame,
            text=self._login_shopify_tip_full_text,
            justify="left",
            anchor="w",
            fg="red",
            font=("TkDefaultFont", 14, "bold"),
        )
        self.login_shopify_tip_label.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self.login_shopify_tip_label.bind(
            "<Configure>",
            lambda _event: self._refresh_login_tip(),
        )

        tk.Label(self.tab_localizer, text="商品 ID").pack(anchor="w")
        self.product_code_entry = tk.Entry(self.tab_localizer, textvariable=self.product_code_var, width=80)
        self.product_code_entry.pack(fill="x", pady=(4, 10))
        self.product_code_entry.focus_set()

        # 语言选择区域:垂直布局
        language_row = tk.Frame(self.tab_localizer)
        language_row.pack(fill="x", pady=(4, 10))

        # 单个语言选择
        tk.Label(language_row, text="语言", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        self.language_box = ttk.Combobox(
            language_row,
            textvariable=self.language_var,
            state="readonly",
            values=[],
        )
        self.language_box.pack(fill="x", pady=(4, 0))

        self.batch_language_controls_frame = tk.Frame(language_row)
        self.batch_language_controls_frame.pack(fill="x", pady=(8, 0), anchor="w")

        # 批量选择按钮
        self.batch_select_btn = tk.Button(
            self.batch_language_controls_frame,
            text="批量选择语言",
            command=self._open_batch_language_dialog,
            font=("TkDefaultFont", 18, "bold"),
            height=2,
        )
        self.batch_select_btn.pack(side="left", anchor="w")

        # 批量选择的语言显示区域
        self.batch_languages_frame = tk.Frame(self.batch_language_controls_frame)
        self.batch_languages_frame.pack(side="left", fill="x", expand=True, padx=(12, 0))

        tk.Label(self.tab_localizer, text="Shopify ID（可选）").pack(anchor="w")
        self.shopify_product_id_entry = tk.Entry(
            self.tab_localizer,
            textvariable=self.shopify_product_id_var,
            width=80,
        )
        self.shopify_product_id_entry.pack(fill="x", pady=(4, 2))

        self.resolved_shopify_id_label = tk.Label(
            self.tab_localizer,
            text="",
            justify="left",
            fg="#228B22",
            font=("TkDefaultFont", 12, "bold"),
        )
        self.resolved_shopify_id_label.pack(anchor="w", pady=(0, 4))

        self.tip_label = tk.Label(
            self.tab_localizer,
            text=(
                "Shopify ID 留空时自动从线上商品页实时获取，并回存到服务器；"
                "填写后会优先使用该值。"
            ),
            justify="left",
            fg="#555",
            wraplength=860,
        )
        self.tip_label.pack(anchor="w", pady=(0, 10))

        self.action_frame = tk.Frame(self.tab_localizer)
        self.action_frame.pack(fill="x", pady=(4, 8))
        self.start_button = tk.Button(
            self.action_frame,
            text="开始替换",
            command=self.start_run,
            width=12,
        )
        self.start_button.pack(side="left")
        self.stop_button = tk.Button(
            self.action_frame,
            text="停止",
            command=self.request_stop,
            width=10,
            state="disabled",
            bg="#c62828",
            fg="white",
            activebackground="#b71c1c",
            activeforeground="white",
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_workspace_button = tk.Button(
            self.action_frame,
            text="打开任务目录",
            command=self._open_workspace,
            state="disabled",
            width=14,
        )
        self.open_workspace_button.pack(side="left", padx=(8, 0))
        self.open_ez_button = tk.Button(
            self.action_frame,
            text="打开 EZ 页面",
            command=lambda: self.open_shopify_target("ez"),
            width=14,
        )
        self.open_ez_button.pack(side="left", padx=(8, 0))
        self.open_detail_button = tk.Button(
            self.action_frame,
            text="打开详情页",
            command=lambda: self.open_shopify_target("detail"),
            width=14,
        )
        self.open_detail_button.pack(side="left", padx=(8, 0))
        self.mapping_button = tk.Button(
            self.action_frame,
            text="映射管理",
            command=self.open_mapping_management,
            width=12,
        )
        self.mapping_button.pack(side="left", padx=(8, 0))
        self.open_download_button = tk.Button(
            self.action_frame,
            text="打开下载目录",
            command=self._open_download_dir,
            state="disabled",
            width=16,
        )
        self.open_download_button.pack(side="left", padx=(8, 0))
        self.link_check_button = tk.Button(
            self.action_frame,
            text="链接检查",
            command=self.open_link_check_dialog,
            width=12,
        )
        self.link_check_button.pack(side="left", padx=(8, 0))
        self.advanced_button = tk.Button(
            self.action_frame,
            text="显示高级设置",
            command=self.toggle_advanced,
            width=14,
        )
        self.advanced_button.pack(side="right")

        self.advanced_frame = tk.Frame(self.tab_localizer)
        tk.Label(self.advanced_frame, text="服务端 API 地址（固定线上）").pack(anchor="w")
        tk.Entry(
            self.advanced_frame,
            textvariable=self.base_url_var,
            width=80,
            state="readonly",
        ).pack(fill="x", pady=(4, 8))
        tk.Label(self.advanced_frame, text="OpenAPI Key").pack(anchor="w")
        tk.Entry(self.advanced_frame, textvariable=self.api_key_var, width=80).pack(fill="x", pady=(4, 8))
        tk.Label(self.advanced_frame, text="Chrome 用户目录").pack(anchor="w")
        tk.Entry(
            self.advanced_frame,
            textvariable=self.browser_user_data_dir_var,
            width=80,
        ).pack(fill="x", pady=(4, 8))

        self.status_label = tk.Label(self.tab_localizer, textvariable=self.status_var, justify="left")
        self.status_label.pack(anchor="w", pady=(4, 8))

    def _build_summary(self) -> None:
        self.progress_summary_pane = tk.Frame(self.main_frame)
        self.progress_summary_pane.pack(fill="x", pady=(0, 10))

        progress_pane = tk.Frame(self.progress_summary_pane)
        progress_pane.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(progress_pane, text="实时进度", anchor="w").pack(anchor="w")
        progress_status_frame = tk.Frame(progress_pane)
        progress_status_frame.pack(fill="x", pady=(2, 4))
        tk.Label(
            progress_status_frame,
            textvariable=self.progress_current_var,
            anchor="w",
            justify="left",
            fg="#0d47a1",
            wraplength=420,
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            progress_status_frame,
            textvariable=self.progress_total_var,
            anchor="e",
            fg="#555",
        ).pack(side="right")

        progress_tree_frame = tk.Frame(progress_pane)
        progress_tree_frame.pack(fill="both", expand=True)
        self.progress_tree = ttk.Treeview(
            progress_tree_frame,
            columns=("time", "step", "elapsed"),
            show="headings",
            selectmode="none",
            height=9,
        )
        self.progress_tree.heading("time", text="时间")
        self.progress_tree.heading("step", text="步骤")
        self.progress_tree.heading("elapsed", text="耗时")
        self.progress_tree.column("time", width=70, anchor="w", stretch=False)
        self.progress_tree.column("step", width=320, anchor="w", stretch=True)
        self.progress_tree.column("elapsed", width=70, anchor="e", stretch=False)
        self.progress_tree.tag_configure("running", background="#e3f2fd")
        progress_scroll = ttk.Scrollbar(progress_tree_frame, orient="vertical", command=self.progress_tree.yview)
        self.progress_tree.configure(yscrollcommand=progress_scroll.set)
        self.progress_tree.pack(side="left", fill="both", expand=True)
        progress_scroll.pack(side="right", fill="y")

        summary_pane = tk.Frame(self.progress_summary_pane)
        summary_pane.pack(side="right", fill="both", expand=True, padx=(8, 0))
        tk.Label(summary_pane, text="运行摘要", anchor="w").pack(anchor="w")
        tk.Frame(summary_pane, height=22).pack(fill="x", pady=(2, 4))
        summary_tree_frame = tk.Frame(summary_pane)
        summary_tree_frame.pack(fill="both", expand=True)
        self.summary_tree = ttk.Treeview(
            summary_tree_frame,
            columns=("item", "value"),
            show="headings",
            selectmode="none",
            height=9,
        )
        self.summary_tree.heading("item", text="项目")
        self.summary_tree.heading("value", text="结果")
        self.summary_tree.column("item", width=140, anchor="w", stretch=False)
        self.summary_tree.column("value", width=320, anchor="w", stretch=True)
        summary_scroll = ttk.Scrollbar(summary_tree_frame, orient="vertical", command=self.summary_tree.yview)
        self.summary_tree.configure(yscrollcommand=summary_scroll.set)
        self.summary_tree.pack(side="left", fill="both", expand=True)
        summary_scroll.pack(side="right", fill="y")

    def _build_log(self) -> None:
        tk.Label(self.main_frame, text="实时日志", anchor="w").pack(anchor="w")
        self.log_widget = tk.Text(self.main_frame, height=10, width=110)
        self.log_widget.pack(fill="both", expand=True, pady=(4, 0))

    def _append_log(self, message: str) -> None:
        self.log_widget.insert("end", f"{message}\n")
        self.log_widget.see("end")

    def _clear_summary(self) -> None:
        for iid in self.summary_tree.get_children():
            self.summary_tree.delete(iid)

    def _add_summary(self, item: str, value: object) -> None:
        self.summary_tree.insert("", "end", values=(item, "" if value is None else str(value)))

    def _refresh_login_tip(self) -> None:
        label = self.login_shopify_tip_label
        full = self._login_shopify_tip_full_text
        avail = label.winfo_width()
        if avail <= 1:
            return
        font = tkfont.Font(font=label.cget("font"))
        if font.measure(full) <= avail:
            if label.cget("text") != full:
                label.configure(text=full)
            return
        ellipsis = "…"
        lo, hi = 0, len(full) - 1
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = full[:mid] + ellipsis
            if font.measure(candidate) <= avail:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        truncated = (full[:best] + ellipsis) if best > 0 else ellipsis
        if label.cget("text") != truncated:
            label.configure(text=truncated)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(int(seconds), 0)
        if total >= 3600:
            hours, remainder = divmod(total, 3600)
            minutes, secs = divmod(remainder, 60)
            return f"{hours}:{minutes:02d}:{secs:02d}"
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _progress_clear(self) -> None:
        if self._progress_tick_after_id is not None:
            try:
                self.root.after_cancel(self._progress_tick_after_id)
            except Exception:
                pass
            self._progress_tick_after_id = None
        for iid in self.progress_tree.get_children():
            self.progress_tree.delete(iid)
        self._progress_current_iid = None
        self._progress_started_at = None
        self._progress_step_started_at = None
        self._progress_running = False
        self.progress_current_var.set("当前：等待启动")
        self.progress_total_var.set("总耗时 00:00")

    def _progress_start(self, message: str) -> None:
        self._progress_clear()
        now = time.monotonic()
        self._progress_started_at = now
        self._progress_running = True
        self._progress_record_step(message, _now=now)
        self._progress_schedule_tick()

    @staticmethod
    def _is_meaningful_step(message: str) -> bool:
        text = (message or "").strip()
        if not text:
            return False
        if text in {"{", "}", "[", "]", "},", "],"}:
            return False
        if text.endswith(",") and text.startswith(("{", "[", '"')):
            return False
        if text.startswith('"') and '"' in text[1:] and ':' in text:
            return False
        first_token = text.split(None, 1)[0] if text else ""
        if first_token.endswith(":") and first_token.startswith('"'):
            return False
        return True

    def _progress_record_step(self, message: str, *, _now: float | None = None) -> None:
        if not self._progress_running:
            return
        if not self._is_meaningful_step(message):
            return
        now = _now if _now is not None else time.monotonic()
        if self._progress_current_iid is not None and self._progress_step_started_at is not None:
            elapsed = self._format_elapsed(now - self._progress_step_started_at)
            try:
                self.progress_tree.set(self._progress_current_iid, "elapsed", elapsed)
                self.progress_tree.item(self._progress_current_iid, tags=())
            except tk.TclError:
                pass
        timestamp = time.strftime("%H:%M:%S")
        iid = self.progress_tree.insert(
            "",
            "end",
            values=(timestamp, message, "00:00"),
            tags=("running",),
        )
        self.progress_tree.see(iid)
        self._progress_current_iid = iid
        self._progress_step_started_at = now
        self.progress_current_var.set(f"当前：{message}")

    def _progress_finish(self, final_message: str | None = None) -> None:
        if not self._progress_running:
            return
        now = time.monotonic()
        if self._progress_current_iid is not None and self._progress_step_started_at is not None:
            elapsed = self._format_elapsed(now - self._progress_step_started_at)
            try:
                self.progress_tree.set(self._progress_current_iid, "elapsed", elapsed)
                self.progress_tree.item(self._progress_current_iid, tags=())
            except tk.TclError:
                pass
        if self._progress_started_at is not None:
            self.progress_total_var.set(
                f"总耗时 {self._format_elapsed(now - self._progress_started_at)}"
            )
        self.progress_current_var.set(f"当前：{final_message}" if final_message else "当前：已结束")
        if self._progress_tick_after_id is not None:
            try:
                self.root.after_cancel(self._progress_tick_after_id)
            except Exception:
                pass
            self._progress_tick_after_id = None
        self._progress_running = False

    def _progress_schedule_tick(self) -> None:
        self._progress_tick_after_id = self.root.after(1000, self._progress_tick)

    def _progress_tick(self) -> None:
        self._progress_tick_after_id = None
        if not self._progress_running:
            return
        now = time.monotonic()
        if self._progress_current_iid is not None and self._progress_step_started_at is not None:
            elapsed = self._format_elapsed(now - self._progress_step_started_at)
            try:
                self.progress_tree.set(self._progress_current_iid, "elapsed", elapsed)
            except tk.TclError:
                return
        if self._progress_started_at is not None:
            self.progress_total_var.set(
                f"总耗时 {self._format_elapsed(now - self._progress_started_at)}"
            )
        self._progress_schedule_tick()

    def toggle_advanced(self) -> None:
        if self.advanced_visible:
            self.advanced_frame.pack_forget()
            self.advanced_button.configure(text="显示高级设置")
            self.advanced_visible = False
            return

        self.advanced_frame.pack(fill="x", pady=(4, 8), before=self.status_label)
        self.advanced_button.configure(text="隐藏高级设置")
        self.advanced_visible = True

    def _build_ai_listing_tab(self) -> None:
        # AI自动上品任务操作区域
        self.ai_action_frame = tk.Frame(self.tab_ai_listing)
        self.ai_action_frame.pack(fill="x", pady=(10, 10))

        self.ai_pull_button = tk.Button(
            self.ai_action_frame,
            text="🔄 拉取就绪任务",
            command=self._load_ai_listing_tasks_async,
            width=18,
            height=2,
            font=("TkDefaultFont", 12, "bold"),
        )
        self.ai_pull_button.pack(side="left", padx=(0, 10))

        self.ai_task_box = ttk.Combobox(
            self.ai_action_frame,
            textvariable=self.ai_task_var,
            state="readonly",
            width=50,
        )
        self.ai_task_box.pack(side="left", padx=(0, 10), ipady=8)
        self.ai_task_box.bind("<<ComboboxSelected>>", lambda _event: self._on_ai_task_selected())

        # 任务详情数据卡片展示
        self.ai_detail_card = tk.LabelFrame(self.tab_ai_listing, text=" 选中的上品任务详情 ", padx=15, pady=15)
        self.ai_detail_card.pack(fill="x", pady=(0, 15))

        # Title / Product Code / Domain / Shopify Product ID
        tk.Label(self.ai_detail_card, text="商品唯一编码 (Product Code)").grid(row=0, column=0, sticky="w", pady=4)
        self.ai_code_entry = tk.Entry(self.ai_detail_card, textvariable=self.ai_product_code_var, width=60, state="readonly")
        self.ai_code_entry.grid(row=0, column=1, sticky="w", padx=10, pady=4)

        tk.Label(self.ai_detail_card, text="英文商品标题 (Title)").grid(row=1, column=0, sticky="w", pady=4)
        self.ai_title_entry = tk.Entry(self.ai_detail_card, textvariable=self.ai_product_title_var, width=60, state="readonly")
        self.ai_title_entry.grid(row=1, column=1, sticky="w", padx=10, pady=4)

        tk.Label(self.ai_detail_card, text="绑定的目标店铺 (Domain)").grid(row=2, column=0, sticky="w", pady=4)
        self.ai_domain_entry = tk.Entry(self.ai_detail_card, textvariable=self.ai_product_domain_var, width=60, state="readonly")
        self.ai_domain_entry.grid(row=2, column=1, sticky="w", padx=10, pady=4)

        tk.Label(self.ai_detail_card, text="新建 Shopify ID").grid(row=3, column=0, sticky="w", pady=4)
        self.ai_shopify_id_entry = tk.Entry(self.ai_detail_card, textvariable=self.ai_shopify_product_id_var, width=60, state="readonly")
        self.ai_shopify_id_entry.grid(row=3, column=1, sticky="w", padx=10, pady=4)

        # 底部执行按钮区域
        self.ai_buttons_frame = tk.Frame(self.tab_ai_listing)
        self.ai_buttons_frame.pack(fill="x", pady=(0, 10))

        self.ai_start_button = tk.Button(
            self.ai_buttons_frame,
            text="🚀 开始全自动 RPA 上架",
            command=self.start_ai_listing_upload,
            width=24,
            height=2,
            font=("TkDefaultFont", 14, "bold"),
            bg="#2e7d32",
            fg="white",
            activebackground="#1b5e20",
            activeforeground="white",
        )
        self.ai_start_button.pack(side="left", padx=(0, 10))

        self.ai_open_backend_button = tk.Button(
            self.ai_buttons_frame,
            text="🛒 访问已上架商品后台",
            command=self.open_ai_shopify_product_backend,
            width=24,
            height=2,
            font=("TkDefaultFont", 12),
        )
        self.ai_open_backend_button.pack(side="left")

    def _load_ai_listing_tasks_async(self) -> None:
        self._append_log("[AI自动上品] 正在从服务器拉取就绪任务...")
        self.status_var.set("正在拉取就绪任务...")
        def worker() -> None:
            try:
                base_url = self.base_url_var.get().strip()
                api_key = self.api_key_var.get().strip()
                res = api_client.fetch_ai_listing_tasks(base_url, api_key)
                tasks = res.get("tasks") or []
                self._ui_after(0, self._set_ai_listing_tasks, tasks)
            except Exception as e:
                self._ui_after(0, self._append_log, f"[AI自动上品] [错误] 拉取就绪任务失败: {e}")
                self._ui_after(0, self.status_var.set, "拉取就绪任务失败")
        threading.Thread(target=worker, daemon=True).start()

    def _set_ai_listing_tasks(self, tasks: list[dict]) -> None:
        self.ai_tasks_list = tasks
        self.ai_task_label_to_id.clear()
        labels = []
        for t in tasks:
            label = f"{t['product_code']} - {t['generated_title'][:40]}"
            self.ai_task_label_to_id[label] = t["id"]
            labels.append(label)
            
        self.ai_task_box.configure(values=labels)
        if labels:
            self.ai_task_box.current(0)
            self._on_ai_task_selected()
            self._append_log(f"[AI自动上品] 已成功拉取 {len(labels)} 个就绪任务")
            self.status_var.set("拉取成功，请选择任务后开始上架")
        else:
            self.ai_task_var.set("")
            self.ai_product_code_var.set("")
            self.ai_product_title_var.set("")
            self.ai_product_domain_var.set("")
            self.ai_shopify_product_id_var.set("")
            self._append_log("[AI自动上品] 目前服务器上没有等待上架的就绪任务")
            self.status_var.set("暂无就绪任务")

    def _on_ai_task_selected(self) -> None:
        label = self.ai_task_var.get()
        task_id = self.ai_task_label_to_id.get(label)
        if not task_id:
            return
        task = next((t for t in self.ai_tasks_list if t["id"] == task_id), None)
        if task:
            self.ai_product_code_var.set(task.get("product_code") or "")
            self.ai_product_title_var.set(task.get("generated_title") or "")
            self.ai_product_domain_var.set(task.get("target_store_domain") or "")
            self.ai_shopify_product_id_var.set("")

    def start_ai_listing_upload(self) -> None:
        label = self.ai_task_var.get()
        task_id = self.ai_task_label_to_id.get(label)
        if not task_id:
            messagebox.showwarning("警告", "请先选择一个上品任务")
            return
            
        domain = self.ai_product_domain_var.get().strip()
        if not domain:
            messagebox.showwarning("警告", "商品绑定的目标店铺域名为空")
            return
            
        self._current_cancel_token = cancellation.CancellationToken()
        self._set_running_state(True, stoppable=True)
        self.status_var.set("正在启动 RPA 全自动上品...")
        self._append_log(f"[AI自动上品] 开始上架任务 ID={task_id}, 店铺={domain}")
        
        self._progress_start("开始全自动上品")
        
        def worker() -> None:
            try:
                base_url = self.base_url_var.get().strip()
                api_key = self.api_key_var.get().strip()
                user_dir = self.browser_user_data_dir_var.get().strip() or settings.DEFAULT_BROWSER_USER_DATA_DIR
                
                shopify_id = ai_listing_uploader.run_ai_listing_upload(
                    base_url=base_url,
                    api_key=api_key,
                    task_id=task_id,
                    user_data_dir=settings.browser_user_data_dir_for_domain(user_dir, domain),
                    domain=domain,
                    log_fn=lambda msg: self._ui_after(0, self._append_log, msg),
                    progress_fn=lambda step: self._ui_after(0, self._progress_record_step, step),
                    cancel_token=self._current_cancel_token,
                )
                
                self._ui_after(0, self._on_ai_upload_completed, shopify_id)
            except Exception as e:
                self._ui_after(0, self._on_ai_upload_failed, str(e))
                
        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_upload_completed(self, shopify_id: str) -> None:
        self.ai_shopify_product_id_var.set(shopify_id)
        self.status_var.set("RPA 上架成功！")
        self._append_log(f"[AI自动上品] 上架成功！Shopify 生成的产品 ID: {shopify_id}")
        self._progress_finish("上架成功")
        self._set_running_state(False)
        messagebox.showinfo("成功", f"商品已成功上架到 Shopify 店铺！\nShopify ID: {shopify_id}")
        self._load_ai_listing_tasks_async()

    def _on_ai_upload_failed(self, error_msg: str) -> None:
        self.status_var.set("上架失败")
        self._append_log(f"[AI自动上品] [错误] 上架失败: {error_msg}")
        self._progress_finish("上架失败")
        self._set_running_state(False)
        messagebox.showerror("上架失败", f"上架过程中遇到错误:\n{error_msg}")

    def open_ai_shopify_product_backend(self) -> None:
        domain = self.ai_product_domain_var.get().strip()
        shopify_id = self.ai_shopify_product_id_var.get().strip()
        if not domain:
            messagebox.showwarning("警告", "店铺域名为空")
            return
        
        store_slug = settings.shopify_store_slug_for_domain(domain)
        if not store_slug:
            store_slug = settings.DEFAULT_SHOPIFY_STORE_SLUG
            
        import webbrowser
        if shopify_id and shopify_id != "SUCCESS_MANUAL_CHECK":
            url = f"https://admin.shopify.com/store/{store_slug}/products/{shopify_id}"
        else:
            url = f"https://admin.shopify.com/store/{store_slug}/products"
            
        self._append_log(f"[AI自动上品] 正在浏览器中打开: {url}")
        webbrowser.open(url)

    def _set_running_state(self, running: bool, *, stoppable: bool = False) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running and stoppable else "disabled")
        self.advanced_button.configure(state=state)
        self.login_shopify_button.configure(state=state)
        self.open_ez_button.configure(state=state)
        self.open_detail_button.configure(state=state)
        self.mapping_button.configure(state=state)
        self.product_code_entry.configure(state=state)
        self.shopify_product_id_entry.configure(state=state)
        self.language_box.configure(state="disabled" if running else "readonly")
        self.batch_select_btn.configure(state=state)
        self.link_check_button.configure(state=state)
        if hasattr(self, "ai_start_button"):
            self.ai_start_button.configure(state=state)
        if hasattr(self, "ai_pull_button"):
            self.ai_pull_button.configure(state=state)
        if not running:
            self._progress_finish()

    def request_stop(self) -> None:
        if self._current_cancel_token is None or self._current_cancel_token.is_cancelled():
            return
        self._current_cancel_token.cancel()
        self.stop_button.configure(state="disabled")
        self.status_var.set("正在停止当前任务")
        self._append_log("已请求停止，当前步骤结束后会退出")

    def close_application(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        if self._current_cancel_token is not None and not self._current_cancel_token.is_cancelled():
            self._current_cancel_token.cancel()
        try:
            self.close_app_button.configure(state="disabled")
        except Exception:
            pass
        try:
            self.stop_button.configure(state="disabled")
            self.status_var.set("正在关闭软件并清理 CDP 浏览器")
            self._append_log("正在关闭软件：取消任务、清理 CDP 浏览器窗口并退出程序")
        except Exception:
            pass

        profiles = self._related_browser_profiles()

        def worker() -> None:
            self._cleanup_related_browser_processes(profiles)
            self._ui_after(0, self._destroy_root)

        threading.Thread(target=worker, daemon=True).start()

    def _related_browser_profiles(self) -> list[str]:
        base_dir = self.browser_user_data_dir_var.get().strip() or settings.DEFAULT_BROWSER_USER_DATA_DIR
        domains: list[str] = []
        for item in self.domain_items or settings.default_domain_items():
            domain = settings.normalize_domain((item or {}).get("domain"))
            if domain:
                domains.append(domain)
        current = settings.normalize_domain(self.current_shopify_domain_var.get())
        if current:
            domains.append(current)
        if settings.DEFAULT_SHOPIFY_DOMAIN not in domains:
            domains.append(settings.DEFAULT_SHOPIFY_DOMAIN)

        profiles: list[str] = []
        seen: set[str] = set()
        for domain in domains:
            profile = settings.browser_user_data_dir_for_domain(base_dir, domain)
            key = profile.lower()
            if key in seen:
                continue
            seen.add(key)
            profiles.append(profile)
        return profiles

    def _cleanup_related_browser_processes(self, profiles: list[str]) -> None:
        try:
            ez_cdp._kill_cdp_chrome_for_port(ez_cdp.DEFAULT_CDP_PORT)
        except Exception:
            pass
        for profile in profiles:
            try:
                session.kill_chrome_for_profile(profile, wait_s=1.0)
            except Exception:
                pass

    def _destroy_root(self) -> None:
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _open_workspace(self) -> None:
        if not self._workspace_root:
            return
        self._open_path(self._workspace_root)

    def _open_download_dir(self) -> None:
        if not self._download_dir:
            return
        self._open_path(self._download_dir)

    def _open_path(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self._append_log(f"打开目录失败：{exc}")

    def _language_label(self, item: dict) -> str:
        code = str(item.get("code") or "").strip().lower()
        label = str(item.get("label") or item.get("name_zh") or code).strip()
        if code and code not in label:
            return f"{label} ({code})"
        return label or code

    def _is_english_language_item(self, item: dict) -> bool:
        code = str(item.get("code") or "").strip().lower().replace("_", "-").replace("/", "-")
        label = str(item.get("label") or item.get("name_zh") or "").strip().lower()
        if code == "en" or code.startswith("en-"):
            return True
        return label in {"english", "英语", "英文"}

    def _set_language_items(self, items: list[dict], fallback: bool = False) -> None:
        mapping: dict[str, str] = {}
        shop_locale_mapping: dict[str, str] = {}
        shopify_name_mapping: dict[str, str] = {}
        labels: list[str] = []
        filtered_items: list[dict] = []
        for item in items:
            if self._is_english_language_item(item):
                continue
            code = str(item.get("code") or "").strip().lower()
            if not code:
                continue
            label = self._language_label(item)
            mapping[label] = code
            shop_locale_mapping[label] = str(item.get("shop_locale") or code).strip()
            shopify_name_mapping[label] = str(item.get("shopify_language_name") or "").strip()
            labels.append(label)
            filtered_items.append(item)

        self.language_items = filtered_items
        self.language_label_to_code = mapping
        self.language_label_to_shop_locale = shop_locale_mapping
        self.language_label_to_shopify_name = shopify_name_mapping
        self.language_box.configure(values=labels)
        if labels and not self.batch_languages:
            self.language_box.current(0)
        elif not labels or self.batch_languages:
            self.language_var.set("")
        if fallback:
            self.status_var.set("语言列表加载失败，请检查 API Key 和网络连接")
            self._append_log("语言列表加载失败，请检查 API Key 和网络连接")
        else:
            self.status_var.set("语言列表已加载，可以开始替换")
            self._append_log(f"已加载 {len(labels)} 个语言选项")

    def _update_batch_languages_display(self) -> None:
        """更新已选择的批量语言显示区域"""
        # 清空现有显示
        for widget in self.batch_languages_frame.winfo_children():
            widget.destroy()

        if not self.batch_languages:
            return

        # 用||分隔显示已选择的语言，单行显示，横向滚动
        display_text = " || ".join(self.batch_languages)

        tk.Label(
            self.batch_languages_frame,
            text=display_text,
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)

    def _remove_batch_language(self, lang_label: str) -> None:
        """从批量选择中移除一个语言"""
        if lang_label in self.batch_languages:
            self.batch_languages.remove(lang_label)
            self._update_batch_languages_display()
            # 如果批量选择为空,恢复单个语言选择
            if not self.batch_languages and self.language_items:
                self.language_box.current(0)

    def _open_batch_language_dialog(self) -> None:
        """打开批量语言选择弹窗"""
        if not self.language_items:
            messagebox.showinfo("提示", "语言列表尚未加载完成，请稍候再试")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("批量选择语言")
        dialog.transient(self.root)
        dialog.geometry("520x560")
        dialog.minsize(420, 440)
        dialog.grab_set()
        self._center_dialog_over_root(dialog)

        # 弹窗内容
        header = tk.Label(
            dialog,
            text="请选择需要批量替换的语言（可多选）",
            anchor="w",
            font=("TkDefaultFont", 11, "bold"),
        )
        header.pack(fill="x", padx=16, pady=(14, 8))

        # 顶部按钮区域
        btn_frame_top = tk.Frame(dialog)
        btn_frame_top.pack(fill="x", padx=16, pady=(0, 8))

        # 先创建check_vars字典
        check_vars: dict[str, tk.BooleanVar] = {}

        # 按钮放右边，按顺序：确认选择 → 取消 → 全选 → 全不选
        btn_frame_right = tk.Frame(btn_frame_top)
        btn_frame_right.pack(side="right")

        # 先定义好确认和取消的函数，后面再绑定
        result = {"confirmed": False}

        def confirm():
            result["confirmed"] = True
            dialog.destroy()

        def cancel():
            dialog.destroy()

        def select_all():
            for var in check_vars.values():
                var.set(True)

        def select_none():
            for var in check_vars.values():
                var.set(False)

        tk.Button(btn_frame_right, text="确认选择", command=confirm, width=10, bg="#1976d2", fg="white").pack(side="left", padx=(0, 4))
        tk.Button(btn_frame_right, text="全选", command=select_all, width=10).pack(side="left", padx=(0, 4))
        tk.Button(btn_frame_right, text="全不选", command=select_none, width=10).pack(side="left", padx=(0, 4))
        tk.Button(btn_frame_right, text="取消", command=cancel, width=10).pack(side="left")

        # 滚动区域放复选框
        canvas = tk.Canvas(dialog, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        checkbox_frame = tk.Frame(canvas)
        checkbox_frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(0, 12))
        scrollbar.pack(side="right", fill="y", padx=(0, 16), pady=(0, 12))

        # 为每个语言创建复选框
        for item in self.language_items:
            lang_label = self._language_label(item)
            var = tk.BooleanVar(value=lang_label in self.batch_languages)
            check_vars[lang_label] = var
            cb = tk.Checkbutton(checkbox_frame, text=lang_label, variable=var, anchor="w")
            cb.pack(fill="x", padx=4, pady=2)

        self.root.wait_window(dialog)

        if result["confirmed"]:
            # 更新批量语言选择
            self.batch_languages = [label for label, var in check_vars.items() if var.get()]
            self._update_batch_languages_display()
            # 如果有批量选择,清空单个语言选择
            if self.batch_languages:
                self.language_var.set("")
            elif self.language_items:
                self.language_box.current(0)

    def _load_languages_async(self) -> None:
        base_url = settings.DEFAULT_BASE_URL
        api_key = self.api_key_var.get().strip()

        def worker() -> None:
            try:
                payload = api_client.fetch_languages(
                    base_url,
                    api_key,
                )
                items = list(payload.get("items") or [])
                self._ui_after(0, self._set_language_items, items, False)
            except Exception as exc:
                self._ui_after(0, self._append_log, f"加载线上语言列表失败：{exc}，请检查 API Key 和网络连接")
                self._ui_after(0, self._set_language_items, [], True)

        threading.Thread(target=worker, daemon=True).start()

    def _update_login_status(self, domain: str | None) -> None:
        """根据当前 domain + 本地 slug 缓存决定状态指示。
        - domain 为空：红字「未登录」
        - domain 有 slug 缓存：黑字「当前网站：{domain}」（启动时也会显示，无需再点已登录）
        - domain 没 slug 缓存：橙黄「待登录：{domain}」（提示要点登录店铺 + 已登录）
        用户随时可以再点「已登录」按钮刷新缓存（防止首次抓错）。"""
        if not domain:
            self.current_login_status_var.set("未登录")
            self.current_login_status_label.configure(fg="red")
            return
        try:
            cached_slug = (
                settings.known_store_slug_for_domain(domain)
                or settings.cached_store_slug_for_domain(domain)
            )
        except Exception:
            cached_slug = ""

        status_parts = []
        status_parts.append(f"当前网站：{domain}")
        if cached_slug:
            status_parts.append(f"当前网站shopify编码：{cached_slug}")
        if self.current_running_language:
            status_parts.append(f"当前替换语言：{self.current_running_language}")

        full_text = "  ".join(status_parts)
        self.current_login_status_var.set(full_text)
        self.current_login_status_label.configure(fg="black" if cached_slug else "#cc7a00")

    def _refresh_login_button_text(self) -> None:
        domain = settings.normalize_domain(self.current_shopify_domain_var.get())
        self.current_shopify_domain_var.set(domain)
        self.login_shopify_button.configure(text="登录店铺")

    def _on_shopify_domain_selected(self) -> None:
        domain = settings.normalize_domain(self.current_shopify_domain_var.get())
        self.current_shopify_domain_var.set(domain)
        if hasattr(self, "domain_box"):
            self.domain_box.set(domain)
        self._refresh_login_button_text()
        self._update_login_status(domain or None)

    def _set_domain_items(self, items: list[dict], fallback: bool = False) -> None:
        normalized_items: list[dict] = []
        seen: set[str] = set()
        for item in items or []:
            domain = settings.normalize_domain((item or {}).get("domain"))
            if domain in seen:
                continue
            seen.add(domain)
            normalized_items.append({**dict(item or {}), "domain": domain})
        if not normalized_items:
            normalized_items = settings.default_domain_items()

        self.domain_items = normalized_items
        domains = [row["domain"] for row in normalized_items]
        current = settings.normalize_domain(self.current_shopify_domain_var.get())
        if current not in domains:
            current = domains[0]
        self.current_shopify_domain_var.set(current)
        if hasattr(self, "domain_box"):
            self.domain_box.configure(values=domains)
            self.domain_box.set(current)
        self._refresh_login_button_text()
        # 域名列表 / current 变了之后同步刷新状态指示，让有缓存 slug 的 domain 显示「当前网站」
        self._update_login_status(current or None)
        if fallback:
            self._append_log("域名列表加载失败，已使用默认域名 newjoyloo.com")
        else:
            self._append_log(f"已加载 {len(domains)} 个域名：{', '.join(domains)}")

    def _load_domains_async(self) -> None:
        base_url = settings.DEFAULT_BASE_URL
        api_key = self.api_key_var.get().strip()

        def worker() -> None:
            try:
                payload = api_client.fetch_domains(
                    base_url,
                    api_key,
                )
                items = list(payload.get("items") or [])
                self._ui_after(0, self._set_domain_items, items, False)
            except Exception as exc:
                self._ui_after(0, self._append_log, f"加载域名列表失败：{exc}")
                self._ui_after(0, self._set_domain_items, settings.default_domain_items(), True)

        threading.Thread(target=worker, daemon=True).start()

    def _choose_shopify_domain(self) -> str:
        items = self.domain_items or settings.default_domain_items()
        domains = [settings.normalize_domain(item.get("domain")) for item in items]
        current = settings.normalize_domain(self.current_shopify_domain_var.get())
        if not domains:
            return current
        if current not in domains:
            current = domains[0]
        self.current_shopify_domain_var.set(current)
        if hasattr(self, "domain_box"):
            self.domain_box.set(current)
        self._update_login_status(current or None)
        return current

    def _prompt_shopify_domain_choice(self, domains: list[str], current: str) -> str:
        dialog = tk.Toplevel(self.root)
        dialog.title("选择 Shopify 店铺域名")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        selected_var = tk.StringVar(value=current if current in domains else domains[0])
        result = {"domain": ""}

        frame = tk.Frame(dialog, padx=16, pady=14)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="选择要登录的店铺域名", anchor="w").pack(anchor="w", pady=(0, 8))
        domain_box = ttk.Combobox(frame, textvariable=selected_var, state="readonly", values=domains, width=36)
        domain_box.pack(fill="x", pady=(0, 12))
        domain_box.focus_set()

        button_frame = tk.Frame(frame)
        button_frame.pack(fill="x")

        def confirm() -> None:
            result["domain"] = settings.normalize_domain(selected_var.get())
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        tk.Button(button_frame, text="取消", command=cancel, width=10).pack(side="right")
        tk.Button(button_frame, text="登录", command=confirm, width=10).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: cancel())
        self._center_dialog_over_root(dialog)
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["domain"]

    def _center_dialog_over_root(self, dialog: "tk.Toplevel") -> None:
        # 让弹窗坐在主窗口的中心，避免 tk 默认把它丢到屏幕左上角 / 多显示器主屏。
        try:
            dialog.update_idletasks()
            self.root.update_idletasks()
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()
            dlg_w = dialog.winfo_reqwidth()
            dlg_h = dialog.winfo_reqheight()
            x = max(root_x + (root_w - dlg_w) // 2, root_x)
            y = max(root_y + (root_h - dlg_h) // 2, root_y)
            dialog.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _selected_lang_code(self, language_label: str) -> str:
        mapped = self.language_label_to_code.get(language_label)
        if mapped:
            return mapped
        if "(" in language_label and language_label.endswith(")"):
            return language_label.rsplit("(", 1)[-1].rstrip(")").strip().lower()
        return language_label.strip().lower()

    def _selected_shopify_language_name(self, language_label: str) -> str:
        return self.language_label_to_shopify_name.get(language_label, "")

    def _selected_shop_locale(self, language_label: str) -> str:
        return self.language_label_to_shop_locale.get(language_label, "")

    def start_run(self) -> None:
        product_code = self.product_code_var.get().strip().lower()
        language_label = self.language_var.get().strip()
        shopify_product_id = self.shopify_product_id_var.get().strip()
        if not product_code:
            messagebox.showerror("错误", "请先输入商品 ID")
            return
        if not language_label and not self.batch_languages:
            messagebox.showerror("错误", "请先选择语言或批量选择语言")
            return
        if shopify_product_id and not shopify_product_id.isdigit():
            messagebox.showerror("错误", "Shopify ID 只能填写数字；不确定时可以留空")
            return

        base_url = settings.DEFAULT_BASE_URL
        self.base_url_var.set(base_url)
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        shopify_domain = settings.normalize_domain(self.current_shopify_domain_var.get())
        if not api_key or not browser_dir:
            messagebox.showerror("错误", "高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空")
            return

        cancel_token = cancellation.CancellationToken()
        self._current_cancel_token = cancel_token
        self._set_running_state(True, stoppable=True)
        self._clear_summary()

        # 单选语言有值时以单选为准；只有单选为空时才使用批量语言。
        use_batch_languages = not language_label and bool(self.batch_languages)
        if use_batch_languages:
            # 批量模式
            lang_codes = [self._selected_lang_code(lbl) for lbl in self.batch_languages]
            self._progress_start(
                f"批量任务已启动：{product_code} / {len(self.batch_languages)} 个语言"
            )
            self.status_var.set("批量任务已启动")
            self._append_log(
                f"开始批量任务：product_code={product_code}, languages={', '.join(lang_codes)}, "
                f"shopify_id={shopify_product_id or '自动识别'}"
            )
            threading.Thread(
                target=self._run_batch,
                args=(
                    base_url,
                    api_key,
                    browser_dir,
                    product_code,
                    self.batch_languages.copy(),
                    shopify_product_id,
                    shopify_domain,
                    cancel_token,
                ),
                daemon=True,
            ).start()
        else:
            # 单个语言模式
            lang_code = self._selected_lang_code(language_label)
            shop_locale = self._selected_shop_locale(language_label)
            shopify_language_name = self._selected_shopify_language_name(language_label)
            self._progress_start(
                f"任务已启动：{product_code} / {lang_code}"
            )
            workspace = storage.create_workspace(product_code, lang_code)
            self._workspace_root = str(workspace.root)
            self._download_dir = str(workspace.source_localized_dir)
            self.open_workspace_button.configure(state="normal")
            self.open_download_button.configure(state="normal")
            self.status_var.set("任务已启动")
            self._append_log(
                f"开始任务：product_code={product_code}, lang={lang_code}, "
                f"shopify_id={shopify_product_id or '自动识别'}"
            )
            threading.Thread(
                target=self._run,
                args=(
                    base_url,
                    api_key,
                    browser_dir,
                    product_code,
                    lang_code,
                    shop_locale,
                    shopify_product_id,
                    shopify_domain,
                    shopify_language_name,
                    cancel_token,
                ),
                daemon=True,
            ).start()

    def open_mapping_management(self) -> None:
        product_code = self.product_code_var.get().strip().lower()
        if not product_code:
            messagebox.showerror("错误", "请先输入商品 ID")
            return

        shopify_domain = settings.normalize_domain(self.current_shopify_domain_var.get())
        self.current_shopify_domain_var.set(shopify_domain)
        self._set_running_state(True)
        self._progress_start(f"正在生成图片映射：{product_code} / {shopify_domain}")
        self.status_var.set("正在生成图片映射")
        self._append_log(f"开始生成图片映射：product_code={product_code}, domain={shopify_domain}")
        threading.Thread(
            target=self._preview_mapping_worker,
            args=(product_code, shopify_domain),
            daemon=True,
        ).start()

    def _preview_mapping_worker(self, product_code: str, shopify_domain: str) -> None:
        try:
            result = controller.preview_domain_image_mapping(
                product_code=product_code,
                shopify_domain=shopify_domain,
            )
            self._ui_after(0, self._render_mapping_preview_result, result)
        except Exception as exc:
            self._ui_after(0, self.status_var.set, "图片映射生成失败")
            self._ui_after(0, self._append_log, f"图片映射生成失败：{exc}")
            self._ui_after(0, messagebox.showerror, "图片映射生成失败", str(exc))
        finally:
            self._ui_after(0, self._set_running_state, False)

    def _render_mapping_preview_result(self, result: dict) -> None:
        summary = result.get("summary") or {}
        self._clear_summary()
        self._add_summary("商品 ID", result.get("product_code"))
        self._add_summary("默认域名", result.get("canonical_domain"))
        self._add_summary("当前域名", result.get("target_domain"))
        if result.get("canonical_product_id"):
            self._add_summary("默认 Shopify ID", result.get("canonical_product_id"))
        if result.get("target_product_id"):
            self._add_summary("当前 Shopify ID", result.get("target_product_id"))
        self._add_summary(
            "轮播图映射",
            f"{summary.get('carousel_mapped_count', 0)} 个，"
            f"低置信度 {summary.get('carousel_low_confidence_count', 0)} 个",
        )
        self._add_summary(
            "详情图映射",
            f"{summary.get('detail_mapped_count', 0)} 个，"
            f"低置信度 {summary.get('detail_low_confidence_count', 0)} 个",
        )

        if result.get("status") == "default_domain":
            status_text = "默认域名无需跨域图片映射"
        else:
            status_text = "图片映射已生成"
        self.status_var.set(status_text)
        self._append_log(
            f"{status_text}：轮播图 {summary.get('carousel_mapped_count', 0)} 个，"
            f"详情图 {summary.get('detail_mapped_count', 0)} 个"
        )
        self._progress_finish(status_text)
        self._show_mapping_management_dialog(result)

    def _mapping_report_text(self, result: dict) -> str:
        summary = result.get("summary") or {}
        lines = [
            f"商品 ID：{result.get('product_code') or '-'}",
            f"默认域名：{result.get('canonical_domain') or '-'}",
            f"当前域名：{result.get('target_domain') or '-'}",
        ]
        if result.get("canonical_product_id"):
            lines.append(f"默认 Shopify ID：{result.get('canonical_product_id')}")
        if result.get("target_product_id"):
            lines.append(f"当前 Shopify ID：{result.get('target_product_id')}")
        message = str(result.get("message") or "").strip()
        if message:
            lines.extend(["", message])
        lines.extend([
            "",
            f"轮播图映射：{summary.get('carousel_mapped_count', 0)} 个",
            f"轮播图低置信度：{summary.get('carousel_low_confidence_count', 0)} 个",
            f"详情图映射：{summary.get('detail_mapped_count', 0)} 个",
            f"详情图低置信度：{summary.get('detail_low_confidence_count', 0)} 个",
        ])

        def append_aliases(title: str, rows: list[dict]) -> None:
            lines.extend(["", title])
            if not rows:
                lines.append("无")
                return
            for row in rows:
                lines.append(
                    f"- 当前位置 {int(row.get('target_index') or 0) + 1} → "
                    f"默认 source_index {row.get('canonical_index')}，"
                    f"{row.get('match_method')} / {row.get('confidence')}"
                )
                lines.append(f"  当前图：{row.get('target_src') or '-'}")
                lines.append(f"  默认图：{row.get('canonical_src') or '-'}")

        append_aliases("轮播图低置信度项", list(summary.get("carousel_low_confidence") or []))
        append_aliases("详情图低置信度项", list(summary.get("detail_low_confidence") or []))
        return "\n".join(lines)

    def _show_mapping_management_dialog(self, result: dict) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("图片映射管理")
        dialog.geometry("780x560")
        dialog.minsize(640, 420)
        dialog.transient(self.root)

        frame = tk.Frame(dialog, padx=16, pady=14)
        frame.pack(fill="both", expand=True)
        title = "图片映射管理"
        tk.Label(frame, text=title, anchor="w", font=("TkDefaultFont", 13, "bold")).pack(fill="x")

        text_frame = tk.Frame(frame)
        text_frame.pack(fill="both", expand=True, pady=(10, 12))
        report = tk.Text(text_frame, wrap="word", height=22)
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=report.yview)
        report.configure(yscrollcommand=scrollbar.set)
        report.insert("1.0", self._mapping_report_text(result))
        report.configure(state="disabled")
        report.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        button_frame = tk.Frame(frame)
        button_frame.pack(fill="x")
        tk.Button(button_frame, text="关闭", command=dialog.destroy, width=10).pack(side="right")
        self._center_dialog_over_root(dialog)

    def open_shopify_target(self, target: str) -> None:
        product_code = self.product_code_var.get().strip().lower()
        language_label = self.language_var.get().strip()
        shopify_product_id = self.shopify_product_id_var.get().strip()
        if not product_code:
            messagebox.showerror("错误", "请先输入商品 ID")
            return
        if not language_label:
            messagebox.showerror("错误", "请先选择语言")
            return
        if shopify_product_id and not shopify_product_id.isdigit():
            messagebox.showerror("错误", "Shopify ID 只能填写数字；不确定时可以留空")
            return

        lang_code = self._selected_lang_code(language_label)
        shop_locale = self._selected_shop_locale(language_label)
        base_url = settings.DEFAULT_BASE_URL
        self.base_url_var.set(base_url)
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        shopify_domain = settings.normalize_domain(self.current_shopify_domain_var.get())
        if not api_key or not browser_dir:
            messagebox.showerror("错误", "高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空")
            return

        target_name = "EZ 页面" if target == "ez" else "详情页"
        self._set_running_state(True)
        self._progress_start(f"正在打开 {target_name}")
        self.status_var.set(f"正在打开 {target_name}")
        self._append_log(
            f"准备打开 {target_name}：product_code={product_code}, lang={lang_code}, "
            f"shopify_id={shopify_product_id or '自动识别'}"
        )
        threading.Thread(
            target=self._open_shopify_target_worker,
            args=(
                target,
                base_url,
                api_key,
                browser_dir,
                product_code,
                lang_code,
                shop_locale,
                shopify_product_id,
                shopify_domain,
            ),
            daemon=True,
        ).start()

    def open_shopify_login(self) -> None:
        base_url = settings.DEFAULT_BASE_URL
        self.base_url_var.set(base_url)
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        shopify_domain = self._choose_shopify_domain()
        if not shopify_domain:
            return
        self.current_shopify_domain_var.set(shopify_domain)
        self._refresh_login_button_text()
        self._update_login_status(shopify_domain)
        if not browser_dir:
            messagebox.showerror("错误", "高级设置里的 Chrome 用户目录不能为空")
            return

        self._set_running_state(True)
        self._progress_start("正在打开 Shopify 产品列表页")
        self.status_var.set("正在打开 Shopify 产品列表页")
        self._append_log("准备打开 Shopify 产品列表页用于店铺登录")
        threading.Thread(
            target=self._open_shopify_login_worker,
            args=(base_url, api_key, browser_dir, shopify_domain),
            daemon=True,
        ).start()

    def _open_shopify_login_worker(
        self,
        base_url: str,
        api_key: str,
        browser_dir: str,
        shopify_domain: str,
    ) -> None:
        try:
            result = controller.open_shopify_login_page(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
                shopify_domain=shopify_domain,
            )
            self._ui_after(0, self._render_login_open_result, result)
        except Exception as exc:
            self._ui_after(0, self.status_var.set, "打开 Shopify 登录页失败")
            self._ui_after(0, self._append_log, f"打开 Shopify 登录页失败：{exc}")
            self._ui_after(0, messagebox.showerror, "打开 Shopify 登录页失败", str(exc))
        finally:
            self._ui_after(0, self._set_running_state, False)

    def _render_login_open_result(self, result: dict) -> None:
        self._clear_summary()
        self._add_summary("已打开页面", "Shopify 主页（请在浏览器里登录后选择目标店铺）")
        self._add_summary("URL", result.get("url"))
        self.status_var.set("已打开 Shopify 主页")
        self._append_log(
            "已打开 Shopify 主页（admin.shopify.com）。请在浏览器里登录账号、点选目标店铺，"
            "登录到对应店铺主页后，回到本程序点「已登录」按钮，程序会从当前浏览器标签页读取真实 slug 并缓存。"
        )
        self._progress_finish("已打开 Shopify 主页")

    def confirm_shopify_login(self) -> None:
        browser_dir = self.browser_user_data_dir_var.get().strip()
        shopify_domain = settings.normalize_domain(self.current_shopify_domain_var.get())
        if not browser_dir:
            messagebox.showerror("错误", "高级设置里的 Chrome 用户目录不能为空")
            return
        self._append_log(f"已登录确认：从当前浏览器标签页读取 {shopify_domain} 的店铺 slug")
        threading.Thread(
            target=self._confirm_shopify_login_worker,
            args=(browser_dir, shopify_domain),
            daemon=True,
        ).start()

    def _confirm_shopify_login_worker(self, browser_dir: str, shopify_domain: str) -> None:
        try:
            result = controller.confirm_shopify_login_capture_slug(
                browser_user_data_dir=browser_dir,
                shopify_domain=shopify_domain,
            )
            self._ui_after(0, self._render_confirm_login_result, result)
        except Exception as exc:
            self._ui_after(0, self._append_log, f"确认登录失败：{exc}")
            self._ui_after(0, messagebox.showerror, "确认登录失败", str(exc))

    def _render_confirm_login_result(self, result: dict) -> None:
        status = (result or {}).get("status")
        domain = (result or {}).get("shopify_domain") or ""
        slug = (result or {}).get("slug") or ""
        url = (result or {}).get("url") or ""
        if status == "captured":
            self._append_log(f"已识别店铺 slug：{domain} → {slug}（来源 URL：{url}），已缓存到本地配置")
            self._update_login_status(domain)
            messagebox.showinfo("已登录", f"已识别 {domain} 的店铺 slug：{slug}\n\n来源 URL：{url}")
        else:
            message = (result or {}).get("message") or "未识别到店铺 slug"
            self._append_log(f"识别失败：{message}（当前浏览器 URL：{url or '(无)'}）")
            self._show_manual_login_url_dialog(result or {})

    def _show_manual_login_url_dialog(self, result: dict) -> None:
        domain = settings.normalize_domain(
            (result or {}).get("shopify_domain") or self.current_shopify_domain_var.get()
        )
        browser_dir = str(
            self.browser_user_data_dir_var.get().strip()
            or (result or {}).get("browser_user_data_dir")
        )
        message = str((result or {}).get("message") or "未识别到店铺 slug")
        current_url = str((result or {}).get("url") or "").strip()

        dialog = tk.Toplevel(self.root)
        dialog.title("未识别到店铺 slug")
        dialog.transient(self.root)
        dialog.minsize(560, 250)

        frame = tk.Frame(dialog, padx=18, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text=message,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(fill="x", anchor="w", pady=(0, 10))

        tk.Label(
            frame,
            text="请把当前浏览器地址栏里的 Shopify 后台 URL 粘贴到这里：",
            anchor="w",
        ).pack(fill="x", anchor="w")

        url_var = tk.StringVar(value=current_url)
        url_entry = tk.Entry(frame, textvariable=url_var, width=90)
        url_entry.pack(fill="x", pady=(6, 8))
        url_entry.focus_set()
        url_entry.selection_range(0, "end")

        status_var = tk.StringVar(value="")
        tk.Label(
            frame,
            textvariable=status_var,
            anchor="w",
            justify="left",
            fg="#c62828",
            wraplength=620,
        ).pack(fill="x", anchor="w", pady=(0, 10))

        button_frame = tk.Frame(frame)
        button_frame.pack(fill="x")

        def cancel() -> None:
            dialog.destroy()

        def save_manual_url() -> None:
            manual_url = url_var.get().strip()
            manual_result = controller.confirm_shopify_login_capture_slug_from_url(
                browser_user_data_dir=browser_dir,
                shopify_domain=domain,
                admin_url=manual_url,
            )
            if manual_result.get("status") == "captured":
                dialog.destroy()
                self._render_confirm_login_result(manual_result)
                return
            status_var.set(manual_result.get("message") or "URL 无法识别，请检查后再保存。")

        tk.Button(button_frame, text="取消", command=cancel, width=10).pack(side="right")
        tk.Button(
            button_frame,
            text="保存 URL",
            command=save_manual_url,
            width=12,
            bg="#1976d2",
            fg="white",
        ).pack(side="right", padx=(0, 8))

        dialog.bind("<Return>", lambda _event: save_manual_url())
        dialog.bind("<Escape>", lambda _event: cancel())
        self._center_dialog_over_root(dialog)
        dialog.grab_set()

    def _open_shopify_target_worker(
        self,
        target: str,
        base_url: str,
        api_key: str,
        browser_dir: str,
        product_code: str,
        lang_code: str,
        shop_locale: str,
        shopify_product_id: str,
        shopify_domain: str,
    ) -> None:
        try:
            result = controller.open_shopify_target(
                target=target,
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
                product_code=product_code,
                lang=lang_code,
                shop_locale=shop_locale,
                shopify_product_id=shopify_product_id,
                shopify_domain=shopify_domain,
            )
            self._ui_after(0, self._render_open_result, result, product_code)
        except Exception as exc:
            self._ui_after(0, self.status_var.set, "打开页面失败")
            self._ui_after(0, self._append_log, f"打开页面失败：{exc}")
            self._ui_after(0, messagebox.showerror, "打开页面失败", str(exc))
        finally:
            self._ui_after(0, self._set_running_state, False)

    def _render_open_result(self, result: dict, product_code: str) -> None:
        target_name = "EZ 页面" if result.get("target") == "ez" else "详情页"
        self._handle_shopify_product_id(result.get("shopify_product_id"))
        self._clear_summary()
        self._add_summary("商品 ID", product_code)
        self._add_summary("语言", result.get("lang"))
        self._add_summary("Shopify ID", result.get("shopify_product_id"))
        self._add_summary("已打开页面", target_name)
        self._add_summary("URL", result.get("url"))
        self.status_var.set(f"已打开 {target_name}")
        self._append_log(f"已打开 {target_name}：{result.get('url')}")
        self._progress_finish(f"已打开 {target_name}")

    def _run(
        self,
        base_url: str,
        api_key: str,
        browser_dir: str,
        product_code: str,
        lang_code: str,
        shop_locale: str,
        shopify_product_id: str,
        shopify_domain: str,
        shopify_language_name: str,
        cancel_token: cancellation.CancellationToken,
    ) -> None:
        # 找到对应的语言标签用于显示
        lang_label = ""
        for lbl, code in self.language_label_to_code.items():
            if code == lang_code:
                lang_label = lbl
                break
        if not lang_label:
            lang_label = lang_code

        try:
            # 设置当前运行语言
            def set_running():
                self.current_running_language = lang_label
                self._update_login_status(shopify_domain)

            self._ui_after(0, set_running)

            result = controller.run_shopify_localizer(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
                product_code=product_code,
                lang=lang_code,
                shop_locale=shop_locale,
                shopify_product_id=shopify_product_id,
                shopify_domain=shopify_domain,
                shopify_language_name=shopify_language_name,
                cancel_token=cancel_token,
                status_cb=lambda message: self._ui_after(0, self._handle_status, message),
                shopify_product_id_cb=lambda product_id: self._ui_after(
                    0,
                    self._handle_shopify_product_id,
                    product_id,
                ),
                visual_pair_confirm_cb=self._confirm_visual_pairs_threadsafe,
            )
            self._ui_after(0, self._render_result, result)
        except cancellation.OperationCancelled:
            self._ui_after(0, self._render_cancelled)
        except Exception as exc:
            self._ui_after(0, self.status_var.set, "执行失败")
            self._ui_after(0, self._append_log, f"================ 任务已结束（执行失败）— 详情请看运行摘要 ================")
            self._ui_after(0, self._append_log, f"失败原因：{exc}")
            self._ui_after(0, self._add_summary, "任务状态", f"执行失败：{exc}")
            self._ui_after(0, messagebox.showerror, "任务失败", f"执行失败：{exc}\n\n详情请看运行摘要")
        finally:
            self._current_cancel_token = None
            self._ui_after(0, self._set_running_state, False)
            # 确保清空当前运行语言
            def clear_running():
                self.current_running_language = ""
                self._update_login_status(shopify_domain)

            self._ui_after(0, clear_running)

    def _run_batch(
        self,
        base_url: str,
        api_key: str,
        browser_dir: str,
        product_code: str,
        language_labels: list[str],
        shopify_product_id: str,
        shopify_domain: str,
        cancel_token: cancellation.CancellationToken,
    ) -> None:
        """批量运行多个语言的替换任务"""
        results = []
        success_count = 0
        failed_count = 0
        first_workspace = None
        first_download_dir = None
        effective_browser_dir = settings.browser_user_data_dir_for_domain(browser_dir, shopify_domain)
        restart_browser_before_next_language = False

        import time

        try:
            for idx, lang_label in enumerate(language_labels, start=1):
                # 检查是否已取消
                if cancel_token.is_cancelled():
                    self._ui_after(0, lambda: self._append_log("批量任务已由用户取消"))
                    break

                if restart_browser_before_next_language:
                    self._ui_after(0, lambda: self._append_log("上一语言执行异常，正在重启浏览器后继续下一语言"))
                    session.kill_chrome_for_profile(effective_browser_dir)
                    restart_browser_before_next_language = False

                lang_code = self._selected_lang_code(lang_label)
                shop_locale = self._selected_shop_locale(lang_label)
                shopify_language_name = self._selected_shopify_language_name(lang_label)

                # 更新当前运行语言显示
                def update_status(lang=lang_label, idx=idx, total=len(language_labels)):
                    self.current_running_language = lang
                    self._update_login_status(shopify_domain)
                    self._progress_record_step(f"[{idx}/{total}] 开始处理语言: {lang}")

                self._ui_after(0, update_status)

                try:
                    # 每个语言跑在独立线程里：Playwright sync API 会在当前
                    # 线程设置 asyncio._running_loop 且从不清理，导致后续
                    # 语言在同一线程调用 sync_playwright() 时报 "Sync API
                    # inside the asyncio loop"。新线程天然隔离该状态。
                    result_container: dict = {}
                    error_container: dict = {}

                    def run_language() -> None:
                        try:
                            result_container["value"] = controller.run_shopify_localizer(
                                base_url=base_url,
                                api_key=api_key,
                                browser_user_data_dir=browser_dir,
                                product_code=product_code,
                                lang=lang_code,
                                shop_locale=shop_locale,
                                shopify_product_id=shopify_product_id,
                                shopify_domain=shopify_domain,
                                shopify_language_name=shopify_language_name,
                                cancel_token=cancel_token,
                                skip_kill_chrome=True,
                                status_cb=lambda msg, lang=lang_label, idx=idx, total=len(language_labels):
                                    self._ui_after(0, lambda: self._handle_status(f"[{idx}/{total}] {msg}")),
                                shopify_product_id_cb=lambda pid: self._ui_after(0, self._handle_shopify_product_id, pid),
                                visual_pair_confirm_cb=self._confirm_visual_pairs_threadsafe,
                            )
                        except Exception as exc:
                            error_container["value"] = exc

                    lang_thread = threading.Thread(target=run_language, daemon=True)
                    lang_thread.start()
                    lang_thread.join()
                    if "value" in error_container:
                        raise error_container["value"]
                    result = result_container["value"]
                    results.append({"language": lang_label, "result": result, "success": True})
                    success_count += 1

                    # 记录第一个任务的工作区
                    if first_workspace is None:
                        first_workspace = result.get("workspace_root") or result.get("workspace")
                        first_download_dir = result.get("download_dir") or ""
                        if not first_download_dir and first_workspace:
                            first_download_dir = str(storage.create_workspace(product_code, lang_code).source_localized_dir)

                    # 更新摘要信息
                    def add_lang_summary(lang=lang_label, res=result):
                        self._add_summary(f"{lang} 状态", "成功")

                    self._ui_after(0, add_lang_summary)

                    # 每个语言之间暂停 5 秒，通过新开 tab 解决切换问题
                    if idx < len(language_labels):
                        time.sleep(5.0)

                except Exception as exc:
                    results.append({"language": lang_label, "error": str(exc), "success": False})
                    failed_count += 1
                    import traceback as _tb
                    detail = f"{lang_label} 执行失败: {exc}\n{_tb.format_exc()}"
                    self._ui_after(0, lambda d=detail: self._append_log(d))
                    # 同时写入工作区日志方便排查
                    try:
                        ws = storage.create_workspace(product_code, lang_code)
                        storage.append_log(ws.log_path, detail)
                    except Exception:
                        pass
                    restart_browser_before_next_language = idx < len(language_labels)
                    # 继续下一个语言,不中断整个批量任务
                    if idx < len(language_labels):
                        time.sleep(5.0)

            # 批量任务完成
            def finish_batch():
                self.current_running_language = ""
                self._update_login_status(shopify_domain)

                # Cache successfully localized links from batch in the session
                for r in results:
                    if r.get("success") and r.get("result"):
                        res = r["result"]
                        l_code = res.get("lang")
                        p_code = res.get("product_code") or product_code
                        dom = res.get("shopify_domain") or shopify_domain
                        if l_code and p_code and dom:
                            url = self.build_product_page_url(dom, l_code, p_code)
                            if url and not any(item["url"] == url for item in self.localized_links):
                                self.localized_links.append({
                                    "lang": l_code,
                                    "domain": dom,
                                    "url": url,
                                    "status": "待检测"
                                })

                if first_workspace:
                    self._workspace_root = first_workspace
                    self.open_workspace_button.configure(state="normal")
                if first_download_dir:
                    self._download_dir = first_download_dir
                    self.open_download_button.configure(state="normal")

                self._add_summary("批量任务状态", f"完成: 成功 {success_count}, 失败 {failed_count}")
                self._add_summary("成功语言", ", ".join([r["language"] for r in results if r["success"]]))
                if failed_count > 0:
                    self._add_summary("失败语言", ", ".join([r["language"] for r in results if not r["success"]]))

                self.status_var.set("批量任务完成")
                self._append_log("================ 批量任务结束 ================")
                self._append_log(f"总计: {len(language_labels)} 个语言, 成功 {success_count}, 失败 {failed_count}")
                self._progress_finish("批量任务完成")
                messagebox.showinfo(
                    "批量任务结束",
                    f"执行完成:\n成功: {success_count}\n失败: {failed_count}",
                    parent=self.root,
                )

            self._ui_after(0, finish_batch)

        except cancellation.OperationCancelled:
            self._ui_after(0, self._render_cancelled)
        except Exception as exc:
            self._ui_after(0, lambda: self._append_log(f"批量任务异常: {exc}"))
        finally:
            self._current_cancel_token = None
            self._ui_after(0, lambda: self._set_running_state(False))
            # 清空当前运行语言
            def clear_running():
                self.current_running_language = ""
                self._update_login_status(shopify_domain)

            self._ui_after(0, clear_running)

    def _render_cancelled(self) -> None:
        self.status_var.set("已停止")
        self.current_running_language = ""
        self._update_login_status(self.current_shopify_domain_var.get())
        self._append_log("================ 任务已结束（用户取消）— 详情请看运行摘要 ================")
        self._add_summary("任务状态", "已停止")
        self._progress_finish("任务已停止")
        messagebox.showinfo("任务结束", "任务已停止，详情请看运行摘要", parent=self.root)

    def _render_result(self, result: dict) -> None:
        self._handle_shopify_product_id(result.get("shopify_product_id"))
        
        # Caching the successfully localized link in the session
        lang_code = result.get("lang")
        product_code = result.get("product_code")
        domain = self.current_shopify_domain_var.get() or result.get("shopify_domain") or settings.DEFAULT_SHOPIFY_DOMAIN
        if lang_code and product_code and domain:
            url = self.build_product_page_url(domain, lang_code, product_code)
            if url:
                if not any(item["url"] == url for item in self.localized_links):
                    self.localized_links.append({
                        "lang": lang_code,
                        "domain": domain,
                        "url": url,
                        "status": "待检测"
                    })

        workspace = str(result.get("workspace_root") or result.get("workspace") or "")
        self._workspace_root = workspace
        download_dir = str(result.get("download_dir") or "")
        if not download_dir and workspace:
            download_dir = str(storage.create_workspace(result.get("product_code"), result.get("lang")).source_localized_dir)
        self._download_dir = download_dir
        if workspace:
            self.open_workspace_button.configure(state="normal")
        if download_dir:
            self.open_download_button.configure(state="normal")

        self._clear_summary()
        self._add_summary("商品 ID", result.get("product_code"))
        self._add_summary("语言", result.get("lang"))
        self._add_summary("Shopify ID", result.get("shopify_product_id"))
        self._add_summary("任务目录", workspace)
        self._add_summary("下载目录", download_dir)
        self._add_summary("结果文件", result.get("manifest_path"))

        carousel = result.get("carousel") or {}
        if carousel:
            carousel_results = list(carousel.get("results") or [])
            failed = len([row for row in carousel_results if row.get("status") not in {"ok", "skipped"}])
            self._add_summary(
                "轮播图",
                f"请求 {carousel.get('requested', 0)}，成功 {carousel.get('ok', 0)}，"
                f"跳过 {carousel.get('skipped', 0)}，失败 {failed}",
            )

        detail = result.get("detail") or {}
        if detail:
            self._add_summary(
                "详情图",
                f"替换 {detail.get('replacement_count', 0)}，"
                f"已存在跳过 {detail.get('skipped_existing_count', 0)}，"
                f"原图兜底 {detail.get('fallback_original_count', 0)}",
            )
            verify = detail.get("verify") or {}
            if verify:
                self._add_summary(
                    "详情验证",
                    f"新图命中 {verify.get('expected_new_urls_present', 0)}/"
                    f"{verify.get('expected_total', 0)}，非 Shopify 图 {verify.get('old_non_shopify_count', 0)}",
                )

        storefront = result.get("storefront") or {}
        if storefront:
            self._add_summary(
                "前台检查",
                f"图片 {storefront.get('image_count', 0)}，"
                f"旧非 Shopify 图 {storefront.get('old_non_shopify_count', 0)}",
            )

        mapping = result.get("domain_image_mapping") or {}
        if mapping.get("target_domain") and mapping.get("target_domain") != mapping.get("canonical_domain"):
            self._add_summary(
                "图片映射",
                f"轮播 {mapping.get('carousel_mapped_count', 0)}，"
                f"详情 {mapping.get('detail_mapped_count', 0)}，"
                f"低置信度 {mapping.get('carousel_low_confidence_count', 0) + mapping.get('detail_low_confidence_count', 0)}",
            )

        # 清空当前运行语言
        self.current_running_language = ""
        self._update_login_status(self.current_shopify_domain_var.get())

        self.status_var.set("执行完成")
        self._append_log("================ 任务已结束（执行完成）— 详情请看运行摘要 ================")
        self._add_summary("任务状态", "已完成")
        self._progress_finish("执行完成")
        messagebox.showinfo("任务结束", "执行完成，详情请看运行摘要", parent=self.root)

    def _handle_status(self, message: str) -> None:
        self.status_var.set(message)
        self._append_log(message)
        self._progress_record_step(message)

    def _handle_shopify_product_id(self, product_id: object) -> None:
        value = str(product_id or "").strip()
        if value:
            self.shopify_product_id_var.set(value)
            self.resolved_shopify_id_label.configure(
                text=f"当前使用: {value}"
            )

    def _confirm_visual_pairs_threadsafe(self, pairs: list[dict]) -> bool:
        done = threading.Event()
        result = {"ok": False}

        def ask() -> None:
            try:
                result["ok"] = self._show_visual_pairs_dialog(pairs)
            except Exception as exc:
                self._append_log(f"视觉兜底确认弹窗失败：{exc}")
                result["ok"] = False
            finally:
                done.set()

        self._ui_after(0, ask)
        while not done.wait(0.1):
            token = self._current_cancel_token
            if token is not None and token.is_cancelled():
                return False
        return bool(result["ok"])

    def _pair_thumbnail(self, parent: tk.Misc, path: str, *, max_size: tuple[int, int] = (180, 130)) -> object | None:
        try:
            from PIL import Image, ImageTk

            image = Image.open(path)
            image.thumbnail(max_size)
            return ImageTk.PhotoImage(image, master=parent)
        except Exception:
            return None

    def _show_visual_pairs_dialog(self, pairs: list[dict]) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("确认视觉兜底配对")
        dialog.geometry("920x620")
        dialog.minsize(760, 520)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog._pair_photos = []  # type: ignore[attr-defined]

        accepted = {"value": False}
        header = tk.Label(
            dialog,
            text=(
                "文件名 token 匹配失效，系统已使用视觉兜底生成配对。"
                "请确认每一行都是“左图替换为右图”，确认后才会继续自动替换。"
            ),
            anchor="w",
            justify="left",
            fg="#b71c1c",
            wraplength=860,
        )
        header.pack(fill="x", padx=16, pady=(14, 8))

        canvas = tk.Canvas(dialog, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas)
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(0, 12))
        scrollbar.pack(side="right", fill="y", padx=(0, 16), pady=(0, 12))

        for index, pair in enumerate(pairs, start=1):
            row = tk.Frame(body, bd=1, relief="solid", padx=10, pady=8)
            row.pack(fill="x", pady=(0, 10))
            slot_index = int(pair.get("slot_index") or 0)
            target_label = "详情图" if pair.get("target_kind") == "detail" else "轮播图"
            slot_score = float(pair.get("slot_score") or 0.0)
            localized_score = float(pair.get("localized_score") or 0.0)
            confidence_label = "需复核" if pair.get("confidence") == "needs_review" else "高可信"
            title = (
                f"{index}. {target_label}位置 {slot_index + 1}  "
                f"{confidence_label}  "
                f"参考图：{pair.get('reference_filename') or '-'}  "
                f"slot={slot_score:.3f}  "
                f"localized={localized_score:.3f}  "
                f"binary={pair.get('binary_similarity', '-')}  "
                f"foreground={pair.get('foreground_overlap', '-')}"
            )
            tk.Label(row, text=title, anchor="w", justify="left").pack(fill="x")

            images = tk.Frame(row)
            images.pack(fill="x", pady=(8, 0))
            current_photo = self._pair_thumbnail(dialog, str(pair.get("current_local_path") or ""))
            replacement_photo = self._pair_thumbnail(dialog, str(pair.get("replacement_local_path") or ""))
            if current_photo is not None:
                dialog._pair_photos.append(current_photo)  # type: ignore[attr-defined]
                tk.Label(images, image=current_photo).grid(row=0, column=0, padx=(0, 12))
            else:
                tk.Label(images, text="当前图预览失败", width=24, height=7).grid(row=0, column=0, padx=(0, 12))
            tk.Label(images, text="→", font=("Arial", 18, "bold")).grid(row=0, column=1, padx=(0, 12))
            if replacement_photo is not None:
                dialog._pair_photos.append(replacement_photo)  # type: ignore[attr-defined]
                tk.Label(images, image=replacement_photo).grid(row=0, column=2, padx=(0, 12))
            else:
                tk.Label(images, text="替换图预览失败", width=24, height=7).grid(row=0, column=2, padx=(0, 12))

            detail_text = (
                f"当前图：{pair.get('current_src') or pair.get('current_local_path')}\n"
                f"替换图：{pair.get('replacement_filename') or pair.get('replacement_local_path')}"
            )
            tk.Label(images, text=detail_text, anchor="w", justify="left", wraplength=420).grid(row=0, column=3, sticky="w")

        footer = tk.Frame(dialog)
        footer.pack(fill="x", padx=16, pady=(0, 14))

        def accept() -> None:
            accepted["value"] = True
            dialog.destroy()

        def reject() -> None:
            accepted["value"] = False
            dialog.destroy()

        tk.Button(footer, text="取消，手动处理", command=reject, width=16).pack(side="right")
        tk.Button(footer, text="确认并继续替换", command=accept, width=18, bg="#1976d2", fg="white").pack(side="right", padx=(0, 10))
        dialog.protocol("WM_DELETE_WINDOW", reject)
        self.root.wait_window(dialog)
        return bool(accepted["value"])

    def build_product_page_url(self, domain: str, lang_code: str, product_code: str) -> str:
        domain = str(domain or "").strip()
        lang_code = str(lang_code or "").strip().lower()
        product_code = str(product_code or "").strip().lower()
        if not domain or not product_code:
            return ""
        
        domain_clean = domain
        if "://" in domain_clean:
            domain_clean = domain_clean.split("://", 1)[1]
        domain_clean = domain_clean.rstrip("/")
        
        # Shopify handles EN/default domain without lang code prefix
        is_en = (lang_code == "en" or lang_code.startswith("en-") or lang_code in {"english", "英语", "英文"})
        if is_en or not lang_code:
            return f"https://{domain_clean}/products/{product_code}"
        else:
            return f"https://{domain_clean}/{lang_code}/products/{product_code}"

    def _language_label_for_code(self, lang_code: str) -> str:
        lang_code = str(lang_code or "").strip().lower()
        for item in self.language_items:
            code = str(item.get("code") or "").strip().lower()
            if code == lang_code:
                return self._language_label(item)
        return lang_code

    def open_link_check_dialog(self) -> None:
        p_code = self.product_code_var.get().strip()
        domain = self.current_shopify_domain_var.get().strip()
        
        # Standalone Link Check input validations
        if not p_code:
            messagebox.showwarning("提示", "请先在主界面中输入商品 ID", parent=self.root)
            return
        if not domain:
            messagebox.showwarning("提示", "请先在主界面中选择店铺域名", parent=self.root)
            return
            
        lang_labels = []
        if self.batch_languages:
            lang_labels = list(self.batch_languages)
        else:
            single_val = self.language_var.get().strip()
            if single_val:
                lang_labels = [single_val]
                
        if not lang_labels:
            messagebox.showwarning("提示", "请先选择单项语言或批量选择语言", parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("小语种链接审计与检测")
        dialog.geometry("850x450")
        dialog.minsize(750, 350)
        dialog.transient(self.root)
        dialog.grab_set()

        title_label = tk.Label(
            dialog,
            text="Shopify 小语种前台商品链接自动审计与检测",
            font=("TkDefaultFont", 14, "bold"),
            fg="#0f4c81"
        )
        title_label.pack(pady=(12, 8))

        # Prep list to show based on the current active inputs in the main window
        items_to_show = []
        for label in lang_labels:
            lang_code = self.language_label_to_code.get(label) or label
            if lang_code:
                url = self.build_product_page_url(domain, lang_code, p_code)
                if url:
                    item = {
                        "lang": lang_code,
                        "domain": domain,
                        "url": url,
                        "status": "待检测"
                    }
                    items_to_show.append(item)
                    if not any(x["url"] == url for x in self.localized_links):
                        self.localized_links.append(item)

        tree_frame = tk.Frame(dialog)
        tree_frame.pack(fill="both", expand=True, padx=16, pady=4)

        tree = ttk.Treeview(
            tree_frame,
            columns=("lang", "domain", "url", "status"),
            show="headings",
            selectmode="browse"
        )
        tree.heading("lang", text="语言")
        tree.heading("domain", text="店铺域名")
        tree.heading("url", text="前台产品链接")
        tree.heading("status", text="检测状态")

        tree.column("lang", width=120, anchor="w", stretch=False)
        tree.column("domain", width=160, anchor="w", stretch=False)
        tree.column("url", width=380, anchor="w", stretch=True)
        tree.column("status", width=100, anchor="center", stretch=False)

        # Style tags
        tree.tag_configure("待检测", foreground="#555555")
        tree.tag_configure("检测中", foreground="#1976d2", font=("TkDefaultFont", 9, "bold"))
        tree.tag_configure("正常", foreground="#2e7d32", font=("TkDefaultFont", 9, "bold"))
        tree.tag_configure("复核", foreground="#ed6c02", font=("TkDefaultFont", 9, "bold"))
        tree.tag_configure("异常", foreground="#d32f2f", font=("TkDefaultFont", 9, "bold"))

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        iid_to_item = {}
        def refresh_tree():
            for item_iid in tree.get_children():
                tree.delete(item_iid)
            iid_to_item.clear()
            for item in items_to_show:
                lang_label = self._language_label_for_code(item["lang"])
                status = item["status"]
                iid = tree.insert(
                    "",
                    "end",
                    values=(lang_label, item["domain"], item["url"], status),
                    tags=(status,)
                )
                iid_to_item[iid] = item

        refresh_tree()

        # Bottom Buttons frame
        btn_frame = tk.Frame(dialog)
        btn_frame.pack(fill="x", padx=16, pady=12)

        def get_selected_item():
            selected = tree.selection()
            if not selected:
                return None
            return iid_to_item.get(selected[0])

        def update_item_status(item, new_status):
            item["status"] = new_status
            refresh_tree()

        def run_single():
            item = get_selected_item()
            if not item:
                messagebox.showwarning("提示", "请先在列表中选中一个要检测的链接", parent=dialog)
                return
            self.run_single_link_check_gui(item, dialog, lambda it, st, res=None: update_item_status(it, st))

        def run_all():
            if not items_to_show:
                messagebox.showinfo("提示", "当前没有可检测的链接", parent=dialog)
                return
            
            btn_single.configure(state="disabled")
            btn_all.configure(state="disabled")
            
            batch_results = []
            
            def run_batch_sequentially(index=0):
                if index >= len(items_to_show):
                    btn_single.configure(state="normal")
                    btn_all.configure(state="normal")
                    
                    if batch_results:
                        try:
                            batch_report_path = self.write_batch_link_check_report(batch_results)
                            if batch_report_path:
                                from link_check_desktop import report as link_check_report
                                link_check_report.open_report(batch_report_path)
                        except Exception as report_exc:
                            self._append_log(f"生成批量检测 HTML 报告失败: {report_exc}")
                            
                    messagebox.showinfo("批量检测完成", "所有小语种链接批量审计与检测完成！\n已自动合并生成批量网页报告并打开。", parent=dialog)
                    return
                
                item = items_to_show[index]
                
                def on_finished(finished_item, final_status, result_dict=None):
                    update_item_status(finished_item, final_status)
                    if result_dict is not None:
                        batch_results.append(result_dict)
                    self._ui_after(1500, lambda: [console_win.destroy(), run_batch_sequentially(index + 1)])

                console_win = self.run_single_link_check_gui(item, dialog, on_finished)
            
            run_batch_sequentially()

        btn_single = tk.Button(btn_frame, text="检测选中链接", command=run_single, width=16, bg="#1976d2", fg="white")
        btn_single.pack(side="left", padx=(0, 10))

        btn_all = tk.Button(btn_frame, text="全部批量检测", command=run_all, width=16, bg="#2e7d32", fg="white")
        btn_all.pack(side="left")

        btn_close = tk.Button(btn_frame, text="关闭", command=dialog.destroy, width=10)
        btn_close.pack(side="right")

    def run_single_link_check_gui(self, item: dict, parent_dialog: tk.Toplevel, on_finish_cb) -> tk.Toplevel:
        console_win = tk.Toplevel(parent_dialog)
        console_win.title(f"链接检测控制台 - {item['url']}")
        console_win.geometry("850x550")
        console_win.transient(parent_dialog)
        console_win.grab_set()

        # Console Header Info
        header_frame = tk.Frame(console_win, bg="#1e293b", padx=12, pady=10)
        header_frame.pack(fill="x")

        lang_label = self._language_label_for_code(item["lang"])
        info_text = f"检测目标: {lang_label} | {item['domain']}\n链接: {item['url']}"
        tk.Label(
            header_frame,
            text=info_text,
            fg="#cbd5e1",
            bg="#1e293b",
            justify="left",
            anchor="w",
            font=("TkDefaultFont", 10, "bold")
        ).pack(side="left")

        # Scrolling terminal console styled beautifully
        term_frame = tk.Frame(console_win)
        term_frame.pack(fill="both", expand=True, padx=12, pady=12)

        term_text = tk.Text(
            term_frame,
            bg="#0f172a",
            fg="#cbd5e1",
            selectbackground="#334155",
            font=("Consolas", 10),
            insertbackground="#ffffff",
            wrap="word",
            state="disabled"
        )
        term_scroll = ttk.Scrollbar(term_frame, orient="vertical", command=term_text.yview)
        term_text.configure(yscrollcommand=term_scroll.set)
        term_text.pack(side="left", fill="both", expand=True)
        term_scroll.pack(side="right", fill="y")

        # Status footer
        footer_frame = tk.Frame(console_win, padx=12, pady=8)
        footer_frame.pack(fill="x", side="bottom")

        close_btn = tk.Button(footer_frame, text="关闭", command=console_win.destroy, width=12, state="disabled")
        close_btn.pack(side="right")

        def append_log(msg: str, tag: str = None) -> None:
            term_text.configure(state="normal")
            if tag:
                if tag == "error":
                    term_text.tag_config("error", foreground="#ef4444")
                elif tag == "success":
                    term_text.tag_config("success", foreground="#10b981", font=("Consolas", 10, "bold"))
                elif tag == "info":
                    term_text.tag_config("info", foreground="#60a5fa")
                
                term_text.insert("end", f"{msg}\n", tag)
            else:
                term_text.insert("end", f"{msg}\n")
            term_text.configure(state="disabled")
            term_text.see("end")

        base_url = self.base_url_var.get().strip()
        api_key = self.api_key_var.get().strip()
        target_url = item["url"]

        on_finish_cb(item, "检测中")
        append_log("[INFO] 正在启动链接检查 background thread...", "info")

        def worker():
            from link_check_desktop import controller as link_check_controller
            from link_check_desktop import report as link_check_report
            
            def thread_status_cb(msg):
                self._ui_after(0, append_log, f"[*] {msg}")

            try:
                result = link_check_controller.run_link_check(
                    base_url=base_url,
                    api_key=api_key,
                    target_url=target_url,
                    status_cb=thread_status_cb
                )
                
                summary = result.get("analysis", {}).get("summary", {})
                decision = str(summary.get("overall_decision", "Review")).strip()
                report_html_path = result.get("report_html_path") or ""
                
                status_mapping = {
                    "Pass": "正常",
                    "Replace": "复核",
                    "Review": "复核",
                }
                final_status = status_mapping.get(decision, "复核")

                self._ui_after(0, append_log, "\n=============================================", "success")
                self._ui_after(0, append_log, f"[SUCCESS] 链接审计成功完成！", "success")
                self._ui_after(0, append_log, f"[+] 产品 ID: {result.get('product', {}).get('id', '')}")
                self._ui_after(0, append_log, f"[+] 目标语言: {result.get('target_language_name', '')} ({result.get('target_language', '')})")
                self._ui_after(0, append_log, f"[+] 整体审计结果: {decision}", "success" if decision == "Pass" else "info")
                self._ui_after(0, append_log, f"[+] 通过判定数: {summary.get('pass_count', 0)}")
                self._ui_after(0, append_log, f"[+] 需替换数: {summary.get('replace_count', 0)}")
                self._ui_after(0, append_log, f"[+] 需复核数: {summary.get('review_count', 0)}")
                self._ui_after(0, append_log, f"[+] 结果报告: {report_html_path}")
                self._ui_after(0, append_log, "=============================================\n", "success")
                
                self._ui_after(0, on_finish_cb, item, final_status, result)

                if report_html_path:
                    try:
                        link_check_report.open_report(report_html_path)
                    except Exception as r_exc:
                        self._ui_after(0, append_log, f"[!] 自动打开报告失败: {r_exc}", "error")

            except Exception as exc:
                friendly = self._friendly_link_check_error(exc)
                self._ui_after(0, append_log, f"\n[ERROR] 链接审计执行失败！", "error")
                self._ui_after(0, append_log, f"[Reason] {friendly}", "error")
                self._ui_after(0, on_finish_cb, item, "异常", None)
            finally:
                self._ui_after(0, close_btn.configure, {"state": "normal"})

        threading.Thread(target=worker, daemon=True).start()
        return console_win

    def write_batch_link_check_report(self, batch_results: list[dict]) -> str | None:
        if not batch_results:
            return None
        
        from pathlib import Path
        workspace_dir = None
        if self._workspace_root:
            workspace_dir = Path(self._workspace_root)
        else:
            first_ws = batch_results[0].get("workspace_root")
            if first_ws:
                workspace_dir = Path(first_ws).parent
                
        if not workspace_dir:
            workspace_dir = Path.home() / ".gemini" / "antigravity"
            
        workspace_dir.mkdir(parents=True, exist_ok=True)
        report_path = workspace_dir / "batch_link_check_report.html"
        
        html_content = self._generate_batch_report_html(batch_results)
        report_path.write_text(html_content, encoding="utf-8")
        return str(report_path)

    def _generate_batch_report_html(self, batch_results: list[dict]) -> str:
        import html
        from pathlib import Path
        
        total_count = len(batch_results)
        pass_count = sum(1 for r in batch_results if r.get("analysis", {}).get("summary", {}).get("overall_decision") == "Pass")
        review_count = sum(1 for r in batch_results if r.get("analysis", {}).get("summary", {}).get("overall_decision") in {"Review", "Replace"})
        
        pass_rate = f"{pass_count / total_count * 100:.1f}%" if total_count > 0 else "0.0%"
        
        rows_html = ""
        for idx, result in enumerate(batch_results, start=1):
            product_id = result.get("product", {}).get("id") or "-"
            lang_name = f"{result.get('target_language_name', '')} ({result.get('target_language', '')})"
            domain = result.get("normalized_url", "").split("://", 1)[-1].split("/", 1)[0]
            summary = result.get("analysis", {}).get("summary") or {}
            decision = str(summary.get("overall_decision") or "Review").strip()
            
            badge_class = "success" if decision == "Pass" else "warning"
            badge_label = "合格 (Pass)" if decision == "Pass" else f"需复核 ({decision})"
            
            pass_imgs = summary.get("pass_count", 0)
            replace_imgs = summary.get("replace_count", 0)
            review_imgs = summary.get("review_count", 0)
            total_imgs = pass_imgs + replace_imgs + review_imgs
            
            img_pass_rate = f"{pass_imgs / total_imgs * 100:.1f}%" if total_imgs > 0 else "0.0%"
            
            single_report = result.get("report_html_path") or ""
            report_link = ""
            if single_report:
                report_uri = Path(single_report).as_uri()
                report_link = f'<a href="{report_uri}" class="report-btn" target="_blank">查看单项报告</a>'
            else:
                report_link = '<span class="no-report">未生成</span>'
                
            rows_html += f"""
            <tr>
                <td>{idx}</td>
                <td class="mono">{html.escape(product_id)}</td>
                <td>{html.escape(lang_name)}</td>
                <td>{html.escape(domain)}</td>
                <td><span class="badge {badge_class}">{html.escape(badge_label)}</span></td>
                <td><strong>{img_pass_rate}</strong> <small class="text-muted">({pass_imgs}/{total_imgs})</small></td>
                <td><span class="text-danger">{replace_imgs} 处需替换</span> / <span class="text-warning">{review_imgs} 处需复核</span></td>
                <td>{report_link}</td>
            </tr>
            """
            
        css_style = """
        :root {
          --primary: #0f4c81;
          --primary-dark: #0b3a63;
          --accent: #2563eb;
          --bg: #f8fafc;
          --panel: #ffffff;
          --border: #e2e8f0;
          --text: #1e293b;
          --text-muted: #64748b;
          --success: #10b981;
          --success-bg: #ecfdf5;
          --warning: #f59e0b;
          --warning-bg: #fffbeb;
          --danger: #ef4444;
          --danger-bg: #fef2f2;
        }
        
        body {
          margin: 0;
          background-color: var(--bg);
          color: var(--text);
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
          line-height: 1.5;
        }
        
        .container {
          max-width: 1200px;
          margin: 0 auto;
          padding: 40px 20px;
        }
        
        header {
          background: linear-gradient(135deg, var(--primary), var(--primary-dark));
          color: #ffffff;
          padding: 30px;
          border-radius: 16px;
          margin-bottom: 30px;
          box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        
        header h1 {
          margin: 0 0 10px 0;
          font-size: 28px;
          font-weight: 700;
        }
        
        header p {
          margin: 0;
          opacity: 0.9;
          font-size: 14px;
        }
        
        .stats-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 20px;
          margin-bottom: 30px;
        }
        
        .stat-card {
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 20px;
          box-shadow: 0 1px 3px rgba(0,0,0,0.05);
          text-align: center;
        }
        
        .stat-card h3 {
          margin: 0 0 8px 0;
          color: var(--text-muted);
          font-size: 14px;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        
        .stat-card .value {
          font-size: 28px;
          font-weight: 700;
          color: var(--primary);
        }
        
        .stat-card .value.success {
          color: var(--success);
        }
        
        .stat-card .value.warning {
          color: var(--warning);
        }
        
        .panel {
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 24px;
          box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.02);
        }
        
        .panel h2 {
          margin: 0 0 20px 0;
          font-size: 20px;
          color: var(--primary);
        }
        
        table {
          width: 100%;
          border-collapse: collapse;
          text-align: left;
        }
        
        th {
          background-color: #f1f5f9;
          color: var(--text-muted);
          font-weight: 600;
          padding: 14px 16px;
          font-size: 13px;
          text-transform: uppercase;
          border-bottom: 2px solid var(--border);
        }
        
        td {
          padding: 16px;
          border-bottom: 1px solid var(--border);
          font-size: 14px;
        }
        
        tr:hover {
          background-color: #f8fafc;
        }
        
        .mono {
          font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
          font-size: 13px;
        }
        
        .badge {
          display: inline-flex;
          align-items: center;
          padding: 4px 10px;
          border-radius: 9999px;
          font-size: 12px;
          font-weight: 600;
        }
        
        .badge.success {
          background-color: var(--success-bg);
          color: var(--success);
        }
        
        .badge.warning {
          background-color: var(--warning-bg);
          color: var(--warning);
        }
        
        .text-muted {
          color: var(--text-muted);
        }
        
        .text-danger {
          color: var(--danger);
          font-weight: 600;
        }
        
        .text-warning {
          color: var(--warning);
          font-weight: 600;
        }
        
        .report-btn {
          display: inline-block;
          background-color: var(--accent);
          color: #ffffff;
          padding: 6px 12px;
          border-radius: 8px;
          text-decoration: none;
          font-size: 12px;
          font-weight: 600;
          transition: background-color 0.2s;
        }
        
        .report-btn:hover {
          background-color: #1d4ed8;
        }
        
        .no-report {
          color: var(--text-muted);
          font-style: italic;
        }
        """
        
        return f"""<!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>Shopify 小语种前台链接批量审计报告</title>
          <style>{css_style}</style>
        </head>
        <body>
          <div class="container">
            <header>
              <h1>Shopify 小语种前台链接批量审计报告</h1>
              <p>本页由 Shopify Image Localizer GUI 客户端在批量链接审计完成后自动合并生成。</p>
            </header>
            
            <div class="stats-grid">
              <div class="stat-card">
                <h3>总审计链接数</h3>
                <div class="value">{total_count}</div>
              </div>
              <div class="stat-card">
                <h3>合格链接 (Pass)</h3>
                <div class="value success">{pass_count}</div>
              </div>
              <div class="stat-card">
                <h3>需复核链接 (Review)</h3>
                <div class="value warning">{review_count}</div>
              </div>
              <div class="stat-card">
                <h3>批量整体合格率</h3>
                <div class="value success">{pass_rate}</div>
              </div>
            </div>
            
            <div class="panel">
              <h2>审计详情列表</h2>
              <table>
                <thead>
                  <tr>
                    <th>序号</th>
                    <th>产品 ID</th>
                    <th>目标语言</th>
                    <th>店铺域名</th>
                    <th>整体判定</th>
                    <th>图片匹配率</th>
                    <th>差异详情</th>
                    <th>单项详细报告</th>
                  </tr>
                </thead>
                <tbody>
                  {rows_html}
                </tbody>
              </table>
            </div>
          </div>
        </body>
        </html>
        """
