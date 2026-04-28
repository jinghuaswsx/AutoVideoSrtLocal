from __future__ import annotations

"""Chrome 启动与 URL 打开 helpers（普通模式，非 CDP）。

为什么不用 CDP：Shopify 的 App Bridge 对 Playwright / `--remote-debugging-port` 有
反调试，EZ 和 TAA 的 embedded iframe 在自动化模式下会被主动移除。本工具因此改为
"普通 Chrome subprocess + Python 端只负责下载/配对/提示" 的半自动 RPA 模式。

本模块职责：
- 找到用户机器上的 chrome.exe
- 自动探测副屏 9:16 竖屏位置 / 主屏兜底 900x1600
- 自动探测本机 Clash/V2Ray 代理端口
- 以 detached 方式启动 Chrome，保留 `--user-data-dir` 里的登录态
- 在现有 Chrome 实例里额外打开新 tab（Chrome 自己支持：同一 user-data-dir 再启动会把 URL 塞给旧实例）
- 发信号让 Chrome 退出（任务完成后主流程可选清理）
"""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from tools.shopify_image_localizer import locales


STORE_SLUG = "0ixug9-pv"

# 固定窗口参数：1920×1080，副屏左上为原点；无副屏放主屏 (0,0)
FIXED_WINDOW_SIZE = "1920,1080"
FIXED_WINDOW_W = 1920
FIXED_WINDOW_H = 1080
FALLBACK_WINDOW_POSITION = "0,0"
FALLBACK_WINDOW_SIZE = FIXED_WINDOW_SIZE


# ---------------------------------------------------------------------------
# Monitor / window detection
# ---------------------------------------------------------------------------


def detect_window_bounds() -> tuple[str, str]:
    """返回 (window_position, window_size)。size 固定 1920×1080。

    - 检测到副屏：position = 副屏 monitor 真实左上（含任务栏）
    - 只有主屏：position = (0, 0)
    - 检测异常：(0, 0)
    """
    if os.name != "nt":
        return FALLBACK_WINDOW_POSITION, FIXED_WINDOW_SIZE
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD),
            ]

        monitors: list[dict] = []
        enum_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
        )

        def _callback(hmon, _hdc, _lprect, _lparam):
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return 1
            # 用 rcMonitor（monitor 真实左上原点，含任务栏区域）而非 rcWork
            monitors.append({
                "left": mi.rcMonitor.left,
                "top": mi.rcMonitor.top,
                "right": mi.rcMonitor.right,
                "bottom": mi.rcMonitor.bottom,
                "is_primary": bool(mi.dwFlags & 1),
            })
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, enum_proc_type(_callback), 0)

        secondary = next((m for m in monitors if not m["is_primary"]), None)
        if secondary is None:
            return FALLBACK_WINDOW_POSITION, FIXED_WINDOW_SIZE

        # 副屏 logical 装得下窗口才用副屏，否则回主屏 (0,0)
        sec_w = int(secondary["right"]) - int(secondary["left"])
        sec_h = int(secondary["bottom"]) - int(secondary["top"])
        if sec_w >= FIXED_WINDOW_W and sec_h >= FIXED_WINDOW_H:
            return f"{secondary['left']},{secondary['top']}", FIXED_WINDOW_SIZE
        return FALLBACK_WINDOW_POSITION, FIXED_WINDOW_SIZE
    except Exception:
        return FALLBACK_WINDOW_POSITION, FIXED_WINDOW_SIZE


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def build_ez_url(shopify_product_id: str) -> str:
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/"
        f"ez-product-image-translate/product/{shopify_product_id}"
    )


def build_translate_url(shopify_product_id: str, shop_locale: str) -> str:
    taa_locale = locales.translate_and_adapt_locale_for(shop_locale)
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/translate-and-adapt/localize/product"
        f"?highlight=handle&id={shopify_product_id}&shopLocale={taa_locale}"
    )


def build_admin_home_url() -> str:
    return f"https://admin.shopify.com/store/{STORE_SLUG}"


def build_products_url() -> str:
    return f"{build_admin_home_url()}/products"


# ---------------------------------------------------------------------------
# Chrome process discovery / proxy / launch
# ---------------------------------------------------------------------------


