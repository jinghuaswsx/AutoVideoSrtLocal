from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from tools.shopify_image_localizer import api_client, controller, settings


class ShopifyImageLocalizerApp:
    def __init__(self, *, prompt_on_start: bool = True) -> None:
        runtime_config = settings.load_runtime_config()

        self.root = tk.Tk()
        self.root.title("Shopify 图片本地化替换")
        self.root.geometry("800x800")
        self.root.resizable(False, False)

        self.base_url_var = tk.StringVar(value=runtime_config["base_url"])
        self.api_key_var = tk.StringVar(value=runtime_config["api_key"])
        self.browser_user_data_dir_var = tk.StringVar(value=runtime_config["browser_user_data_dir"])
        self.product_code_var = tk.StringVar()
        self.language_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请输入产品 ID，选择语言后点击开始替换图片")
        self.advanced_visible = False
        self.language_items: list[dict] = []
        self.language_label_to_code: dict[str, str] = {}

        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(self.main_frame, text="产品 ID").pack(anchor="w")
        self.product_code_entry = tk.Entry(self.main_frame, textvariable=self.product_code_var, width=72)
        self.product_code_entry.pack(fill="x", pady=(4, 8))
        self.product_code_entry.focus_set()

        tk.Label(self.main_frame, text="目标语言").pack(anchor="w")
        self.language_box = ttk.Combobox(
            self.main_frame,
            textvariable=self.language_var,
            state="readonly",
            values=[],
        )
        self.language_box.pack(fill="x", pady=(4, 8))

        self.action_frame = tk.Frame(self.main_frame)
        self.action_frame.pack(fill="x", pady=(8, 8))
        self.start_button = tk.Button(
            self.action_frame,
            text="开始替换图片",
            command=self.start_run,
        )
        self.start_button.pack(side="left")
        self.advanced_button = tk.Button(
            self.action_frame,
            text="显示高级设置",
            command=self.toggle_advanced,
        )
        self.advanced_button.pack(side="right")

        self.advanced_frame = tk.Frame(self.main_frame)
        tk.Label(self.advanced_frame, text="服务端 API 地址").pack(anchor="w")
        tk.Entry(self.advanced_frame, textvariable=self.base_url_var, width=72).pack(fill="x", pady=(4, 8))
        tk.Label(self.advanced_frame, text="OpenAPI Key").pack(anchor="w")
        tk.Entry(self.advanced_frame, textvariable=self.api_key_var, width=72).pack(fill="x", pady=(4, 8))
        tk.Label(self.advanced_frame, text="浏览器用户目录").pack(anchor="w")
        tk.Entry(
            self.advanced_frame,
            textvariable=self.browser_user_data_dir_var,
            width=72,
        ).pack(fill="x", pady=(4, 8))

        tk.Label(self.main_frame, textvariable=self.status_var, justify="left").pack(anchor="w", pady=(8, 8))
        self.log_widget = tk.Text(self.main_frame, height=30, width=96)
        self.log_widget.pack(fill="both", expand=True)

        self._append_log("程序已启动，正在加载语言列表")
        self._load_languages_async()
        _ = prompt_on_start

    def _append_log(self, message: str) -> None:
        self.log_widget.insert("end", f"{message}\n")
        self.log_widget.see("end")

    def toggle_advanced(self) -> None:
        if self.advanced_visible:
            self.advanced_frame.pack_forget()
            self.advanced_button.configure(text="显示高级设置")
            self.advanced_visible = False
            return

        self.advanced_frame.pack(fill="x", pady=(8, 8))
        self.advanced_button.configure(text="隐藏高级设置")
        self.advanced_visible = True

    def _set_running_state(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.advanced_button.configure(state=state)
        self.language_box.configure(state="disabled" if running else "readonly")

    def _load_languages_async(self) -> None:
        def worker() -> None:
            try:
                payload = api_client.fetch_languages(
                    self.base_url_var.get().strip(),
                    self.api_key_var.get().strip(),
                )
                items = list(payload.get("items") or [])
                mapping = {
                    str(item.get("label") or ""): str(item.get("code") or "").strip().lower()
                    for item in items
                    if item.get("label") and item.get("code")
                }
                labels = list(mapping.keys())

                def update_ui() -> None:
                    self.language_items = items
                    self.language_label_to_code = mapping
                    self.language_box.configure(values=labels)
                    if labels:
                        self.language_box.current(0)
                    self.status_var.set("语言列表已加载，可以开始替换图片")
                    self._append_log(f"已加载 {len(labels)} 个语言选项")

                self.root.after(0, update_ui)
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"加载语言失败: {exc}")
                self.root.after(0, self._append_log, f"加载语言失败: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def start_run(self) -> None:
        product_code = self.product_code_var.get().strip()
        language_label = self.language_var.get().strip()
        if not product_code:
            messagebox.showerror("错误", "请先输入产品 ID")
            return
        if not language_label:
            messagebox.showerror("错误", "请先选择目标语言")
            return

        lang_code = self.language_label_to_code.get(language_label, language_label.strip().lower())
        base_url = self.base_url_var.get().strip()
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        if not base_url or not api_key or not browser_dir:
            messagebox.showerror("错误", "高级设置里的 API 地址、Key、浏览器目录不能为空")
            return

        settings.save_runtime_config(
            base_url=base_url,
            api_key=api_key,
            browser_user_data_dir=browser_dir,
        )
        self._set_running_state(True)
        self.status_var.set("任务已启动")
        self._append_log(f"开始任务: product_code={product_code}, lang={lang_code}")
        threading.Thread(
            target=self._run,
            args=(base_url, api_key, browser_dir, product_code, lang_code),
            daemon=True,
        ).start()

    def _run(
        self,
        base_url: str,
        api_key: str,
        browser_dir: str,
        product_code: str,
        lang_code: str,
    ) -> None:
        try:
            result = controller.run_shopify_localizer(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
                product_code=product_code,
                lang=lang_code,
                status_cb=lambda message: self.root.after(0, self._handle_status, message),
            )
            summary = (
                f"执行完成，状态: {result.get('status', '')}\n"
                f"模式: {result.get('mode', '')}\n"
                f"目录: {result.get('workspace_root', '')}"
            )
            self.root.after(0, self.status_var.set, "执行完成")
            self.root.after(0, self._append_log, summary)
        except Exception as exc:
            self.root.after(0, self.status_var.set, "执行失败")
            self.root.after(0, self._append_log, f"执行失败: {exc}")
            self.root.after(0, messagebox.showerror, "执行失败", str(exc))
        finally:
            self.root.after(0, self._set_running_state, False)

    def _handle_status(self, message: str) -> None:
        self.status_var.set(message)
        self._append_log(message)
