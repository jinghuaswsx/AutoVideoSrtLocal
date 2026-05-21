from __future__ import annotations

"""EZ Product Translate automation through external Chrome CDP.

This keeps the important part of the working route: Chrome is launched as a
normal detached user browser with the existing Shopify profile. Playwright only
connects to that browser over CDP after it is running, then drives the EZ iframe
DOM and file inputs directly.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer import cancellation
from tools.shopify_image_localizer.browser import session


DEFAULT_CDP_PORT = 7777
ACTION_DELAY_MS = 1000
UPLOAD_FILE_READY_TIMEOUT_MS = 20000
POST_SAVE_MARKER_VERIFY_TIMEOUT_MS = 10000
POST_SAVE_MARKER_VERIFY_INTERVAL_MS = 1000
POST_SAVE_MARKER_MAX_ATTEMPTS = 2
STARTUP_URL = "https://www.google.com"
MAX_BROWSER_TABS = 20


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    print(f"[{_timestamp()}] {message}", flush=True)


def _duration_s(started_at: float) -> str:
    return f"{time.perf_counter() - started_at:.2f}s"


def _run_step(
    scope: str,
    label: str,
    action: Callable[[], object],
    detail: Callable[[object], str] | None = None,
) -> object:
    started_at = time.perf_counter()
    _log(f"{scope} 开始：{label}")
    try:
        result = action()
    except Exception as exc:
        _log(f"{scope} 失败：{label}（耗时 {_duration_s(started_at)}）错误={exc}")
        raise
    status = detail(result) if detail is not None else "ok"
    _log(f"{scope} 完成：{label}（耗时 {_duration_s(started_at)}）{status}")
    return result


def _pause_after_action(
    frame,
    scope: str,
    label: str,
    *,
    cancel_token: cancellation.CancellationToken | None = None,
) -> None:
    def pause() -> None:
        cancellation.throw_if_cancelled(cancel_token)
        frame.page.wait_for_timeout(ACTION_DELAY_MS)
        cancellation.throw_if_cancelled(cancel_token)

    _run_step(scope, f"{label} 后等待 1 秒", pause)


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


def _normalize_path_token(value: str) -> str:
    return str(value or "").strip().strip("\"'").replace("\\", "/").rstrip("/").lower()


def _commandline_uses_profile(command_line: str, user_data_dir: str) -> bool:
    target = _normalize_path_token(user_data_dir)
    if not target:
        return False
    pattern = r"--user-data-dir(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)"
    for match in re.finditer(pattern, str(command_line or "")):
        if _normalize_path_token(match.group(1)) == target:
            return True
    return False


def _hidden_subprocess_kwargs() -> dict[str, object]:
    return session._hidden_subprocess_kwargs()


def _cdp_port_matches_profile(port: int, user_data_dir: str) -> bool:
    if os.name != "nt":
        return True
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' -and "
                    f"$_.CommandLine -match 'remote-debugging-port={int(port)}' }} | "
                    "ForEach-Object { $_.CommandLine }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=6,
            **_hidden_subprocess_kwargs(),
        )
    except Exception:
        return False
    return any(
        _commandline_uses_profile(line, user_data_dir)
        for line in (result.stdout or "").splitlines()
    )


def _kill_cdp_chrome_for_port(port: int) -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' -and "
                    f"$_.CommandLine -match 'remote-debugging-port={int(port)}' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                ),
            ],
            capture_output=True,
            timeout=8,
            **_hidden_subprocess_kwargs(),
        )
    except Exception:
        pass


def _chrome_exe() -> str:
    found = session.find_chrome_executable()
    if found:
        return found
    which = shutil.which("chrome")
    if which:
        return which
    raise RuntimeError("未找到 chrome.exe")


def _startup_urls(initial_url: str | None = None) -> list[str]:
    urls = [STARTUP_URL]
    candidate = str(initial_url or "").strip()
    if candidate and candidate.rstrip("/") != STARTUP_URL.rstrip("/"):
        urls.append(candidate)
    return urls


def _page_url(page) -> str:
    return str(getattr(page, "url", "") or "")


def _is_google_url(url: str | None) -> bool:
    normalized = str(url or "").strip().lower().rstrip("/")
    return normalized in {"https://www.google.com", "http://www.google.com", "https://google.com", "http://google.com"}


def _is_restored_invalid_tool_url(url: str | None) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() == "ez"


def close_restored_invalid_tool_tabs(context, *, keep_pages: list | tuple = ()) -> int:
    pages = list(getattr(context, "pages", None) or [])
    keep_ids = {id(page) for page in keep_pages}
    closed = 0
    for page in reversed(pages):
        if id(page) in keep_ids:
            continue
        if not _is_restored_invalid_tool_url(_page_url(page)):
            continue
        try:
            page.close()
            closed += 1
        except Exception:
            pass
    return closed


def ensure_google_home_tab(context) -> None:
    close_restored_invalid_tool_tabs(context)
    pages = list(getattr(context, "pages", None) or [])
    if not pages:
        page = context.new_page()
        page.goto(STARTUP_URL, wait_until="domcontentloaded", timeout=30000)
        return
    first = pages[0]
    first_url = _page_url(first)
    if _is_google_url(first_url):
        return
    first.goto(STARTUP_URL, wait_until="domcontentloaded", timeout=30000)


def _target_page_token(target_url: str) -> str:
    value = str(target_url or "")
    if "ez-product-image-translate" in value:
        return "ez-product-image-translate"
    if "translate-and-adapt" in value:
        return "translate-and-adapt"
    if "/products/" in value:
        return "/products/"
    return value


def select_or_create_business_page(context, target_url: str):
    close_restored_invalid_tool_tabs(context)
    token = _target_page_token(target_url)
    pages = [page for page in list(getattr(context, "pages", None) or []) if not _is_restored_invalid_tool_url(_page_url(page))]
    for page in pages[1:]:
        if token and token in _page_url(page):
            return page
    return context.new_page()


def prune_browser_tabs(context, *, keep_pages: list | tuple = (), max_tabs: int = MAX_BROWSER_TABS) -> None:
    close_restored_invalid_tool_tabs(context, keep_pages=keep_pages)
    pages = list(getattr(context, "pages", None) or [])
    keep_ids = {id(page) for page in keep_pages}
    for page in reversed(pages[1:]):
        if len(pages) <= max_tabs:
            return
        if id(page) in keep_ids:
            continue
        try:
            page.close()
        except Exception:
            pass
        with contextlib.suppress(ValueError):
            pages.remove(page)


def _ensure_google_home_tab_via_cdp(port: int) -> None:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(_cdp_ws_endpoint(port))
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                ensure_google_home_tab(context)
                prune_browser_tabs(context)
            finally:
                with contextlib.suppress(Exception):
                    browser.close()
    except Exception:
        pass


def open_managed_tab(
    *,
    user_data_dir: str,
    target_url: str,
    port: int = DEFAULT_CDP_PORT,
    cancel_token: cancellation.CancellationToken | None = None,
) -> None:
    ensure_cdp_chrome(user_data_dir, port=port, cancel_token=cancel_token)
    cancellation.throw_if_cancelled(cancel_token)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(_cdp_ws_endpoint(port))
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            ensure_google_home_tab(context)
            page = select_or_create_business_page(context, target_url)
            prune_browser_tabs(context, keep_pages=(page,))
            try:
                page.bring_to_front()
            except Exception:
                pass
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        finally:
            with contextlib.suppress(Exception):
                browser.close()


def ensure_cdp_chrome(
    user_data_dir: str,
    initial_url: str = STARTUP_URL,
    *,
    port: int = DEFAULT_CDP_PORT,
    proxy_server: str | None = None,
    startup_timeout_s: int = 30,
    cancel_token: cancellation.CancellationToken | None = None,
) -> bool:
    """Start normal Chrome with a CDP port if needed.

    Returns True when this call starts Chrome, False when an existing CDP Chrome
    is reused. No Playwright launch flags are used.
    """
    if _cdp_alive(port):
        if _cdp_port_matches_profile(port, user_data_dir):
            _ensure_google_home_tab_via_cdp(port)
            return False
        _kill_cdp_chrome_for_port(port)
        deadline = time.time() + 5
        while time.time() < deadline and _cdp_alive(port):
            cancellation.throw_if_cancelled(cancel_token)
            cancellation.cancellable_sleep(cancel_token, 0.25)
    session.kill_chrome_for_profile(user_data_dir)
    if proxy_server is None:
        proxy_server = session.detect_system_proxy()
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    session._enable_chrome_developer_mode(user_data_dir)
    args = [
        _chrome_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    ext_dir = session._resolve_bundled_extension_dir()
    if ext_dir:
        args.append(f"--load-extension={ext_dir}")
    if proxy_server:
        args.extend([
            f"--proxy-server={proxy_server}",
            "--proxy-bypass-list=127.0.0.1;localhost;172.16.254.106;<local>",
        ])
    args.extend(_startup_urls(initial_url))

    subprocess.Popen(
        args,
        creationflags=0x00000008 | 0x00000200 if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.time() + startup_timeout_s
    while time.time() < deadline:
        cancellation.throw_if_cancelled(cancel_token)
        if _cdp_alive(port):
            _ensure_google_home_tab_via_cdp(port)
            return True
        cancellation.cancellable_sleep(cancel_token, 0.5)
    raise RuntimeError(f"Chrome CDP 127.0.0.1:{port} 未就绪")


def md5_token(value: str) -> str | None:
    match = re.search(r"([a-f0-9]{28,})", (value or "").lower())
    return match.group(1) if match else None


def _find_plugin_frame(page):
    for frame in page.frames:
        if "translate.freshify.click" in (frame.url or ""):
            return frame
    return None


_EZ_PRODUCT_LOAD_ERROR_MARKERS = (
    "impossible de charger les donnees du produit",
    "unable to load product data",
    "无法加载产品数据",
    "无法加载商品数据",
)


def _fold_error_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _ez_product_load_error_text(page) -> str:
    try:
        text = page.locator("body").inner_text(timeout=500) or ""
    except Exception:
        return ""
    folded = _fold_error_text(text)
    if not any(marker in folded for marker in _EZ_PRODUCT_LOAD_ERROR_MARKERS):
        return ""
    return " ".join(str(text).split())[:500]


def _wait_plugin_frame(page, *, timeout_s: int = 30, cancel_token: cancellation.CancellationToken | None = None):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cancellation.throw_if_cancelled(cancel_token)
        frame = _find_plugin_frame(page)
        if frame is not None:
            try:
                if frame.locator("s-button.image-button").count() > 0:
                    return frame
            except Exception:
                pass
        error_text = _ez_product_load_error_text(page)
        if error_text:
            page_url = str(getattr(page, "url", "") or "").strip()
            url_hint = f" 当前 URL：{page_url}" if page_url else ""
            raise RuntimeError(
                "EZ 页面无法加载商品数据；通常是当前网站缓存的 Shopify 店铺编码与商品 ID 不匹配，"
                "或该商品不属于当前店铺。请重新选择正确店铺并点击「已登录」刷新缓存后再试。"
                f"{url_hint} EZ 提示：{error_text}"
            )
        page.wait_for_timeout(500)
    raise RuntimeError("EZ freshify iframe 未加载或未出现图片按钮")


def _dialog_text(frame) -> str:
    return frame.locator("[role=dialog]").inner_text(timeout=5000) or ""


def _modal_hash(frame) -> str | None:
    text = _dialog_text(frame)
    match = re.search(r"translation for:\s*([a-f0-9]{28,})\.", text, re.I)
    return match.group(1).lower() if match else None


_SAVE_BUTTON_CANDIDATES = (
    # freshify 当前版本（含 omurio）实际按钮文字是 "Upgrade"——必须放最前面，避免命中
    # 其它弹窗里同存的 Save / Upload 按钮（已知事故 2026-05-09 v3.8）。
    'button:has-text("Upgrade")',
    'button:has-text("Save")',
    'button:has-text("Upload")',
    'button:has-text("Update")',
    'button:has-text("Apply")',
    'button:has-text("Confirm")',
    'button:has-text("Submit")',
    'button:has-text("升级")',
    'button:has-text("保存")',
    'button:has-text("上传")',
    'button:has-text("更新")',
    'button:has-text("应用")',
    'button:has-text("确认")',
    'button:has-text("确定")',
)


_CANCEL_SIBLING_SUBMIT_SELECTOR = (
    'xpath=//button[contains(., "Cancel") or contains(., "取消")]'
    '/following-sibling::button[1]'
)


def _click_save_and_wait(frame) -> dict:
    """点确认/上传按钮提交 EZ 弹窗。

    优先按文本候选匹配（覆盖 Upgrade / Save / Upload 等已知 freshify 版本）。
    全部都没命中时，走结构 fallback：定位 "Cancel" 按钮 + 取它**右边的下一个 button 兄弟**——
    EZ 弹窗布局始终是 `Cancel | <提交>`，这条 fallback 与按钮文字解耦，应对未来文字变体（已知事故
    2026-05-09 v3.8 漏掉 "Upgrade"，2026-05-09 用户反馈："找 Cancel 右边的按钮"）。
    """
    last_err: Exception | None = None
    matched: str | None = None
    for selector in _SAVE_BUTTON_CANDIDATES:
        try:
            loc = frame.locator(selector).first
            loc.wait_for(state="visible", timeout=2000)
            loc.click(timeout=2000)
            matched = selector
            break
        except Exception as exc:  # PlaywrightTimeoutError 或元素不可见
            last_err = exc
            continue
    if matched is None:
        # 文本候选全部 miss → 用 Cancel 右兄弟做结构兜底
        try:
            sibling = frame.locator(_CANCEL_SIBLING_SUBMIT_SELECTOR).first
            sibling.wait_for(state="visible", timeout=2000)
            sibling.click(timeout=2000)
            matched = _CANCEL_SIBLING_SUBMIT_SELECTOR
        except Exception as exc:
            raise RuntimeError(
                "未在弹窗里找到提交按钮（候选文本全部 miss，Cancel 右兄弟兜底也失败）："
                f"text_err={last_err} sibling_err={exc}"
            )
    try:
        frame.locator("[role=dialog]").wait_for(state="detached", timeout=15000)
        return {"dialog_closed": True, "matched_selector": matched}
    except PlaywrightTimeoutError:
        frame.page.wait_for_timeout(2500)
        return {"dialog_closed": False, "fallback_wait_ms": 2500, "matched_selector": matched}


def _click_cancel(frame) -> bool:
    try:
        frame.locator('button:has-text("Cancel")').click(timeout=3000)
        frame.page.wait_for_timeout(1000)
        return True
    except Exception:
        return False


def _open_slot(frame, slot_idx: int, expected_hash: str | None) -> dict:
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
    return {"visible_buttons": count, "modal_hash": actual_hash or "", "expected_hash": expected_hash or ""}


def _target_exists(frame, language: str) -> bool:
    return frame.locator(f'button[aria-label="Remove {language}"]').count() > 0


def _normalize_language_marker(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^remove\s+", "", text, flags=re.IGNORECASE).strip()
    return text.casefold()


def _row_language_marker_candidates(row: dict) -> list[object]:
    labels = [value for value in (row.get("languages") or []) if str(value or "").strip()]
    if labels:
        return labels
    text = str(row.get("text") or "")
    return [value for value in re.split(r"[\r\n\t,;|]+", text) if value.strip()]


def _row_has_language_marker(row: dict | None, language: str) -> bool:
    wanted = _normalize_language_marker(language)
    if not row or not wanted:
        return False
    return any(_normalize_language_marker(value) == wanted for value in _row_language_marker_candidates(row))


def verify_target_language_markers(frame, expected_slots: list[int], language: str) -> dict:
    rows = frame.evaluate(
        """() => {
            const slotContainer = (button) => {
                let candidate = button;
                let node = button.parentElement;
                for (let depth = 0; node && depth < 12; depth += 1, node = node.parentElement) {
                    const imageButtonCount = node.querySelectorAll('s-button.image-button').length;
                    if (imageButtonCount > 1) break;
                    if (imageButtonCount === 1) candidate = node;
                }
                return candidate || button.parentElement || button;
            };
            return Array.from(document.querySelectorAll('s-button.image-button')).map((button, idx) => {
                const container = slotContainer(button);
                const text = (container.textContent || '').trim();
                const labels = Array.from(container.querySelectorAll('[aria-label], button, span, s-badge'))
                    .map((node) => node.getAttribute('aria-label') || node.textContent || '')
                    .map((value) => value.trim())
                    .filter(Boolean);
                return {slot: idx, text, languages: labels};
            });
        }"""
    ) or []
    expected = {int(slot) for slot in expected_slots}
    matched: list[int] = []
    missing: list[int] = []
    for slot in sorted(expected):
        row = next((item for item in rows if int(item.get("slot") or 0) == slot), None)
        if _row_has_language_marker(row, language):
            matched.append(slot)
        else:
            missing.append(slot)
    return {
        "ok": not missing,
        "expected": len(expected),
        "matched": len(matched),
        "missing": missing,
    }


def _wait_slot_language_marker(
    frame,
    slot_idx: int,
    language: str,
    *,
    timeout_ms: int = POST_SAVE_MARKER_VERIFY_TIMEOUT_MS,
    interval_ms: int = POST_SAVE_MARKER_VERIFY_INTERVAL_MS,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict:
    deadline = time.time() + timeout_ms / 1000
    last_result: dict = {}
    while time.time() < deadline:
        cancellation.throw_if_cancelled(cancel_token)
        last_result = verify_target_language_markers(frame, [slot_idx], language)
        if last_result.get("ok"):
            return last_result
        frame.page.wait_for_timeout(interval_ms)
    raise RuntimeError(f"{language} marker missing after save for slot {slot_idx}: {last_result}")


def _mark_missing_language_markers_failed(
    results: list[dict],
    pairs: list[tuple[int, str]],
    missing_slots: list[int],
    language: str,
) -> None:
    by_path = {int(slot_idx): path for slot_idx, path in pairs}
    for slot_idx in sorted({int(slot) for slot in missing_slots}):
        row = next((item for item in results if int(item.get("slot") or -1) == slot_idx), None)
        error = f"{language} marker missing after final verification"
        if row is None:
            results.append({
                "slot": slot_idx,
                "status": "failed",
                "path": by_path.get(slot_idx, ""),
                "error": error,
            })
            continue
        previous_status = str(row.get("status") or "")
        row["status"] = "failed"
        row["error"] = error
        if previous_status:
            row["previous_status"] = previous_status
        row.setdefault("path", by_path.get(slot_idx, ""))


def verify_many_language_markers(
    *,
    ez_url: str,
    user_data_dir: str,
    expected_slots: list[int],
    language: str = "Italian",
    port: int = DEFAULT_CDP_PORT,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict:
    ensure_cdp_chrome(user_data_dir, port=port, cancel_token=cancel_token)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(_cdp_ws_endpoint(port))
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(15000)
        page = context.new_page()
        try:
            cancellation.throw_if_cancelled(cancel_token)
            page.goto(ez_url, wait_until="domcontentloaded", timeout=30000)
            frame = _wait_plugin_frame(page, cancel_token=cancel_token)
            cancellation.throw_if_cancelled(cancel_token)
            return verify_target_language_markers(frame, expected_slots, language)
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


def _select_language(frame, language: str) -> dict:
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
    return dict(result)


def _uploaded_file_state(frame) -> dict:
    result = frame.evaluate(
        """() => {
            const input = document.querySelector('input[type=file]');
            if (!input) return {ok:false, reason:'no input'};
            const files = Array.from(input.files || []);
            return {
                ok: files.length > 0,
                count: files.length,
                names: files.map((file) => file.name || '')
            };
        }"""
    )
    return dict(result or {})


def _wait_uploaded_file_registered(
    frame,
    *,
    timeout_ms: int = UPLOAD_FILE_READY_TIMEOUT_MS,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict:
    deadline = time.time() + timeout_ms / 1000
    last_state: dict = {}
    while time.time() < deadline:
        cancellation.throw_if_cancelled(cancel_token)
        last_state = _uploaded_file_state(frame)
        if last_state.get("ok") and int(last_state.get("count") or 0) > 0:
            return last_state
        frame.page.wait_for_timeout(500)
    raise RuntimeError(f"上传文件未写入 input[type=file]，最后状态={last_state}")


def _set_upload_file(frame, local_image_path: str, *, cancel_token: cancellation.CancellationToken | None = None) -> dict:
    frame.locator("input[type=file]").set_input_files(local_image_path, timeout=10000)
    frame.page.wait_for_timeout(2500)
    cancellation.throw_if_cancelled(cancel_token)
    state = _uploaded_file_state(frame)
    if not state.get("ok"):
        state["continued"] = True
        state["note"] = "input.files is empty after set_input_files; continue because EZ may clear the input after accepting the upload"
    return state


def filter_pairs_missing_language_markers(frame, pairs: list[tuple[int, str]], language: str) -> tuple[list[dict], list[tuple[int, str]]]:
    expected_slots = [slot_idx for slot_idx, _path in pairs]
    marker_result = verify_target_language_markers(frame, expected_slots, language)
    missing_slots = {int(slot) for slot in marker_result.get("missing") or []}
    skipped: list[dict] = []
    missing_pairs: list[tuple[int, str]] = []
    for slot_idx, path in pairs:
        if int(slot_idx) in missing_slots:
            missing_pairs.append((slot_idx, path))
        else:
            skipped.append({
                "slot": slot_idx,
                "status": "skipped",
                "reason": f"{language} already exists",
                "path": path,
            })
    return skipped, missing_pairs


def replace_slot(
    frame,
    slot_idx: int,
    local_image_path: str,
    *,
    language: str = "Italian",
    replace_existing: bool = True,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict:
    scope = f"[轮播图][位置 {slot_idx}]"
    cancellation.throw_if_cancelled(cancel_token)
    local_path = Path(local_image_path)
    local_hash = md5_token(local_path.name)
    exists = local_path.is_file()
    size = local_path.stat().st_size if exists else 0
    started_at = time.perf_counter()
    _log(
        f"{scope} 开始替换：路径={local_image_path} 文件={local_path.name} "
        f"已存在={exists} 大小={size} 语言={language} 哈希={local_hash or '-'}"
    )
    for attempt in range(1, POST_SAVE_MARKER_MAX_ATTEMPTS + 1):
        attempt_scope = (
            f"{scope}[尝试 {attempt}/{POST_SAVE_MARKER_MAX_ATTEMPTS}]"
            if POST_SAVE_MARKER_MAX_ATTEMPTS > 1 else scope
        )
        try:
            open_info = _run_step(
                attempt_scope,
                "打开翻译对话框",
                lambda: _open_slot(frame, slot_idx, local_hash),
                lambda value: (
                    f"可见按钮数={dict(value).get('visible_buttons')} "
                    f"弹窗哈希={dict(value).get('modal_hash') or '-'}"
                ),
            )
            _pause_after_action(frame, attempt_scope, "打开翻译对话框", cancel_token=cancel_token)

            cancellation.throw_if_cancelled(cancel_token)
            target_exists = bool(_run_step(
                attempt_scope,
                f"检查 {language} 语言标记是否已存在",
                lambda: _target_exists(frame, language),
                lambda value: f"已存在={bool(value)}",
            ))
            if target_exists:
                _run_step(
                    attempt_scope,
                    "已存在，关闭对话框跳过",
                    lambda: _click_cancel(frame),
                    lambda value: f"已关闭={bool(value)}",
                )
                status = "ok" if attempt > 1 else "skipped"
                _log(f"{scope} 结果：{status}，原因={language} 已存在（总耗时 {_duration_s(started_at)}）")
                return {
                    "slot": slot_idx,
                    "status": status,
                    "reason": f"{language} already exists",
                    "path": local_image_path,
                }

            cancellation.throw_if_cancelled(cancel_token)
            language_info = _run_step(
                attempt_scope,
                f"选择语言 {language}",
                lambda: _select_language(frame, language),
                lambda value: f"选中值={dict(value).get('value') or '-'}",
            )
            _pause_after_action(frame, attempt_scope, "选择语言", cancel_token=cancel_token)

            cancellation.throw_if_cancelled(cancel_token)
            file_state = _run_step(
                attempt_scope,
                "设置上传文件",
                lambda: _set_upload_file(frame, local_image_path, cancel_token=cancel_token),
                lambda value: (
                    f"已选文件={','.join(dict(value).get('names') or []) or '-'} "
                    f"input 数量={int(dict(value).get('count') or 0)} "
                    f"已继续={bool(dict(value).get('continued'))}"
                ),
            )
            _pause_after_action(frame, attempt_scope, "设置上传文件", cancel_token=cancel_token)

            cancellation.throw_if_cancelled(cancel_token)
            save_info = _run_step(
                attempt_scope,
                "点击 Save 并等待对话框关闭",
                lambda: _click_save_and_wait(frame),
                lambda value: f"对话框已关闭={bool(dict(value).get('dialog_closed'))}",
            )
            _pause_after_action(frame, attempt_scope, "保存", cancel_token=cancel_token)

            marker_result = _run_step(
                attempt_scope,
                f"回查 {language} 语言标记",
                lambda: _wait_slot_language_marker(
                    frame,
                    slot_idx,
                    language,
                    cancel_token=cancel_token,
                ),
                lambda value: (
                    f"匹配={int(dict(value).get('matched') or 0)}/"
                    f"{int(dict(value).get('expected') or 0)} "
                    f"缺失={dict(value).get('missing') or []}"
                ),
            )
            _log(f"{scope} 结果：成功（总耗时 {_duration_s(started_at)}）")
            return {
                "slot": slot_idx,
                "status": "ok",
                "path": local_image_path,
                "verify": marker_result,
                "attempt": attempt,
            }
        except Exception as exc:
            _run_step(
                attempt_scope,
                "失败后关闭对话框",
                lambda: _click_cancel(frame),
                lambda value: f"已关闭={bool(value)}",
            )
            if attempt < POST_SAVE_MARKER_MAX_ATTEMPTS:
                _log(
                    f"{scope} 第 {attempt} 次尝试失败，准备重试（总耗时 {_duration_s(started_at)}）错误={exc}"
                )
                cancellation.throw_if_cancelled(cancel_token)
                frame.page.wait_for_timeout(2000)
                cancellation.throw_if_cancelled(cancel_token)
                continue
            _log(f"{scope} 结果：失败（总耗时 {_duration_s(started_at)}）错误={exc}")
            raise
    raise RuntimeError(f"{scope} 未完成替换")


def replace_many(
    *,
    ez_url: str,
    user_data_dir: str,
    pairs: list[tuple[int, str]],
    language: str = "Italian",
    replace_existing: bool = True,
    port: int = DEFAULT_CDP_PORT,
    limit: int | None = None,
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict]:
    started_at = time.perf_counter()
    selected_pairs = pairs[:limit] if limit is not None else pairs
    _log(
        f"[轮播图] 开始批量替换：地址={ez_url} 语言={language} "
        f"待处理={len(selected_pairs)} 总配对={len(pairs)} 限制={limit or '不限'}"
    )
    _run_step(
        "[轮播图]",
        "准备 Chrome CDP",
        lambda: ensure_cdp_chrome(user_data_dir, port=port, cancel_token=cancel_token),
        lambda value: f"是否新启动 Chrome={bool(value)} 端口={port}",
    )
    results: list[dict] = []
    try:
        with sync_playwright() as playwright:
            browser = _run_step(
                "[轮播图]",
                "连接 Chrome CDP",
                lambda: playwright.chromium.connect_over_cdp(_cdp_ws_endpoint(port)),
                lambda value: f"context 数={len(getattr(value, 'contexts', []) or [])}",
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            context.set_default_timeout(15000)
            ensure_google_home_tab(context)
            page = select_or_create_business_page(context, ez_url)
            prune_browser_tabs(context, keep_pages=(page,))
            try:
                page.bring_to_front()
            except Exception:
                pass
            try:
                cancellation.throw_if_cancelled(cancel_token)
                _run_step(
                    "[轮播图]",
                    "打开 EZ 页面",
                    lambda: page.goto(ez_url, wait_until="domcontentloaded", timeout=30000),
                    lambda _value: "DOM 加载完成",
                )
                frame = _run_step(
                    "[轮播图]",
                    "等待 EZ iframe 与图片按钮",
                    lambda: _wait_plugin_frame(page, cancel_token=cancel_token),
                    lambda value: f"frame 地址={getattr(value, 'url', '') or '-'}",
                )
                scan_result = _run_step(
                    "[轮播图]",
                    "扫描已有语言标记",
                    lambda: filter_pairs_missing_language_markers(frame, selected_pairs, language),
                    lambda value: f"已跳过={len(value[0])} 待处理={len(value[1])}",
                )
                skipped_results, pending_pairs = scan_result
                results.extend(skipped_results)
                if skipped_results:
                    _log(
                        f"[轮播图] {len(skipped_results)} 个位置已有 {language}；"
                        f"待处理 {len(pending_pairs)} 个"
                    )
                if not pending_pairs and selected_pairs:
                    _log(f"[轮播图] 全部 {len(selected_pairs)} 个位置已有 {language}，跳过上传")
                    _log("[轮播图] 已全部替换到目标语言，停留 5 秒供人工检查确认")
                    cancellation.throw_if_cancelled(cancel_token)
                    page.wait_for_timeout(5000)
                    cancellation.throw_if_cancelled(cancel_token)
                for idx, (slot_idx, path) in enumerate(pending_pairs):
                    cancellation.throw_if_cancelled(cancel_token)
                    _log(f"[轮播图][位置 {slot_idx}] 已入队 路径={path}")
                    try:
                        frame = _run_step(
                            f"[轮播图][位置 {slot_idx}]",
                            "替换前刷新 EZ iframe",
                            lambda: _wait_plugin_frame(page, cancel_token=cancel_token),
                            lambda value: f"frame 地址={getattr(value, 'url', '') or '-'}",
                        )
                        row = replace_slot(
                            frame,
                            slot_idx,
                            path,
                            language=language,
                            replace_existing=replace_existing,
                            cancel_token=cancel_token,
                        )
                        results.append(row)
                        _log(f"[轮播图][位置 {slot_idx}] 完成 状态={row.get('status')} 路径={path}")
                    except cancellation.OperationCancelled:
                        raise
                    except Exception as exc:
                        _log(f"[轮播图][位置 {slot_idx}] 失败 错误={exc} 路径={path}")
                        results.append({
                            "slot": slot_idx,
                            "status": "failed",
                            "path": path,
                            "error": str(exc),
                        })
                    # 节流：避免 Shopify CDN 短时间大量上传被限流（10054 远程主机强迫关闭连接）。
                    # 与 taa_cdp 详情图同款 2 秒节流，最后一个位置不需要等。
                    if idx < len(pending_pairs) - 1:
                        cancellation.cancellable_sleep(cancel_token, 2.0)
                if selected_pairs:
                    frame = _run_step(
                        "[轮播图]",
                        "最终校验前刷新 EZ iframe",
                        lambda: _wait_plugin_frame(page, cancel_token=cancel_token),
                        lambda value: f"frame 地址={getattr(value, 'url', '') or '-'}",
                    )
                    expected_slots = [slot_idx for slot_idx, _path in selected_pairs]
                    final_marker_result = _run_step(
                        "[轮播图]",
                        f"最终校验 {language} 语言标记",
                        lambda: verify_target_language_markers(frame, expected_slots, language),
                        lambda value: (
                            f"匹配={int(dict(value).get('matched') or 0)}/"
                            f"{int(dict(value).get('expected') or 0)} "
                            f"缺失={dict(value).get('missing') or []}"
                        ),
                    )
                    missing_slots = [int(slot) for slot in final_marker_result.get("missing") or []]
                    if missing_slots:
                        _log(f"[轮播图] 最终校验缺失 {language} 标记的位置：{missing_slots}")
                        _mark_missing_language_markers_failed(
                            results,
                            selected_pairs,
                            missing_slots,
                            language,
                        )
            finally:
                try:
                    _run_step("[轮播图]", "关闭 EZ 自动化页面", page.close)
                except Exception:
                    pass
                try:
                    _run_step("[轮播图]", "断开 Chrome CDP", browser.close)
                except Exception:
                    pass
    except Exception as exc:
        _log(f"[轮播图] 整体失败（总耗时 {_duration_s(started_at)}）错误={exc}")
        raise
    ok_count = sum(1 for row in results if row.get("status") == "ok")
    skipped_count = sum(1 for row in results if row.get("status") == "skipped")
    failed_count = sum(1 for row in results if row.get("status") not in {"ok", "skipped"})
    _log(
        f"[轮播图] 整体完成：请求={len(selected_pairs)} 成功={ok_count} "
        f"跳过={skipped_count} 失败={failed_count}（总耗时 {_duration_s(started_at)}）"
    )
    return results