def find_chrome_executable() -> str | None:
    candidates: list[str] = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        candidates.append(str(Path(localappdata) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    for path in candidates:
        if path and Path(path).is_file():
            return path
    which = shutil.which("chrome")
    if which:
        return which
    return None


_COMMON_PROXY_PORTS = (7890, 7891, 7892, 7893, 10808, 10809, 1080, 8118, 8888)


def detect_system_proxy() -> str | None:
    """探测本机常见代理端口（Clash/V2Ray 默认配置）。"""
    for port in _COMMON_PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


def _nt_creation_flags() -> int:
    if os.name != "nt":
        return 0
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return 0x00000008 | 0x00000200


def is_chrome_running_for_profile(user_data_dir: str) -> bool:
    """简单检测：`user-data-dir` 对应的 Chrome 主进程是否在跑。"""
    if os.name != "nt":
        return False
    try:
        target = Path(user_data_dir).resolve().as_posix().lower()
    except Exception:
        target = str(user_data_dir).lower()
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" "
                "| Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' } "
                "| ForEach-Object { $_.CommandLine }",
            ],
            capture_output=True,
            text=True,
            timeout=6,
        )
    except Exception:
        return False
    for line in (result.stdout or "").splitlines():
        low = line.lower().replace("\\", "/")
        if target in low or str(user_data_dir).lower() in line.lower():
            return True
    return False


