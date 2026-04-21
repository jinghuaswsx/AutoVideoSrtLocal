from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

from link_check_desktop import controller


class LinkCheckApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Link Check Desktop")

        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请输入目标页链接")
        self.result_var = tk.StringVar(value="")

        tk.Label(self.root, text="目标页链接").pack(anchor="w", padx=12, pady=(12, 4))
        tk.Entry(self.root, textvariable=self.url_var, width=90).pack(fill="x", padx=12)
        self.start_button = tk.Button(self.root, text="开始检查", command=self.start_run)
        self.start_button.pack(anchor="e", padx=12, pady=12)
        tk.Label(self.root, textvariable=self.status_var, justify="left").pack(anchor="w", padx=12)
        tk.Label(self.root, textvariable=self.result_var, justify="left").pack(
            anchor="w",
            padx=12,
            pady=(8, 12),
        )

    def start_run(self) -> None:
        target_url = self.url_var.get().strip()
        if not target_url:
            messagebox.showerror("错误", "请先输入目标页链接")
            return
        self.start_button.configure(state="disabled")
        self.status_var.set("任务已启动")
        self.result_var.set("")
        threading.Thread(target=self._run, args=(target_url,), daemon=True).start()

    def _run(self, target_url: str) -> None:
        try:
            result = controller.run_link_check(
                target_url=target_url,
                status_cb=lambda message: self.root.after(0, self.status_var.set, message),
            )
            summary = result["analysis"]["summary"]
            text = (
                f"产品 ID: {result['product']['id']}\n"
                f"语种: {result['target_language']}\n"
                f"通过: {summary.get('pass_count', 0)}\n"
                f"替换: {summary.get('replace_count', 0)}\n"
                f"复核: {summary.get('review_count', 0)}"
            )
            self.root.after(0, self.result_var.set, text)
        except Exception as exc:
            self.root.after(0, messagebox.showerror, "执行失败", str(exc))
        finally:
            self.root.after(0, lambda: self.start_button.configure(state="normal"))
