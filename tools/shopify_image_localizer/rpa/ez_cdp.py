from __future__ import annotations

"""EZ Product Translate automation through external Chrome CDP.

This keeps the important part of the working route: Chrome is launched as a
normal detached user browser with the existing Shopify profile. Playwright only
connects to that browser over CDP after it is running, then drives the EZ iframe
DOM and file inputs directly.
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer.browser import session


DEFAULT_CDP_PORT = 7777


def _cdp_alive(port: int = DEFAULT_CDP_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _cdp_ws_endpoint(port: int = DEFAULT_CDP_PORT) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    endpoint = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if not endpoint:
        raise RuntimeError(f"Chrome CDP 127.0.0.1:{port} 未返回 webSocketDebuggerUrl")
    return endpoint


def _chrome_exe() -> str:
    found = session.find_chrome_executable()
    if found:
        return found
    which = shutil.which("chrome")
    if which:
        return which
    raise RuntimeError("未找到 chrome.exe")


def ensure_cdp_chrome(
    user_data_dir: str,
    initial_url: str,
    *,
    port: int = DEFAULT_CDP_PORT,
    proxy_server: str | None = None,
    startup_timeout_s: int = 30,
) -> bool:
    """Start normal Chrome with a CDP port if needed.

    Returns True when this call starts Chrome, False when an existing CDP Chrome
    is reused. No Playwright launch flags are used.
    """
    if _cdp_alive(port):
        return False
    if proxy_server is None:
        proxy_server = session.detect_system_proxy()
    args = [
        _chrome_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    if proxy_server:
        args.extend([
            f"--proxy-server={proxy_server}",
            "--proxy-bypass-list=127.0.0.1;localhost;172.30.254.14;<local>",
        ])
    args.append(initial_url)

    subprocess.Popen(
        args,
        creationflags=0x00000008 | 0x00000200 if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.time() + startup_timeout_s
    while time.time() < deadline:
        if _cdp_alive(port):
            return True
        time.sleep(0.5)
    raise RuntimeError(f"Chrome CDP 127.0.0.1:{port} 未就绪")


def md5_token(value: str) -> str | None:
    match = re.search(r"([a-f0-9]{28,})", (value or "").lower())
    return match.group(1) if match else None


def _find_plugin_frame(page):
    for frame in page.frames:
        if "translate.freshify.click" in (frame.url or ""):
            return frame
    return None


def _wait_plugin_frame(page, *, timeout_s: int = 30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = _find_plugin_frame(page)
        if frame is not None:
            try:
                if frame.locator("s-button.image-button").count() > 0:
                    return frame
            except Exception:
                pass
        page.wait_for_timeout(500)
    raise RuntimeError("EZ freshify iframe 未加载或未出现图片按钮")


def _dialog_text(frame) -> str:
    return frame.locator("[role=dialog]").inner_text(timeout=5000) or ""


def _modal_hash(frame) -> str | None:
    text = _dialog_text(frame)
    match = re.search(r"translation for:\s*([a-f0-9]{28,})\.", text, re.I)
    return match.group(1).lower() if match else None


def _click_save_and_wait(frame) -> None:
    frame.locator('button:has-text("Save")').click(timeout=5000)
    try:
        frame.locator("[role=dialog]").wait_for(state="detached", timeout=15000)
    except PlaywrightTimeoutError:
        frame.page.wait_for_timeout(2500)


def _click_cancel(frame) -> None:
    try:
        frame.locator('button:has-text("Cancel")').click(timeout=3000)
        frame.page.wait_for_timeout(1000)
    except Exception:
        pass


def _open_slot(frame, slot_idx: int, expected_hash: str | None) -> None:
    buttons = frame.locator("s-button.image-button")
    count = buttons.count()
    if slot_idx >= count:
        raise RuntimeError(f"slot {slot_idx} 超出 EZ 可见按钮数量 {count}")
    buttons.nth(slot_idx).click(timeout=8000)
    frame.locator("[role=dialog]").wait_for(state="visible", timeout=10000)
    actual_hash = _modal_hash(frame)
    if expected_hash and actual_hash and actual_hash != expected_hash:
        _click_cancel(frame)
        raise RuntimeError(f"slot {slot_idx} hash mismatch: modal={actual_hash}, local={expected_hash}")


def _target_exists(frame, language: str) -> bool:
    return frame.locator(f'button[aria-label="Remove {language}"]').count() > 0


def _select_language(frame, language: str) -> None:
    result = frame.evaluate(
        """(language) => {
            const wanted = String(language || '').trim().toLowerCase();
            const select = document.querySelector('s-select[label="Add Language"]') || document.querySelector('s-select');
            if (!select) return {ok:false, reason:'no s-select'};
            const option = Array.from(select.querySelectorAll('s-option')).find((node) => {
                return (node.textContent || '').trim().toLowerCase() === wanted;
            });
            if (!option) {
                return {ok:false, reason:'missing option', options:Array.from(select.querySelectorAll('s-option')).map((node) => (node.textContent || '').trim())};
            }
            select.value = option.getAttribute('value');
            select.dispatchEvent(new Event('input', {bubbles:true, composed:true}));
            select.dispatchEvent(new Event('change', {bubbles:true, composed:true}));
            return {ok:true, value: select.value};
        }""",
        language,
    )
    if not result or not result.get("ok"):
        raise RuntimeError(f"无法选择语言 {language}: {json.dumps(result, ensure_ascii=False)}")
    frame.locator("input[type=file]").wait_for(state="attached", timeout=10000)


def replace_slot(
    frame,
    slot_idx: int,
    local_image_path: str,
    *,
    language: str = "Italian",
    replace_existing: bool = True,
) -> dict:
    local_hash = md5_token(Path(local_image_path).name)
    _open_slot(frame, slot_idx, local_hash)
    try:
        if _target_exists(frame, language):
            if not replace_existing:
                _click_cancel(frame)
                return {"slot": slot_idx, "status": "skipped", "reason": f"{language} already exists"}
            frame.locator(f'button[aria-label="Remove {language}"]').click(timeout=5000)
            _click_save_and_wait(frame)
            frame.page.wait_for_timeout(1500)
            frame = _wait_plugin_frame(frame.page, timeout_s=20)
            _open_slot(frame, slot_idx, local_hash)

        _select_language(frame, language)
        frame.locator("input[type=file]").set_input_files(local_image_path, timeout=10000)
        frame.page.wait_for_timeout(2500)
        _click_save_and_wait(frame)
        return {"slot": slot_idx, "status": "ok", "path": local_image_path}
    except Exception:
        _click_cancel(frame)
        raise


def replace_many(
    *,
    ez_url: str,
    user_data_dir: str,
    pairs: list[tuple[int, str]],
    language: str = "Italian",
    replace_existing: bool = True,
    port: int = DEFAULT_CDP_PORT,
    limit: int | None = None,
) -> list[dict]:
    ensure_cdp_chrome(user_data_dir, ez_url, port=port)
    results: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(_cdp_ws_endpoint(port))
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(15000)
        page = context.new_page()
        try:
            page.goto(ez_url, wait_until="domcontentloaded", timeout=30000)
            frame = _wait_plugin_frame(page)
            selected_pairs = pairs[:limit] if limit is not None else pairs
            for slot_idx, path in selected_pairs:
                try:
                    frame = _wait_plugin_frame(page)
                    results.append(
                        replace_slot(
                            frame,
                            slot_idx,
                            path,
                            language=language,
                            replace_existing=replace_existing,
                        )
                    )
                except Exception as exc:
                    results.append({
                        "slot": slot_idx,
                        "status": "failed",
                        "path": path,
                        "error": str(exc),
                    })
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
    return results
