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
from typing import Callable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer import cancellation
from tools.shopify_image_localizer.browser import session


DEFAULT_CDP_PORT = 7777
ACTION_DELAY_MS = 1000
UPLOAD_FILE_READY_TIMEOUT_MS = 20000


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
    _log(f"{scope} START {label}")
    try:
        result = action()
    except Exception as exc:
        _log(f"{scope} END {label}: failed ({_duration_s(started_at)}) error={exc}")
        raise
    status = detail(result) if detail is not None else "ok"
    _log(f"{scope} END {label}: {status} ({_duration_s(started_at)})")
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

    _run_step(scope, f"wait 1s after {label}", pause)


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
    cancel_token: cancellation.CancellationToken | None = None,
) -> bool:
    """Start normal Chrome with a CDP port if needed.

    Returns True when this call starts Chrome, False when an existing CDP Chrome
    is reused. No Playwright launch flags are used.
    """
    if _cdp_alive(port):
        return False
    session.kill_chrome_for_profile(user_data_dir)
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
        cancellation.throw_if_cancelled(cancel_token)
        if _cdp_alive(port):
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
        page.wait_for_timeout(500)
    raise RuntimeError("EZ freshify iframe 未加载或未出现图片按钮")


def _dialog_text(frame) -> str:
    return frame.locator("[role=dialog]").inner_text(timeout=5000) or ""


def _modal_hash(frame) -> str | None:
    text = _dialog_text(frame)
    match = re.search(r"translation for:\s*([a-f0-9]{28,})\.", text, re.I)
    return match.group(1).lower() if match else None


def _click_save_and_wait(frame) -> dict:
    frame.locator('button:has-text("Save")').click(timeout=5000)
    try:
        frame.locator("[role=dialog]").wait_for(state="detached", timeout=15000)
        return {"dialog_closed": True}
    except PlaywrightTimeoutError:
        frame.page.wait_for_timeout(2500)
        return {"dialog_closed": False, "fallback_wait_ms": 2500}


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


def verify_target_language_markers(frame, expected_slots: list[int], language: str) -> dict:
    rows = frame.evaluate(
        """() => Array.from(document.querySelectorAll('s-button.image-button')).map((button, idx) => {
            const container = button.closest('tr, li, [data-index], .Polaris-IndexTable__TableRow, div') || button.parentElement || button;
            const text = (container.textContent || '').trim();
            const labels = Array.from(container.querySelectorAll('[aria-label], button, span, s-badge'))
                .map((node) => node.getAttribute('aria-label') || node.textContent || '')
                .map((value) => value.trim())
                .filter(Boolean);
            return {slot: idx, text, languages: labels};
        })"""
    ) or []
    wanted = str(language or "").strip().lower()
    expected = {int(slot) for slot in expected_slots}
    matched: list[int] = []
    missing: list[int] = []
    for slot in sorted(expected):
        row = next((item for item in rows if int(item.get("slot") or 0) == slot), None)
        labels = " ".join(str(value) for value in ((row or {}).get("languages") or []))
        text = f"{(row or {}).get('text') or ''} {labels}".lower()
        if wanted and wanted in text:
            matched.append(slot)
        else:
            missing.append(slot)
    return {
        "ok": not missing,
        "expected": len(expected),
        "matched": len(matched),
        "missing": missing,
    }


