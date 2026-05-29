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

    # 1. Handle "Continue with Facebook" button if it exists on page
    continue_btn = page.locator('div[role=button]:has-text("Continue with Facebook")')
    login_page = page
    if hasattr(continue_btn, "count") and continue_btn.count() > 0:
        try:
            context = page.context
            # Clicking this opens a popup window
            with context.expect_event("page", timeout=10000) as event_info:
                continue_btn.first.click()
            popup_page = event_info.value
            popup_page.wait_for_load_state("domcontentloaded")
            login_page = popup_page
        except Exception as exc:
            log.warning("failed to click Continue with Facebook or wait for popup: %s", exc)

    # 2. Check if the login_page (which could be the popup) has "Use another profile"
    use_another = login_page.locator('div[aria-label="Use another profile"]')
    if hasattr(use_another, "count") and use_another.count() > 0:
        try:
            use_another.first.evaluate("el => el.click()")
            email_locator = login_page.locator("input[name=email]")
            if hasattr(email_locator, "wait_for"):
                email_locator.wait_for(state="visible", timeout=10000)
        except Exception as exc:
            log.warning("failed to click Use another profile: %s", exc)

    # 3. Fill the credentials on the login_page (popup or original page)
    fill_facebook_login_page(login_page, credential.username, credential.password)
    try:
        login_page.wait_for_timeout(10000)
    except Exception:
        pass

    # 4. If we used a popup_page, close it after login attempt so we don't leak pages
    if login_page is not page:
        try:
            login_page.close()
        except Exception:
            pass

    # 5. Check if the original page is now logged in, or try navigating to target_url
    if target_url:
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
