from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from tools.shopify_image_localizer import api_client, cancellation, controller, settings


FALLBACK_LANGUAGES = [
    {"code": "it", "label": "意大利语"},
    {"code": "es", "label": "西班牙语"},
    {"code": "ja", "label": "日语"},
    {"code": "de", "label": "德语"},
    {"code": "fr", "label": "法语"},
]


class ShopifyImageLocalizerApp:
    def __init__(self, *, prompt_on_start: bool = True) -> None:
        runtime_config = settings.load_runtime_config()

        self.root = tk.Tk()
        self.root.title("Shopify 图片本地化替换")
        self.root.geometry("920x760")
        self.root.minsize(780, 620)
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
        self._workspace_root = ""
        self._current_cancel_token: cancellation.CancellationToken | None = None

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
            command=lambda: self.open_shopify_target("detail"),
            width=24,
            height=2,
        )
        self.login_shopify_button.pack(side="left")
        self.login_shopify_tip_label = tk.Label(
            self.login_shopify_frame,
            text="第一次使用或者店铺登录状态掉线，先从这里登录店铺，再操作后续",
            justify="left",
            fg="red",
            wraplength=620,
        )
        self.login_shopify_tip_label.pack(side="left", padx=(12, 0))

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
        tk.Label(self.main_frame, text="运行摘要", anchor="w").pack(anchor="w")
        self.summary_tree = ttk.Treeview(
            self.main_frame,
            columns=("item", "value"),
            show="headings",
            selectmode="none",
            height=9,
        )
        self.summary_tree.heading("item", text="项目")
        self.summary_tree.heading("value", text="结果")
        self.summary_tree.column("item", width=180, anchor="w")
        self.summary_tree.column("value", width=680, anchor="w")
        self.summary_tree.pack(fill="x", pady=(4, 10))

    def _build_log(self) -> None:
        tk.Label(self.main_frame, text="实时日志", anchor="w").pack(anchor="w")
        self.log_widget = tk.Text(self.main_frame, height=12, width=110)
        self.log_widget.pack(fill="both", expand=True, pady=(4, 0))

    def _append_log(self, message: str) -> None:
        self.log_widget.insert("end", f"{message}\n")
        self.log_widget.see("end")

    def _clear_summary(self) -> None:
        for iid in self.summary_tree.get_children():
            self.summary_tree.delete(iid)

    def _add_summary(self, item: str, value: object) -> None:
        self.summary_tree.insert("", "end", values=(item, "" if value is None else str(value)))

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
        try:
            if os.name == "nt":
                os.startfile(self._workspace_root)
            else:
                subprocess.Popen(["xdg-open", self._workspace_root])
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
            labels.append(label)
            filtered_items.append(item)

        self.language_items = filtered_items
        self.language_label_to_code = mapping
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
        self._workspace_root = ""
        self.open_workspace_button.configure(state="disabled")
        self.status_var.set("任务已启动")
        self._append_log(
            f"开始任务：product_code={product_code}, lang={lang_code}, "
            f"shopify_id={shopify_product_id or '自动识别'}"
        )
        threading.Thread(
            target=self._run,
            args=(base_url, api_key, browser_dir, product_code, lang_code, shopify_product_id, cancel_token),
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
        base_url = settings.DEFAULT_BASE_URL
        self.base_url_var.set(base_url)
        api_key = self.api_key_var.get().strip()
        browser_dir = self.browser_user_data_dir_var.get().strip()
        if not api_key or not browser_dir:
            messagebox.showerror("错误", "高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空")
            return

        target_name = "EZ 页面" if target == "ez" else "详情页"
        self._set_running_state(True)
        self.status_var.set(f"正在打开 {target_name}")
        self._append_log(
            f"准备打开 {target_name}：product_code={product_code}, lang={lang_code}, "
            f"shopify_id={shopify_product_id or '自动识别'}"
        )
        threading.Thread(
            target=self._open_shopify_target_worker,
            args=(target, base_url, api_key, browser_dir, product_code, lang_code, shopify_product_id),
            daemon=True,
        ).start()

    def _open_shopify_target_worker(
        self,
        target: str,
        base_url: str,
        api_key: str,
        browser_dir: str,
        product_code: str,
        lang_code: str,
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
        self._clear_summary()
        self._add_summary("商品 ID", product_code)
        self._add_summary("语言", result.get("lang"))
        self._add_summary("Shopify ID", result.get("shopify_product_id"))
        self._add_summary("已打开页面", target_name)
        self._add_summary("URL", result.get("url"))
        self.status_var.set(f"已打开 {target_name}")
        self._append_log(f"已打开 {target_name}：{result.get('url')}")

    def _run(
        self,
        base_url: str,
        api_key: str,
        browser_dir: str,
        product_code: str,
        lang_code: str,
        shopify_product_id: str,
        cancel_token: cancellation.CancellationToken,
    ) -> None:
        try:
            result = controller.run_shopify_localizer(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_dir,
                product_code=product_code,
                lang=lang_code,
                shopify_product_id=shopify_product_id,
                cancel_token=cancel_token,
                status_cb=lambda message: self.root.after(0, self._handle_status, message),
            )
            self.root.after(0, self._render_result, result)
        except cancellation.OperationCancelled:
            self.root.after(0, self._render_cancelled)
        except Exception as exc:
            self.root.after(0, self.status_var.set, "执行失败")
            self.root.after(0, self._append_log, f"执行失败：{exc}")
            self.root.after(0, messagebox.showerror, "执行失败", str(exc))
        finally:
            self._current_cancel_token = None
            self.root.after(0, self._set_running_state, False)

    def _render_cancelled(self) -> None:
        self.status_var.set("已停止")
        self._append_log("任务已停止")
        self._add_summary("任务状态", "已停止")

    def _render_result(self, result: dict) -> None:
        workspace = str(result.get("workspace_root") or result.get("workspace") or "")
        self._workspace_root = workspace
        if workspace:
            self.open_workspace_button.configure(state="normal")

        self._clear_summary()
        self._add_summary("商品 ID", result.get("product_code"))
        self._add_summary("语言", result.get("lang"))
        self._add_summary("Shopify ID", result.get("shopify_product_id"))
        self._add_summary("任务目录", workspace)
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
        self._append_log("执行完成")

    def _handle_status(self, message: str) -> None:
        self.status_var.set(message)
        self._append_log(message)