def _enable_chrome_developer_mode(user_data_dir: str) -> None:
    """在 Chrome 未启动时把 profile 的 Preferences 里的 developer_mode 打开。

    Chrome 137+ 默认要求 Developer Mode 才会加载 unpacked extension (--load-extension)。
    修改：extensions.ui.developer_mode = true
    """
    import json as _json
    prefs_path = Path(user_data_dir) / "Default" / "Preferences"
    try:
        if not prefs_path.is_file():
            # Default 目录可能还没初始化（首次启动），先写一个最小 Preferences
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"extensions": {"ui": {"developer_mode": True}}}
            prefs_path.write_text(_json.dumps(data), encoding="utf-8")
            return
        raw = prefs_path.read_text(encoding="utf-8")
        data = _json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    exts = data.setdefault("extensions", {})
    ui = exts.setdefault("ui", {})
    if ui.get("developer_mode") is True:
        return
    ui["developer_mode"] = True
    try:
        prefs_path.write_text(_json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _resolve_bundled_extension_dir() -> str | None:
    """Return absolute path to the bundled Chrome extension directory, if present."""
    candidates = [
        Path(__file__).resolve().parent.parent / "chrome_ext",
    ]
    import sys as _sys

    if getattr(_sys, "frozen", False):
        # PyInstaller: data files live next to the EXE or under _MEIPASS
        meipass = getattr(_sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "tools" / "shopify_image_localizer" / "chrome_ext")
            candidates.append(Path(meipass) / "chrome_ext")
        candidates.append(Path(_sys.executable).resolve().parent / "chrome_ext")
    for c in candidates:
        try:
            if c.is_dir() and (c / "manifest.json").is_file():
                return str(c)
        except Exception:
            continue
    return None


def _build_base_args(
    user_data_dir: str,
    *,
    window_position: str | None,
    window_size: str | None,
    proxy_server: str | None,
    load_extension: bool = True,
    maximized: bool = True,
) -> list[str]:
    if proxy_server is None:
        proxy_server = detect_system_proxy()

    args: list[str] = [
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if maximized:
        # 让 Chrome 启动时占满当前屏幕，避免窗口尺寸/坐标的 DPI 麻烦
        args.append("--start-maximized")
    else:
        # 兼容旧路径：显式 position + size
        if window_position is None or window_size is None:
            detected_pos, detected_size = detect_window_bounds()
            window_position = window_position or detected_pos
            window_size = window_size or detected_size
        if window_position:
            args.append(f"--window-position={window_position}")
        if window_size:
            args.append(f"--window-size={window_size}")
    if proxy_server:
        args.append(f"--proxy-server={proxy_server}")
        args.append("--proxy-bypass-list=127.0.0.1;localhost;172.30.254.14;<local>")
    if load_extension:
        ext_dir = _resolve_bundled_extension_dir()
        if ext_dir:
            # Chrome 137+ 需要 "Developer Mode" 才会加载未签名 unpacked extension，
            # `--enable-unsafe-extension-debugging` 或命令行启用即可绕过 UI toggle
            args.append(f"--load-extension={ext_dir}")
    return args


def start_chrome(
    user_data_dir: str,
    initial_urls: list[str] | None = None,
    *,
    window_position: str | None = None,
    window_size: str | None = None,
    proxy_server: str | None = None,
) -> subprocess.Popen:
    """以普通用户模式启动 Chrome，不带任何 automation flag。

    - 用 `--user-data-dir` 指定的 profile（保留登录态/授权）
    - 不加 `--remote-debugging-port`/`--enable-automation`/`--disable-web-security`，
      这些 flag 任何一个都会让 Shopify App Bridge 反调试拒绝渲染 embedded app iframe。
    """
    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        raise RuntimeError(
            "未找到 chrome.exe，请安装 Google Chrome，或确认 chrome.exe 在 PATH 中"
        )
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    # Chrome 137+ 需要 Developer Mode 才加载 unpacked extension
    _enable_chrome_developer_mode(user_data_dir)

    argv = [chrome_exe] + _build_base_args(
        user_data_dir,
        window_position=window_position,
        window_size=window_size,
        proxy_server=proxy_server,
    )
    for u in (initial_urls or []):
        argv.append(u)

    return subprocess.Popen(
        argv,
        creationflags=_nt_creation_flags(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def open_urls_in_chrome(
    user_data_dir: str,
    urls: list[str],
    *,
    window_position: str | None = None,
    window_size: str | None = None,
    proxy_server: str | None = None,
) -> None:
    """在已有 Chrome 实例里新开 tab（同一 user-data-dir 会复用现有进程）。

    如果 Chrome 还没启动，这次调用会同时启动 Chrome。
    如果 Chrome 已经在跑，Chrome 会把 URL 塞进现有 window 新开 tab。
    """
    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        raise RuntimeError("未找到 chrome.exe")
    argv = [chrome_exe] + _build_base_args(
        user_data_dir,
        window_position=window_position,
        window_size=window_size,
        proxy_server=proxy_server,
    ) + list(urls)
    subprocess.Popen(
        argv,
        creationflags=_nt_creation_flags(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def ensure_chrome_started(
    user_data_dir: str,
    initial_urls: list[str] | None = None,
    *,
    window_position: str | None = None,
    window_size: str | None = None,
    proxy_server: str | None = None,
    wait_s: float = 2.0,
) -> None:
    """保证 Chrome 已经运行在对应 profile 上。

    - 若 Chrome 已在跑：把 initial_urls 当新 tab 塞进去
    - 若 Chrome 没跑：启动 + 打开 initial_urls
    """
    if is_chrome_running_for_profile(user_data_dir):
        if initial_urls:
            open_urls_in_chrome(
                user_data_dir,
                initial_urls,
                window_position=window_position,
                window_size=window_size,
                proxy_server=proxy_server,
            )
        return
    start_chrome(
        user_data_dir,
        initial_urls,
        window_position=window_position,
        window_size=window_size,
        proxy_server=proxy_server,
    )
    time.sleep(wait_s)


def kill_chrome_for_profile(user_data_dir: str, *, wait_s: float = 5.0) -> None:
    """强制结束使用 `user_data_dir` profile 的所有 Chrome 进程。

    只在工具需要重启 Chrome 时使用；正常半自动流程不需要杀 Chrome。
    """
    if os.name != "nt":
        return
    target = str(user_data_dir or "").strip()
    if not target:
        return
    ps_target = target.replace("'", "''")
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$target = '{ps_target}'; "
                    "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -and "
                    "$_.CommandLine.IndexOf($target, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                ),
            ],
            capture_output=True,
            timeout=8,
        )
    except Exception:
        pass
    deadline = time.time() + max(0.0, float(wait_s or 0.0))
    while time.time() < deadline:
        if not is_chrome_running_for_profile(user_data_dir):
            return
        time.sleep(0.25)
