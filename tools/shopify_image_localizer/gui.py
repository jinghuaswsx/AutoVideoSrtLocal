from __future__ import annotations

import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from tools.shopify_image_localizer import api_client, cancellation, controller, settings, storage, version


FALLBACK_LANGUAGES = [
    {"code": "it", "label": "意大利语", "shopify_language_name": "Italian"},
    {"code": "es", "label": "西班牙语", "shopify_language_name": "Spanish"},
    {"code": "ja", "label": "日语", "shopify_language_name": "Japanese"},
    {"code": "de", "label": "德语", "shopify_language_name": "German"},
    {"code": "fr", "label": "法语", "shopify_language_name": "French"},
]


class ShopifyImageLocalizerApp:
    def __init__(self, *, prompt_on_start: bool = True) -> None:
        runtime_config = settings.load_runtime_config()

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
        self.product_code_var = tk.StringVar()
        self.shopify_product_id_var = tk.StringVar()
        self.language_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请输入商品 ID，选择语言后开始")
        self.advanced_visible = False
        self.language_items: list[dict] = []
        self.language_label_to_code: dict[str, str] = {}
        self.language_label_to_shop_locale: dict[str, str] = {}
        self.language_label_to_shopify_name: dict[str, str] = {}
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

        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=16, pady=16)

        self._build_form()
        self._build_summary()
        self._build_log()

        self._append_log("程序已启动，正在加载线上语言列表")
        self._load_languages_async()
        _ = prompt_on_start

    def _build_form(self) -> None:
        self.login_shopify_frame = tk.Frame(self.main_frame)
        self.login_shopify_frame.pack(fill="x", pady=(0, 10))
        self.login_shopify_button = tk.Button(
            self.login_shopify_frame,
            text="登录shopify店铺",
            command=lambda: self.open_shopify_login(),
            width=24,
            height=2,
        )
        self.login_shopify_button.pack(side="left")
        self._login_shopify_tip_full_text = "第一次用或者店铺登录掉线，先点左侧按钮"
        self.login_shopify_tip_label = tk.Label(
            self.login_shopify_frame,
            text=self._login_shopify_tip_full_text,
            justify="left",
            anchor="w",
            fg="red",
            font=("TkDefaultFont", 27, "bold"),
        )
        self.login_shopify_tip_label.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self.login_shopify_tip_label.bind(
            "<Configure>",
            lambda _event: self._refresh_login_tip(),
        )

        tk.Label(self.main_frame, text="商品 ID").pack(anchor="w")
        self.product_code_entry = tk.Entry(self.main_frame, textvariable=self.product_code_var, width=80)
        self.product_code_entry.pack(fill="x", pady=(4, 10))
        self.product_code_entry.focus_set()

        tk.Label(self.main_frame, text="语言").pack(anchor="w")
        self.language_box = ttk.Combobox(
            self.main_frame,
            textvariable=self.language_var,
            state="readonly",
            values=[],
        )
        self.language_box.pack(fill="x", pady=(4, 10))

        tk.Label(self.main_frame, text="Shopify ID（可选）").pack(anchor="w")
        self.shopify_product_id_entry = tk.Entry(
            self.main_frame,
            textvariable=self.shopify_product_id_var,
            width=80,
        )
        self.shopify_product_id_entry.pack(fill="x", pady=(4, 6))

        self.tip_label = tk.Label(
            self.main_frame,
            text=(
                "Shopify ID 留空时会自动从线上商品页识别；填写后会优先使用该值，"
                "并随 bootstrap 请求一起发送，绕过服务端未填写 Shopify ID 的阻塞。"
            ),
            justify="left",
            fg="#555",
            wraplength=860,
        )
        self.tip_label.pack(anchor="w", pady=(0, 10))

        self.action_frame = tk.Frame(self.main_frame)
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
        self.open_download_button = tk.Button(
            self.action_frame,
            text="打开下载目录",
            command=self._open_download_dir,
            state="disabled",
            width=16,
        )
        self.open_download_button.pack(side="left", padx=(8, 0))
        self.advanced_button = tk.Button(
            self.action_frame,
            text="显示高级设置",
            command=self.toggle_advanced,
            width=14,
        )
        self.advanced_button.pack(side="right")

        self.advanced_frame = tk.Frame(self.main_frame)
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

        self.status_label = tk.Label(self.main_frame, textvariable=self.status_var, justify="left")
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

    def _set_running_state(self, running: bool, *, stoppable: bool = False) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running and stoppable else "disabled")
        self.advanced_button.configure(state=state)
        self.login_shopify_button.configure(state=state)
        self.open_ez_button.configure(state=state)
        self.open_detail_button.configure(state=state)
        self.product_code_entry.configure(state=state)
        self.shopify_product_id_entry.configure(state=state)
        self.language_box.configure(state="disabled" if running else "readonly")
        if not running:
            self._progress_finish()

    def request_stop(self) -> None:
        if self._current_cancel_token is None or self._current_cancel_token.is_cancelled():
            return
        self._current_cancel_token.cancel()
        self.stop_button.configure(state="disabled")
        self.status_var.set("正在停止当前任务")
        self._append_log("已请求停止，当前步骤结束后会退出")

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
        if labels:
            self.language_box.current(0)
        else:
            self.language_var.set("")
        if fallback:
            self.status_var.set("线上语言列表加载失败，已使用内置常用语言")
            self._append_log("线上语言列表加载失败，已使用内置常用语言")
        else:
            self.status_var.set("语言列表已加载，可以开始替换")
            self._append_log(f"已加载 {len(labels)} 个语言选项")

    def _load_languages_async(self) -> None:
        def worker() -> None:
            try:
                payload = api_client.fetch_languages(
                    settings.DEFAULT_BASE_URL,
                    self.api_key_var.get().strip(),
                )
                items = list(payload.get("items") or [])
                self.root.after(0, self._set_language_items, items, False)
            except Exception as exc:
                self.root.after(0, self._append_log, f"加载线上语言列表失败：{exc}")
                self.root.after(0, self._set_language_items, FALLBACK_LANGUAGES, True)

        threading.Thread(target=worker, daemon=True).start()

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
        if not language_label:
            messagebox.showerror("错误", "请先选择语言")
            return
        if shopify_product_id and not shopify_product_id.isdigit():
            messagebox.showerror("错误", "Shopify ID 只能填写数字；不确定时可以留空")
            return

        lang_code = self._selected_lang_code(language_label)
        shop_locale = self._selected_shop_locale(language_label)
        shopify_language_name = self._selected_shopify_language_name(language_label)
        base_url = settings.DEFAULT_BASE_URL
        self.base_url_var.set(base_url)
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        if not api_key or not browser_dir:
            messagebox.showerror("错误", "高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空")
            return

        cancel_token = cancellation.CancellationToken()
        self._current_cancel_token = cancel_token
        self._set_running_state(True, stoppable=True)
        self._clear_summary()
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
                shopify_language_name,
                cancel_token,
            ),
            daemon=True,
        ).start()

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
            args=(target, base_url, api_key, browser_dir, product_code, lang_code, shop_locale, shopify_product_id),
            daemon=True,
        ).start()

    def open_shopify_login(self) -> None:
        base_url = settings.DEFAULT_BASE_URL
        self.base_url_var.set(base_url)
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        if not browser_dir:
            messagebox.showerror("错误", "高级设置里的 Chrome 用户目录不能为空")
            return

        self._set_running_state(True)
        self._progress_start("正在打开 Shopify 产品列表页")
        self.status_var.set("正在打开 Shopify 产品列表页")
        self._append_log("准备打开 Shopify 产品列表页用于店铺登录")
        threading.Thread(
            target=self._open_shopify_login_worker,
            args=(base_url, api_key, browser_dir),
            daemon=True,
        ).start()

    def _open_shopify_login_worker(
        self,
        base_url: str,
        api_key: str,
        browser_dir: str,
    ) -> None:
        try:
            result = controller.open_shopify_login_page(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
            )
            self.root.after(0, self._render_login_open_result, result)
        except Exception as exc:
            self.root.after(0, self.status_var.set, "打开 Shopify 登录页失败")
            self.root.after(0, self._append_log, f"打开 Shopify 登录页失败：{exc}")
            self.root.after(0, messagebox.showerror, "打开 Shopify 登录页失败", str(exc))
        finally:
            self.root.after(0, self._set_running_state, False)

    def _render_login_open_result(self, result: dict) -> None:
        self._clear_summary()
        self._add_summary("已打开页面", "Shopify 产品列表页")
        self._add_summary("URL", result.get("url"))
        self.status_var.set("已打开 Shopify 产品列表页")
        self._append_log(f"已打开 Shopify 产品列表页，请在浏览器里手动登录店铺：{result.get('url')}")
        self._progress_finish("已打开 Shopify 产品列表页")

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
            )
            self.root.after(0, self._render_open_result, result, product_code)
        except Exception as exc:
            self.root.after(0, self.status_var.set, "打开页面失败")
            self.root.after(0, self._append_log, f"打开页面失败：{exc}")
            self.root.after(0, messagebox.showerror, "打开页面失败", str(exc))
        finally:
            self.root.after(0, self._set_running_state, False)

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
        shopify_language_name: str,
        cancel_token: cancellation.CancellationToken,
    ) -> None:
        try:
            result = controller.run_shopify_localizer(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
                product_code=product_code,
                lang=lang_code,
                shop_locale=shop_locale,
                shopify_product_id=shopify_product_id,
                shopify_language_name=shopify_language_name,
                cancel_token=cancel_token,
                status_cb=lambda message: self.root.after(0, self._handle_status, message),
                shopify_product_id_cb=lambda product_id: self.root.after(
                    0,
                    self._handle_shopify_product_id,
                    product_id,
                ),
                visual_pair_confirm_cb=self._confirm_visual_pairs_threadsafe,
            )
            self.root.after(0, self._render_result, result)
        except cancellation.OperationCancelled:
            self.root.after(0, self._render_cancelled)
        except Exception as exc:
            self.root.after(0, self.status_var.set, "执行失败")
            self.root.after(0, self._append_log, f"================ 任务已结束（执行失败）— 详情请看运行摘要 ================")
            self.root.after(0, self._append_log, f"失败原因：{exc}")
            self.root.after(0, self._add_summary, "任务状态", f"执行失败：{exc}")
            self.root.after(0, messagebox.showerror, "任务失败", f"执行失败：{exc}\n\n详情请看运行摘要")
        finally:
            self._current_cancel_token = None
            self.root.after(0, self._set_running_state, False)

    def _render_cancelled(self) -> None:
        self.status_var.set("已停止")
        self._append_log("================ 任务已结束（用户取消）— 详情请看运行摘要 ================")
        self._add_summary("任务状态", "已停止")
        self._progress_finish("任务已停止")
        messagebox.showinfo("任务结束", "任务已停止，详情请看运行摘要", parent=self.root)

    def _render_result(self, result: dict) -> None:
        self._handle_shopify_product_id(result.get("shopify_product_id"))
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

        self.root.after(0, ask)
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
