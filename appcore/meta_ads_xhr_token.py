"""Harvest a Marketing API access_token from the Ads Manager page session.

Docs-anchor:
docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md

The browser (DXM01-Meta on CDP 9222) sends requests to
``https://adsmanager-graph.facebook.com/v22.0/act_<id>/am_tabular?access_token=...``
whenever an Ads Manager table renders. The ``access_token`` query parameter
is a user-bound page token good for ~1-2 hours of Marketing API calls.

This module captures that token by attaching a request listener to a
fresh Playwright page, then caches it in ``system_settings`` for reuse.
The token is shared across all enabled Meta ad accounts (Meta scopes
tokens to user, not to ad account).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import parse_qs, urlparse

from appcore import settings as system_settings
from appcore.meta_ads_cdp import DEFAULT_META_ADS_CDP_URL, meta_ads_cdp_lock

log = logging.getLogger(__name__)

CACHE_SETTING_KEY = "meta_xhr_token_cache"
DEFAULT_TOKEN_TTL_MINUTES = 90
HARVEST_TIMEOUT_SECONDS = 30
AM_TABULAR_URL_FRAGMENT = "am_tabular"


class TokenHarvestError(RuntimeError):
    """Raised when the token harvester cannot obtain a fresh access_token."""


@dataclass(frozen=True)
class CachedToken:
    access_token: str
    harvested_at: datetime
    expires_hint_at: datetime
    harvested_via_account: str

    def is_fresh(self, *, now: datetime | None = None) -> bool:
        ref = now or datetime.now()
        return ref < self.expires_hint_at


def extract_access_token_from_url(url: str) -> str | None:
    """Return the ``access_token`` query parameter from an Ads Manager URL.

    Returns None when the URL has no ``access_token`` or it is empty.
    Pure function — used by both the live harvester and unit tests.
    """
    if not url:
        return None
    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=False)
    except Exception:  # noqa: BLE001 - defensive against weird URLs
        return None
    values = qs.get("access_token") or []
    if not values:
        return None
    token = (values[0] or "").strip()
    return token or None


def load_cached_token() -> CachedToken | None:
    raw = system_settings.get_setting(CACHE_SETTING_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("meta_xhr_token_cache: stored value is not valid JSON")
        return None
    if not isinstance(data, dict):
        return None
    token = str(data.get("access_token") or "").strip()
    if not token:
        return None
    try:
        harvested_at = datetime.fromisoformat(str(data.get("harvested_at")))
        expires_hint_at = datetime.fromisoformat(str(data.get("expires_hint_at")))
    except (TypeError, ValueError):
        log.warning("meta_xhr_token_cache: invalid timestamps in cache")
        return None
    return CachedToken(
        access_token=token,
        harvested_at=harvested_at,
        expires_hint_at=expires_hint_at,
        harvested_via_account=str(data.get("harvested_via_account") or ""),
    )


def save_cached_token(
    access_token: str,
    *,
    harvested_via_account: str,
    ttl_minutes: int = DEFAULT_TOKEN_TTL_MINUTES,
    now: datetime | None = None,
) -> CachedToken:
    moment = now or datetime.now()
    expiry = moment + timedelta(minutes=max(1, ttl_minutes))
    payload = {
        "access_token": access_token,
        "harvested_at": moment.replace(microsecond=0).isoformat(),
        "expires_hint_at": expiry.replace(microsecond=0).isoformat(),
        "harvested_via_account": harvested_via_account,
    }
    system_settings.set_setting(
        CACHE_SETTING_KEY, json.dumps(payload, ensure_ascii=False)
    )
    return CachedToken(
        access_token=access_token,
        harvested_at=moment,
        expires_hint_at=expiry,
        harvested_via_account=harvested_via_account,
    )


def clear_cached_token() -> None:
    system_settings.delete_setting(CACHE_SETTING_KEY)


def _select_harvest_account():
    from appcore import meta_ad_accounts

    accounts = meta_ad_accounts.get_enabled_accounts()
    if not accounts:
        raise TokenHarvestError("no enabled Meta ad accounts; cannot harvest token")
    return accounts[0]


def _build_campaign_url(account_id: str, business_id: str) -> str:
    today = datetime.now().date().isoformat()
    return (
        f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?"
        f"act={account_id}&business_id={business_id}&global_scope_id={business_id}"
        f"&attribution_windows=default&column_preset=1658418688523178"
        f"&date={today}_{today}&insights_date={today}_{today}"
    )


def _harvest_with_playwright(
    target_url: str,
    *,
    cdp_url: str,
    timeout_seconds: int,
) -> str:
    """Open a fresh page, return the first access_token observed on am_tabular."""
    from playwright.sync_api import sync_playwright

    captured: dict[str, str] = {}

    def on_request(request) -> None:
        try:
            url = request.url
        except Exception:  # noqa: BLE001
            return
        if AM_TABULAR_URL_FRAGMENT not in url:
            return
        token = extract_access_token_from_url(url)
        if token and "token" not in captured:
            captured["token"] = token

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.on("request", on_request)
        try:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            except Exception as exc:  # noqa: BLE001 - listener may still fire
                log.warning("token harvester goto warning: %s", exc)
            deadline_ms = int(timeout_seconds * 1000)
            elapsed = 0
            while elapsed < deadline_ms and "token" not in captured:
                page.wait_for_timeout(200)
                elapsed += 200
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    if "token" not in captured:
        raise TokenHarvestError(
            f"timed out after {timeout_seconds}s waiting for am_tabular request "
            f"on {target_url}; check that DXM01-Meta is logged in"
        )
    return captured["token"]


def harvest_meta_ads_access_token(
    *,
    force_refresh: bool = False,
    cdp_url: str | None = None,
    timeout_seconds: int = HARVEST_TIMEOUT_SECONDS,
    ttl_minutes: int = DEFAULT_TOKEN_TTL_MINUTES,
    select_account: Callable[[], object] | None = None,
    harvester: Callable[..., str] | None = None,
    now: datetime | None = None,
) -> str:
    """Return a usable Marketing API access_token, harvesting if needed.

    - Reads ``system_settings.meta_xhr_token_cache`` first.
    - On cache miss / expired / ``force_refresh``: takes the Meta Ads CDP
      lock, opens a fresh page in DXM01-Meta, captures an ``am_tabular``
      request, extracts ``access_token``, writes cache, returns it.
    - ``select_account`` / ``harvester`` are injection points for tests;
      production callers pass nothing.
    """
    if not force_refresh:
        cached = load_cached_token()
        if cached and cached.is_fresh(now=now):
            return cached.access_token

    select = select_account or _select_harvest_account
    harvest = harvester or _harvest_with_playwright

    account = select()
    target_url = _build_campaign_url(account.account_id, account.business_id)
    chosen_cdp = cdp_url or DEFAULT_META_ADS_CDP_URL

    with meta_ads_cdp_lock(
        task_code="meta_ads_xhr_token_harvest",
        timeout_seconds=120,
        retry_seconds=5,
    ):
        token = harvest(
            target_url,
            cdp_url=chosen_cdp,
            timeout_seconds=timeout_seconds,
        )

    save_cached_token(
        token,
        harvested_via_account=account.code,
        ttl_minutes=ttl_minutes,
        now=now,
    )
    return token
