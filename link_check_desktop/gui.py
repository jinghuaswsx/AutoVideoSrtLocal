from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

from link_check_desktop import controller, report, settings
from link_check_desktop.bootstrap_api import BootstrapError


class LinkCheckApp:
    def __init__(self, *, prompt_on_start: bool = True) -> None:
        runtime_config = settings.load_runtime_config()

        self.root = tk.Tk()
        self.root.title("Link Check Desktop")
        self.root.geometry("760x320")
        self.root.resizable(False, False)

        self.url_var = tk.StringVar()
        self.base_url_var = tk.StringVar(value=runtime_config["base_url"])
        self.api_key_var = tk.StringVar(value=runtime_config["api_key"])
        self.status_var = tk.StringVar(value="粘贴产品页面链接后，点击开始检查")
        self.result_var = tk.StringVar(value="")
        self.advanced_visible = False

        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(self.main_frame, text="产品页链接").pack(anchor="w")
        self.url_entry = tk.Entry(self.main_frame, textvariable=self.url_var, width=90)
        self.url_entry.pack(fill="x", pady=(4, 0))
        self.url_entry.focus_set()

        self.action_frame = tk.Frame(self.main_frame)
        self.action_frame.pack(fill="x", pady=(8, 0))
        self.start_button = tk.Button(
            self.action_frame,
            text="开始检查",
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
        tk.Label(self.advanced_frame, text="服务端 API 地址").pack(anchor="w", pady=(4, 0))
        tk.Entry(self.advanced_frame, textvariable=self.base_url_var, width=90).pack(fill="x")
        tk.Label(self.advanced_frame, text="OpenAPI Key").pack(anchor="w", pady=(8, 0))
        tk.Entry(self.advanced_frame, textvariable=self.api_key_var, width=90).pack(fill="x")

        tk.Label(self.main_frame, textvariable=self.status_var, justify="left").pack(
            anchor="w",
            pady=(12, 0),
        )
        tk.Label(self.main_frame, textvariable=self.result_var, justify="left").pack(
            anchor="w",
            pady=(8, 0),
        )

        _ = prompt_on_start

    def toggle_advanced(self) -> None:
        if self.advanced_visible:
            self.advanced_frame.pack_forget()
            self.advanced_button.configure(text="显示高级设置")
            self.advanced_visible = False
            return

        self.advanced_frame.pack(fill="x", pady=(8, 0))
        self.advanced_button.configure(text="隐藏高级设置")
        self.advanced_visible = True

    def _set_running_state(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.advanced_button.configure(state=state)

    def _friendly_error_message(self, exc: Exception) -> str:
        if isinstance(exc, BootstrapError):
            code = (exc.payload or {}).get("error") or ""
            mapping = {
                "invalid api key": "OpenAPI Key 无效，请检查高级设置中的密钥。",
                "invalid target_url": "产品页面链接无效，请检查链接格式。",
                "language not detected": "无法从链接中识别语种。",
                "product not found": "服务端素材库中找不到这个产品。",
                "references not ready": "服务端素材库里该语种参考图还没就绪，请先在服务端补齐素材。",
                "bootstrap returned non-json response": "服务端返回了异常响应，请稍后重试。",
            }
            return mapping.get(code, str(exc))
        return str(exc)

    def start_run(self) -> None:
        target_url = self.url_var.get().strip()
        base_url = self.base_url_var.get().strip()
        api_key = self.api_key_var.get().strip()
        if not target_url:
            messagebox.showerror("错误", "请先输入产品页链接")
            return
        if not base_url:
            messagebox.showerror("错误", "请先输入服务端 API 地址")
            return
        if not api_key:
            messagebox.showerror("错误", "请先输入 OpenAPI Key")
            return

        settings.save_runtime_config(base_url=base_url, api_key=api_key)
        self._set_running_state(True)
        self.status_var.set("任务已启动")
        self.result_var.set("")
        threading.Thread(
            target=self._run,
            args=(target_url, base_url, api_key),
            daemon=True,
        ).start()

    def _run(self, target_url: str, base_url: str, api_key: str) -> None:
        try:
            result = controller.run_link_check(
                base_url=base_url,
                api_key=api_key,
                target_url=target_url,
                status_cb=lambda message: self.root.after(0, self.status_var.set, message),
            )
            summary = result["analysis"]["summary"]
            report_html_path = result.get("report_html_path") or ""
            text = (
                f"产品 ID: {result['product']['id']}\n"
                f"语种: {result['target_language']}\n"
                f"总判定: {summary.get('overall_decision', '')}\n"
                f"通过: {summary.get('pass_count', 0)}\n"
                f"替换: {summary.get('replace_count', 0)}\n"
                f"复核: {summary.get('review_count', 0)}\n"
                f"目录: {result.get('workspace_root', '')}\n"
                f"报告: {report_html_path}"
            )
            self.root.after(0, self.status_var.set, "执行完成，已生成本地结果页")
            self.root.after(0, self.result_var.set, text)
            if report_html_path:
                try:
                    report.open_report(report_html_path)
                except Exception:
                    pass
        except Exception as exc:
            friendly = self._friendly_error_message(exc)
            self.root.after(0, self.status_var.set, "执行失败")
            self.root.after(0, self.result_var.set, friendly)
            self.root.after(0, messagebox.showerror, "执行失败", friendly)
        finally:
            self.root.after(0, self._set_running_state, False)
