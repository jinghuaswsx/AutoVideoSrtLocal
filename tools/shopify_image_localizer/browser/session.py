from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


STORE_SLUG = "0ixug9-pv"
DEFAULT_CDP_PORT = 7777

LOGIN_URL_HINTS = ("login", "signin", "sign-in", "account", "identity")
LOGIN_TEXT_HINTS = (
    "log in",
    "sign in",
    "continue with email",
    "enter your email",
    "shopify",
)


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def build_ez_url(shopify_product_id: str) -> str:
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/"
        f"ez-product-image-translate/product/{shopify_product_id}"
    )


def build_translate_url(shopify_product_id: str, shop_locale: str) -> str:
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/translate-and-adapt/localize/product"
        f"?highlight=handle&id={shopify_product_id}&shopLocale={shop_locale}"
    )


# ---------------------------------------------------------------------------
# Chrome launch + CDP connect
# ---------------------------------------------------------------------------


def _find_chrome_executable() -> str | None:
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


def _cdp_alive(port: int, *, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_chrome_running(
    user_data_dir: str,
    *,
    port: int = DEFAULT_CDP_PORT,
    startup_timeout_s: int = 30,
) -> bool:
    """确保一个普通 Chrome 进程在 `port` 上开着 DevTools 协议。

    返回：True 表示本函数刚启动了 Chrome；False 表示发现已有 Chrome 在监听。

    关键点：
    - 不加 `--enable-automation`、`--test-type` 等会触发"自动化控制"横幅的 flag
    - 以 detached 方式启动，不是 Playwright 的子进程
    - 仅当 `port` 上还没有 DevTools 时才启动新的 Chrome
    """
    if _cdp_alive(port):
        return False

    chrome_exe = _find_chrome_executable()
    if not chrome_exe:
        raise RuntimeError(
            "未找到 chrome.exe，请安装 Google Chrome，或确认 chrome.exe 在 PATH 中"
        )
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    creation_flags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — Chrome 独立于本进程存活
        creation_flags = 0x00000008 | 0x00000200

    subprocess.Popen(
        [
            chrome_exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
        ],
        creationflags=creation_flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    deadline = time.time() + startup_timeout_s
    while time.time() < deadline:
        if _cdp_alive(port):
            return True
        time.sleep(0.5)
    raise RuntimeError(
        f"Chrome 在 127.0.0.1:{port} 未在 {startup_timeout_s}s 内就绪，请手动启动后重试"
    )


@dataclass
class ChromeSession:
    """持有 CDP 连接句柄的会话。关闭时只断开 CDP，不杀 Chrome 进程。"""

    browser: object
    context: object

    def close(self) -> None:
        try:
            if self.browser is not None:
                # connect_over_cdp 返回的 browser.close() 只断开连接，不会终止外部 Chrome
                self.browser.close()
        except Exception:
            pass


def open_chrome_session(
    playwright,
    user_data_dir: str,
    *,
    port: int = DEFAULT_CDP_PORT,
) -> ChromeSession:
    """确保 Chrome 在 `port` 上运行并用 CDP 接入，返回默认 context。"""
    ensure_chrome_running(user_data_dir, port=port)
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()
    return ChromeSession(browser=browser, context=context)


# 兼容旧调用点：返回一个 BrowserContext，内部把 browser 句柄挂在上面防被 GC
def launch_persistent_context(playwright, user_data_dir: str, *, port: int = DEFAULT_CDP_PORT):
    chrome_session = open_chrome_session(playwright, user_data_dir, port=port)
    context = chrome_session.context
    try:
        setattr(context, "_chrome_session", chrome_session)
    except Exception:
        pass
    return context


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------


def page_requires_login(page) -> bool:
    current_url = (page.url or "").strip().lower()
    if any(token in current_url for token in LOGIN_URL_HINTS):
        return True

    try:
        body_text = (page.locator("body").inner_text(timeout=1500) or "").strip().lower()
    except Exception:
        body_text = ""

    if body_text:
        condensed = re.sub(r"\s+", " ", body_text)
        if any(token in condensed for token in LOGIN_TEXT_HINTS):
            if "admin.shopify.com/store/" not in current_url:
                return True
    return False


def ensure_target_page(page, target_url: str, *, status_cb=None, label: str = "Shopify") -> None:
    if status_cb is not None:
        status_cb(f"正在打开 {label} 页面")
    page.goto(target_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    warned = False
    while page_requires_login(page):
        if status_cb is not None and not warned:
            status_cb("检测到 Shopify 未登录，请在浏览器中手动登录，程序会自动继续")
        warned = True
        page.wait_for_timeout(2000)

    if warned:
        if status_cb is not None:
            status_cb(f"检测到 Shopify 登录恢复，正在回到 {label} 页面")
        page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)


def save_page_snapshot(page, output_dir: Path, filename: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def capture_visible_images(
    page,
    output_dir: Path,
    *,
    prefix: str,
    selectors: list[str],
    min_width: float = 80.0,
    min_height: float = 80.0,
    max_count: int = 40,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []
    seen_keys: set[tuple[str, int, int]] = set()

    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = min(locator.count(), max_count)
        except Exception:
            continue

        for index in range(count):
            if len(captured) >= max_count:
                return captured

            candidate = locator.nth(index)
            try:
                box = candidate.bounding_box()
                if not box:
                    continue
                if float(box.get("width") or 0.0) < min_width or float(box.get("height") or 0.0) < min_height:
                    continue
                src = (
                    candidate.evaluate("(node) => node.currentSrc || node.src || ''")
                    if hasattr(candidate, "evaluate")
                    else ""
                )
                dedupe_key = (
                    str(src or selector),
                    int(box.get("width") or 0),
                    int(box.get("height") or 0),
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                slot_id = f"{prefix}-{len(captured) + 1:03d}"
                local_path = output_dir / f"{slot_id}.png"
                candidate.screenshot(path=str(local_path))
                captured.append({
                    "slot_id": slot_id,
                    "local_path": str(local_path),
                    "selector": selector,
                    "index": index,
                    "src": str(src or ""),
                    "width": float(box.get("width") or 0.0),
                    "height": float(box.get("height") or 0.0),
                })
            except Exception:
                continue

    return captured


def click_slot(page, slot: dict) -> None:
    selector = str(slot.get("selector") or "")
    index = int(slot.get("index") or 0)
    if not selector:
        return
    locator = page.locator(selector)
    if locator.count() <= index:
        return
    locator.nth(index).click(timeout=3000)
    page.wait_for_timeout(500)


def upload_file_to_page(page, local_path: str) -> bool:
    file_inputs = page.locator("input[type='file']")
    try:
        count = file_inputs.count()
    except Exception:
        count = 0
    for index in range(count):
        try:
            file_inputs.nth(index).set_input_files(local_path)
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue

    button_patterns = [
        re.compile(r"add", re.I),
        re.compile(r"upload", re.I),
        re.compile(r"replace", re.I),
        re.compile(r"image", re.I),
        re.compile(r"photo", re.I),
        re.compile(r"media", re.I),
    ]
    for pattern in button_patterns:
        try:
            button = page.get_by_role("button", name=pattern).first
            with page.expect_file_chooser(timeout=2000) as chooser:
                button.click()
            chooser.value.set_files(local_path)
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    return False
