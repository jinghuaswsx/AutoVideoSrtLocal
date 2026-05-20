"""Mingkong/wedev login recovery through the visible DXM02-MK Chrome.

Docs-anchor:
docs/superpowers/specs/2026-05-20-mingkong-product-local-aggregate-stats-design.md#mingkong-auto-login-recovery
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests

from appcore import settings as system_settings
from appcore.browser_automation_lock import BrowserAutomationLockTimeout, browser_automation_lock


DEFAULT_WEDEV_BASE_URL = "https://os.wedev.vip"
DEFAULT_LOGIN_URL = f"{DEFAULT_WEDEV_BASE_URL}/login?redirect=/home"
DEFAULT_MINGKONG_CDP_URL = "http://127.0.0.1:9223"
DEFAULT_LOCK_PATH = Path("/data/autovideosrt/browser/runtime-mk-selection/automation.lock")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")


def _configured_cdp_url() -> str:
    return (os.environ.get("MINGKONG_CDP_URL") or DEFAULT_MINGKONG_CDP_URL).strip()


def _configured_lock_path() -> Path:
    configured = os.environ.get("MINGKONG_CDP_LOCK_PATH")
    if configured:
        return Path(configured)
    if DEFAULT_LOCK_PATH.parent.exists() or Path("/data/autovideosrt").exists():
        return DEFAULT_LOCK_PATH
    return Path("output") / "browser_automation" / "mingkong_cdp.lock"


def _dispatch_autofill_input_events(page: Any) -> None:
    try:
        page.evaluate(
            """
            () => {
              for (const input of document.querySelectorAll('input')) {
                if (!input.value) continue;
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                input.blur();
              }
            }
            """
        )
    except Exception:
        pass


def click_saved_login(page: Any, *, wait_ms: int = 5000) -> bool:
    """Wait for Chrome password autofill, then click the visible Mingkong login control."""
    page.wait_for_timeout(wait_ms)
    _dispatch_autofill_input_events(page)
    selectors = (
        'button:has-text("登录")',
        'button:has-text("登 录")',
        'input[type="submit"]',
        'button[type="submit"]',
        ".login button",
    )
    for selector in selectors:
        try:
            raw_locator = page.locator(selector)
            locator = getattr(raw_locator, "first", raw_locator)
            if locator.count() and locator.is_visible(timeout=1000):
                locator.click(timeout=5000)
                return True
        except Exception:
            continue
    for selector in ('input[type="password"]', 'input[autocomplete="current-password"]'):
        try:
            raw_locator = page.locator(selector)
            locator = getattr(raw_locator, "first", raw_locator)
            if locator.count():
                locator.press("Enter", timeout=3000)
                return True
        except Exception:
            continue
    return False


def _local_storage_token(page: Any) -> str:
    try:
        raw = page.evaluate(
            r"""
            () => {
              const preferred = ['token', 'access_token', 'auth_token', 'Authorization', 'authorization'];
              for (const key of preferred) {
                const value = localStorage.getItem(key);
                if (value) return value;
              }
              const jwtRe = /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/;
              for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                const value = localStorage.getItem(key) || '';
                const match = value.match(jwtRe);
                if (match) return match[0];
              }
              return '';
            }
            """
        )
    except Exception:
        raw = ""
    text = str(raw or "").strip()
    match = _JWT_RE.search(text)
    return match.group(0) if match else text


def _format_authorization(token: str) -> str:
    token = str(token or "").strip()
    if not token:
        return ""
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def extract_wedev_credentials(
    context: Any,
    page: Any,
    *,
    base_url: str = DEFAULT_WEDEV_BASE_URL,
) -> dict[str, str]:
    cookies = context.cookies(base_url)
    cookie_header = "; ".join(
        f"{cookie.get('name')}={cookie.get('value')}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value")
    )
    token = _local_storage_token(page)
    if not token:
        for cookie in cookies:
            if cookie.get("name") == "token" and cookie.get("value"):
                token = str(cookie["value"]).strip()
                break
    return {
        "cookie": cookie_header,
        "authorization": _format_authorization(token),
    }


def verify_wedev_credentials(
    *,
    base_url: str,
    credentials: dict[str, str],
    product_code: str = "__credential_probe__",
    timeout_seconds: int = 20,
    session: requests.Session | None = None,
) -> bool:
    headers = {"Accept": "application/json"}
    if credentials.get("authorization"):
        headers["Authorization"] = credentials["authorization"]
    if credentials.get("cookie"):
        headers["Cookie"] = credentials["cookie"]
    if "Authorization" not in headers and "Cookie" not in headers:
        return False
    http = session or requests.Session()
    try:
        resp = http.get(
            f"{base_url.rstrip('/')}/api/marketing/medias",
            params={"page": 1, "q": product_code or "__credential_probe__", "source": "", "level": "", "show_attention": 0},
            headers=headers,
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception:
        return False
    return not (
        data.get("is_guest") is True
        or str(data.get("message") or "").startswith("登录")
    )


def save_wedev_credentials(
    credentials: dict[str, str],
    *,
    base_url: str = DEFAULT_WEDEV_BASE_URL,
) -> None:
    system_settings.set_setting("push_localized_texts_base_url", base_url.rstrip("/"))
    if credentials.get("authorization"):
        system_settings.set_setting("push_localized_texts_authorization", credentials["authorization"])
    if credentials.get("cookie"):
        system_settings.set_setting("push_localized_texts_cookie", credentials["cookie"])


def _refresh_on_page(
    page: Any,
    context: Any,
    *,
    login_url: str,
    base_url: str,
    verify_product_code: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
    clicked = click_saved_login(page, wait_ms=5000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        page.wait_for_timeout(5000)
    credentials = extract_wedev_credentials(context, page, base_url=base_url)
    verified = verify_wedev_credentials(
        base_url=base_url,
        credentials=credentials,
        product_code=verify_product_code,
        timeout_seconds=timeout_seconds,
    )
    if not verified:
        page.wait_for_timeout(5000)
        credentials = extract_wedev_credentials(context, page, base_url=base_url)
        verified = verify_wedev_credentials(
            base_url=base_url,
            credentials=credentials,
            product_code=verify_product_code,
            timeout_seconds=timeout_seconds,
        )
    if not verified:
        return {
            "status": "failed",
            "error": "login_verification_failed",
            "clicked": clicked,
            "current_url": getattr(page, "url", ""),
        }
    save_wedev_credentials(credentials, base_url=base_url)
    return {
        "status": "success",
        "clicked": clicked,
        "current_url": getattr(page, "url", ""),
        "has_cookie": bool(credentials.get("cookie")),
        "has_authorization": bool(credentials.get("authorization")),
    }


def refresh_wedev_credentials_via_cdp(
    *,
    cdp_url: str | None = None,
    login_url: str = DEFAULT_LOGIN_URL,
    base_url: str = DEFAULT_WEDEV_BASE_URL,
    verify_product_code: str = "__credential_probe__",
    timeout_seconds: int = 20,
    page_factory: Any | None = None,
    close_page: bool = True,
) -> dict[str, Any]:
    """Refresh saved wedev credentials using the server-visible Mingkong browser."""
    if page_factory is not None:
        page = page_factory()
        context = getattr(page, "context", None) or getattr(page, "_context", None)
        return _refresh_on_page(
            page,
            context,
            login_url=login_url,
            base_url=base_url,
            verify_product_code=verify_product_code,
            timeout_seconds=timeout_seconds,
        )

    from playwright.sync_api import sync_playwright

    chosen_cdp = cdp_url or _configured_cdp_url()
    try:
        with browser_automation_lock(
            task_code="mingkong_login_autofill",
            timeout_seconds=int(os.environ.get("MINGKONG_CDP_LOCK_TIMEOUT_SECONDS", "600")),
            retry_seconds=int(os.environ.get("MINGKONG_CDP_LOCK_RETRY_SECONDS", "5")),
            command=login_url,
            lock_path=_configured_lock_path(),
        ):
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(chosen_cdp)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                try:
                    return _refresh_on_page(
                        page,
                        context,
                        login_url=login_url,
                        base_url=base_url,
                        verify_product_code=verify_product_code,
                        timeout_seconds=timeout_seconds,
                    )
                finally:
                    if close_page:
                        try:
                            page.close()
                        except Exception:
                            pass
    except BrowserAutomationLockTimeout as exc:
        return {"status": "lock_timeout", "error": str(exc)}