def verify_many_language_markers(
    *,
    ez_url: str,
    user_data_dir: str,
    expected_slots: list[int],
    language: str = "Italian",
    port: int = DEFAULT_CDP_PORT,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict:
    ensure_cdp_chrome(user_data_dir, ez_url, port=port, cancel_token=cancel_token)
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
    scope = f"[carousel][slot {slot_idx}]"
    cancellation.throw_if_cancelled(cancel_token)
    local_path = Path(local_image_path)
    local_hash = md5_token(local_path.name)
    exists = local_path.is_file()
    size = local_path.stat().st_size if exists else 0
    started_at = time.perf_counter()
    _log(
        f"{scope} START replace path={local_image_path} file={local_path.name} "
        f"exists={exists} size={size} language={language} hash={local_hash or '-'}"
    )
    try:
        open_info = _run_step(
            scope,
            "open translation dialog",
            lambda: _open_slot(frame, slot_idx, local_hash),
            lambda value: (
                f"ok visible_buttons={dict(value).get('visible_buttons')} "
                f"modal_hash={dict(value).get('modal_hash') or '-'}"
            ),
        )
        _pause_after_action(frame, scope, "open translation dialog", cancel_token=cancel_token)

        cancellation.throw_if_cancelled(cancel_token)
        target_exists = bool(_run_step(
            scope,
            f"check existing {language} marker",
            lambda: _target_exists(frame, language),
            lambda value: f"exists={bool(value)}",
        ))
        if target_exists:
            _run_step(
                scope,
                "close dialog after skip",
                lambda: _click_cancel(frame),
                lambda value: f"closed={bool(value)}",
            )
            _log(f"{scope} RESULT skipped reason={language} already exists total={_duration_s(started_at)}")
            return {"slot": slot_idx, "status": "skipped", "reason": f"{language} already exists"}

        cancellation.throw_if_cancelled(cancel_token)
        language_info = _run_step(
            scope,
            f"select language {language}",
            lambda: _select_language(frame, language),
            lambda value: f"ok value={dict(value).get('value') or '-'}",
        )
        _pause_after_action(frame, scope, "select language", cancel_token=cancel_token)

        cancellation.throw_if_cancelled(cancel_token)
        file_state = _run_step(
            scope,
            "set upload file",
            lambda: _set_upload_file(frame, local_image_path, cancel_token=cancel_token),
            lambda value: (
                f"ok selected_files={','.join(dict(value).get('names') or []) or '-'} "
                f"input_count={int(dict(value).get('count') or 0)} "
                f"continued={bool(dict(value).get('continued'))}"
            ),
        )
        _pause_after_action(frame, scope, "set upload file", cancel_token=cancel_token)

        cancellation.throw_if_cancelled(cancel_token)
        save_info = _run_step(
            scope,
            "click Save and wait dialog close",
            lambda: _click_save_and_wait(frame),
            lambda value: f"ok dialog_closed={bool(dict(value).get('dialog_closed'))}",
        )
        _pause_after_action(frame, scope, "save", cancel_token=cancel_token)
        _log(f"{scope} RESULT ok total={_duration_s(started_at)}")
        return {"slot": slot_idx, "status": "ok", "path": local_image_path}
    except Exception as exc:
        _log(f"{scope} RESULT failed total={_duration_s(started_at)} error={exc}")
        _run_step(
            scope,
            "close dialog after failure",
            lambda: _click_cancel(frame),
            lambda value: f"closed={bool(value)}",
        )
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
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict]:
    started_at = time.perf_counter()
    selected_pairs = pairs[:limit] if limit is not None else pairs
    _log(
        f"[carousel] START replace_many url={ez_url} language={language} "
        f"requested={len(selected_pairs)} total_pairs={len(pairs)} limit={limit or 'all'}"
    )
    _run_step(
        "[carousel]",
        "ensure Chrome CDP",
        lambda: ensure_cdp_chrome(user_data_dir, ez_url, port=port, cancel_token=cancel_token),
        lambda value: f"ok started_new_chrome={bool(value)} port={port}",
    )
    results: list[dict] = []
    try:
        with sync_playwright() as playwright:
            browser = _run_step(
                "[carousel]",
                "connect Chrome CDP",
                lambda: playwright.chromium.connect_over_cdp(_cdp_ws_endpoint(port)),
                lambda value: f"ok contexts={len(getattr(value, 'contexts', []) or [])}",
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            context.set_default_timeout(15000)
            page = context.new_page()
            try:
                cancellation.throw_if_cancelled(cancel_token)
                _run_step(
                    "[carousel]",
                    "open EZ page",
                    lambda: page.goto(ez_url, wait_until="domcontentloaded", timeout=30000),
                    lambda _value: "ok wait_until=domcontentloaded",
                )
                frame = _run_step(
                    "[carousel]",
                    "wait EZ iframe and image buttons",
                    lambda: _wait_plugin_frame(page, cancel_token=cancel_token),
                    lambda value: f"ok frame_url={getattr(value, 'url', '') or '-'}",
                )
                scan_result = _run_step(
                    "[carousel]",
                    "scan existing language markers",
                    lambda: filter_pairs_missing_language_markers(frame, selected_pairs, language),
                    lambda value: f"ok skipped={len(value[0])} pending={len(value[1])}",
                )
                skipped_results, pending_pairs = scan_result
                results.extend(skipped_results)
                if skipped_results:
                    _log(
                        f"[carousel] {len(skipped_results)} slot(s) already have {language}; "
                        f"pending {len(pending_pairs)}"
                    )
                if not pending_pairs and selected_pairs:
                    _log(f"[carousel] all {len(selected_pairs)} slot(s) already have {language}; skipping upload")
                for slot_idx, path in pending_pairs:
                    cancellation.throw_if_cancelled(cancel_token)
                    _log(f"[carousel][slot {slot_idx}] QUEUED path={path}")
                    try:
                        frame = _run_step(
                            f"[carousel][slot {slot_idx}]",
                            "refresh EZ iframe before slot",
                            lambda: _wait_plugin_frame(page, cancel_token=cancel_token),
                            lambda value: f"ok frame_url={getattr(value, 'url', '') or '-'}",
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
                        _log(f"[carousel][slot {slot_idx}] FINAL status={row.get('status')} path={path}")
                    except cancellation.OperationCancelled:
                        raise
                    except Exception as exc:
                        _log(f"[carousel][slot {slot_idx}] FINAL status=failed error={exc} path={path}")
                        results.append({
                            "slot": slot_idx,
                            "status": "failed",
                            "path": path,
                            "error": str(exc),
                        })
            finally:
                try:
                    _run_step("[carousel]", "close EZ automation page", page.close)
                except Exception:
                    pass
                try:
                    _run_step("[carousel]", "disconnect Chrome CDP", browser.close)
                except Exception:
                    pass
    except Exception as exc:
        _log(f"[carousel] RESULT failed total={_duration_s(started_at)} error={exc}")
        raise
    ok_count = sum(1 for row in results if row.get("status") == "ok")
    skipped_count = sum(1 for row in results if row.get("status") == "skipped")
    failed_count = sum(1 for row in results if row.get("status") not in {"ok", "skipped"})
    _log(
        f"[carousel] RESULT done requested={len(selected_pairs)} ok={ok_count} "
        f"skipped={skipped_count} failed={failed_count} total={_duration_s(started_at)}"
    )
    return results
