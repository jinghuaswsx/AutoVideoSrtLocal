"""Meta/Facebook login autofill for server browser CDP sessions.

This module intentionally never logs credentials.
Docs-anchor: docs/superpowers/specs/2026-05-08-meta-login-plaintext-autofill-design.md
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from appcore import browser_login_credentials
from appcore.browser_automation_lock import BrowserAutomationLockTimeout
from appcore.meta_ads_cdp import meta_ads_cdp_lock


LOGIN_MARKERS = (
    "business.facebook.com/business/loginpage",
    "facebook.com/login",
    "log into ads manager",
    "log in with facebook",
)
HUMAN_REQUIRED_MARKERS = (
    "checkpoint",
    "two-factor",
    "two factor",
    "authentication code",
    "enter code",
    "captcha",
    "confirm your identity",
    "secure your account",
)


def build_ads_manager_campaigns_url(
    target_date: date,
    *,
    account_id: str,
    business_id: str,
) -> str:
    ds = target_date.isoformat()
    return (
        "https://adsmanager.facebook.com/adsmanager/manage/campaigns?"
        f"act={str(account_id).removeprefix('act_')}&business_id={business_id}&global_scope_id={business_id}"
        "&attribution_windows=default&column_preset=1658418688523178"
        f"&date={ds}_{ds}&insights_date={ds}_{ds}&insights_selected_metrics=cpm"
    )


def classify_meta_login_state(url: str, body_text: str = "") -> str:
    haystack = f"{url or ''}\n{body_text or ''}".lower()
    if any(marker in haystack for marker in HUMAN_REQUIRED_MARKERS):
        return "needs_human"
    if any(marker in haystack for marker in LOGIN_MARKERS):
        return "login_required"
    return "logged_in"


def fill_facebook_login_page(page: Any, username: str, password: str) -> None:
    page.locator("input[name=email]").fill(username, timeout=10000)
    page.locator("input[name=pass]").fill(password, timeout=10000)
    page.locator("input[name=pass]").press("Enter")


def _page_body_text(page: Any) -> str:
    if hasattr(page, "body_text"):
        return str(page.body_text or "")
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _page_title(page: Any) -> str:
    try:
        title_attr = getattr(page, "title")
        return title_attr() if callable(title_attr) else str(title_attr or "")
    except Exception:
        return ""


def _ensure_meta_login_on_page(
    page: Any,
    *,
    env_code: str,
    provider: str,
    target_url: str | None,
) -> dict[str, Any]:
    state = classify_meta_login_state(getattr(page, "url", ""), _page_body_text(page))
    if state == "logged_in":
        browser_login_credentials.mark_login_result(env_code, provider, "already_logged_in", None)
        return {"status": "already_logged_in", "title": _page_title(page), "current_url": getattr(page, "url", "")}
    if state == "needs_human":
        browser_login_credentials.mark_login_result(env_code, provider, "needs_human", "checkpoint_required")
        return {"status": "needs_human", "error": "checkpoint_required", "current_url": getattr(page, "url", "")}

    credential = browser_login_credentials.get_credential(env_code, provider)
    if not credential:
        browser_login_credentials.mark_login_result(env_code, provider, "failed", "missing_credential")
        return {"status": "missing_credential", "error": "missing_credential", "current_url": getattr(page, "url", "")}

    fill_facebook_login_page(page, credential.username, credential.password)
    try:
        page.wait_for_timeout(10000)
    except Exception:
        pass
    if target_url and classify_meta_login_state(getattr(page, "url", ""), _page_body_text(page)) == "logged_in":
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
        except Exception:
            pass

    final_state = classify_meta_login_state(getattr(page, "url", ""), _page_body_text(page))
    if final_state == "logged_in":
        browser_login_credentials.mark_login_result(env_code, provider, "success", None)
        return {"status": "success", "title": _page_title(page), "current_url": getattr(page, "url", "")}
    if final_state == "needs_human":
        browser_login_credentials.mark_login_result(env_code, provider, "needs_human", "checkpoint_required")
        return {"status": "needs_human", "error": "checkpoint_required", "current_url": getattr(page, "url", "")}

    browser_login_credentials.mark_login_result(env_code, provider, "failed", "login_still_required")
    return {"status": "failed", "error": "login_still_required", "current_url": getattr(page, "url", "")}


def ensure_meta_login(
    cdp_url: str,
    *,
    env_code: str = browser_login_credentials.DEFAULT_ENV_CODE,
    provider: str = browser_login_credentials.DEFAULT_PROVIDER,
    target_url: str | None = None,
    page_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Ensure Meta Ads Manager login, autofilling Facebook credentials if needed."""
    if page_factory is not None:
        return _ensure_meta_login_on_page(
            page_factory(),
            env_code=env_code,
            provider=provider,
            target_url=target_url,
        )

    from playwright.sync_api import sync_playwright

    try:
        with meta_ads_cdp_lock(
            task_code="meta_login_autofill",
            command=target_url or cdp_url,
        ):
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0]
                page = context.new_page()
                try:
                    if target_url:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(8000)
                    return _ensure_meta_login_on_page(
                        page,
                        env_code=env_code,
                        provider=provider,
                        target_url=target_url,
                    )
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
    except BrowserAutomationLockTimeout as exc:
        browser_login_credentials.mark_login_result(env_code, provider, "failed", "lock_timeout")
        return {"status": "lock_timeout", "error": str(exc)}
