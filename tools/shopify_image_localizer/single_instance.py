from __future__ import annotations

import ctypes
import os
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk


MUTEX_NAME = r"Local\ShopifyImageLocalizer"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    def __init__(self, handle: int | None = None, kernel32=None) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    def release(self) -> None:
        handle = self._handle
        kernel32 = self._kernel32
        self._handle = None
        if not handle or kernel32 is None:
            return
        try:
            kernel32.ReleaseMutex(handle)
        finally:
            kernel32.CloseHandle(handle)

    def __enter__(self) -> SingleInstanceGuard:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()


def acquire_or_prompt(close_existing_callback: Callable[[], None]) -> SingleInstanceGuard | None:
    if os.name != "nt":
        return SingleInstanceGuard()

    try:
        handle, already_exists, kernel32 = _create_mutex()
    except OSError as exc:
        _show_error(f"创建单例锁失败：{exc}")
        return None

    if not already_exists:
        return SingleInstanceGuard(handle, kernel32)

    kernel32.CloseHandle(handle)
    if not _ask_close_previous_instance():
        return None

    try:
        close_existing_callback()
    except Exception as exc:
        _show_error(f"关闭前一个程序失败：{exc}")
        return None

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        handle, already_exists, kernel32 = _create_mutex()
        if not already_exists:
            return SingleInstanceGuard(handle, kernel32)
        kernel32.CloseHandle(handle)
        time.sleep(0.25)

    _show_error("前一个程序尚未退出，请稍后再启动。")
    return None


def _create_mutex() -> tuple[int, bool, ctypes.WinDLL]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise OSError(error, "CreateMutexW failed")
    return int(handle), error == ERROR_ALREADY_EXISTS, kernel32


def _ask_close_previous_instance() -> bool:
    root = tk.Tk()
    root.withdraw()

    result = {"close_previous": False}
    dialog = tk.Toplevel(root)
    dialog.title("Shopify 图片本地化替换已在运行")
    dialog.resizable(False, False)
    dialog.attributes("-topmost", True)

    frame = ttk.Frame(dialog, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text="检测到 Shopify 图片本地化替换工具已经在运行。",
        wraplength=360,
        justify="left",
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text="你可以关闭前一个程序并启动新的，或退出本次启动。",
        wraplength=360,
        justify="left",
    ).pack(anchor="w", pady=(8, 0))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(18, 0))

    def close_previous() -> None:
        result["close_previous"] = True
        dialog.destroy()

    def exit_current() -> None:
        result["close_previous"] = False
        dialog.destroy()

    ttk.Button(buttons, text="关闭前一个并启动", command=close_previous).pack(side="left")
    ttk.Button(buttons, text="退出", command=exit_current).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", exit_current)
    dialog.update_idletasks()
    x = root.winfo_screenwidth() // 2 - dialog.winfo_width() // 2
    y = root.winfo_screenheight() // 2 - dialog.winfo_height() // 2
    dialog.geometry(f"+{x}+{y}")
    dialog.grab_set()
    dialog.focus_force()
    dialog.wait_window()
    root.destroy()
    return result["close_previous"]


def _show_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Shopify 图片本地化替换启动失败", message, parent=root)
    root.destroy()
