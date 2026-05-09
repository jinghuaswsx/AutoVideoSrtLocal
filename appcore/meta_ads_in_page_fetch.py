"""Fetch Meta Marketing API /insights from inside a Playwright page.

Docs-anchor:
docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md

Why in-page: the Ads Manager session token (harvested by
``meta_ads_xhr_token``) is a first-party Page Token. Meta's auth gate
rejects it when sent from a process that does not also carry the
matching Origin / Referer / cookie context, so external ``urllib`` calls
get HTTP 400 OAuthException code 1.

The workaround that **does** work, verified end-to-end on 2026-05-09:

- Open an Ads Manager tab via Playwright + CDP.
- Inside that page, run ``fetch('https://adsmanager-graph.facebook.com/v22.0/act_<id>/insights?access_token=...&...', {credentials: 'include'})``.
- ``adsmanager-graph.facebook.com`` is on the CORS allowlist for the
  Ads Manager origin and accepts the user token; ``graph.facebook.com``
  is not. ``credentials: 'include'`` ships the user's session cookies.

This module wraps that pattern so callers (roi_hourly_sync,
meta_daily_final_sync) can do many fetches across many accounts /
levels / dates in one browser visit, with one CDP lock acquisition.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Literal
from urllib.parse import urlencode

from appcore import meta_ads_xhr_token
from appcore.meta_ads_cdp import DEFAULT_META_ADS_CDP_URL, meta_ads_cdp_lock

log = logging.getLogger(__name__)

ADS_MANAGER_GRAPH_HOST = "https://adsmanager-graph.facebook.com"
ADS_MANAGER_GRAPH_VERSION = "v22.0"
DEFAULT_LIMIT = 500
DEFAULT_MAX_PAGES = 200
LevelLiteral = Literal["campaign", "adset", "ad"]


class MetaAdsInPageFetchError(RuntimeError):
    """Generic failure when in-page /insights fetch does not return 200."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class MetaAdsTokenExpiredError(MetaAdsInPageFetchError):
    """Raised when /insights returns OAuth code 190 (token expired/invalid).

    Callers should call ``harvest_meta_ads_access_token(force_refresh=True)``
    and retry exactly once; a second 190 means the page has lost its login
    state and a human must re-authenticate DXM01-Meta.
    """


# JS executed inside the page. Pages all the result rows for one URL,
# returns {"rows": [...], "pages": N} or raises with status + body.
_FETCH_JS = """
async ({initialUrl, maxPages}) => {
  const all = [];
  let url = initialUrl;
  let pages = 0;
  while (url && pages < maxPages) {
    pages += 1;
    const resp = await fetch(url, {credentials: 'include'});
    const bodyText = await resp.text();
    if (!resp.ok) {
      const err = new Error('HTTP ' + resp.status);
      err.status = resp.status;
      err.body = bodyText.slice(0, 1500);
      throw err;
    }
    let parsed;
    try {
      parsed = JSON.parse(bodyText);
    } catch (e) {
      const err = new Error('non-JSON response');
      err.status = resp.status;
      err.body = bodyText.slice(0, 1500);
      throw err;
    }
    const data = parsed.data || [];
    for (const row of data) all.push(row);
    url = (parsed.paging && parsed.paging.next) || null;
  }
  return {rows: all, pages: pages};
}
"""


def _build_insights_url(
    account_id: str,
    *,
    access_token: str,
    level: LevelLiteral,
    time_range: dict[str, str],
    fields: Iterable[str],
    time_increment: str = "1",
    limit: int = DEFAULT_LIMIT,
    extra: dict[str, str] | None = None,
) -> str:
    params: dict[str, str] = {
        "access_token": access_token,
        "fields": ",".join(fields),
        "level": level,
        "time_range": json.dumps(time_range, separators=(",", ":")),
        "time_increment": time_increment,
        "limit": str(max(1, int(limit))),
    }
    if extra:
        params.update({k: str(v) for k, v in extra.items()})
    aid = str(account_id).strip().removeprefix("act_")
    return (
        f"{ADS_MANAGER_GRAPH_HOST}/{ADS_MANAGER_GRAPH_VERSION}/act_{aid}/insights?"
        + urlencode(params)
    )


def _interpret_runner_error(err: Exception) -> MetaAdsInPageFetchError:
    """Map a JS-side raised Error or Python exception to a typed error.

    Playwright surfaces raised JS Errors via ``page.evaluate`` as a
    ``playwright._impl._errors.Error`` (or subclass) with the message
    embedded; the JS code in ``_FETCH_JS`` puts ``HTTP <status>`` in the
    message and ``status`` / ``body`` on the Error object. Playwright
    flattens those properties into the exception message string.
    """
    raw = str(err)
    status: int | None = None
    body = ""
    # Best-effort: Playwright's wrapped JS Error includes the message.
    if "HTTP " in raw:
        try:
            after = raw.split("HTTP ", 1)[1]
            status = int(after.split()[0].rstrip(":"))
        except (ValueError, IndexError):
            status = None
    # OAuth token expiry surfaces inside the body as "code":190 in newer
    # Meta error envelopes. Detect the signature heuristically.
    if "\"code\":190" in raw or '"code": 190' in raw:
        return MetaAdsTokenExpiredError(
            f"in-page /insights returned OAuth code 190 (token expired): {raw[:400]}",
            status=status or 400,
            body=raw[:1500],
        )
    return MetaAdsInPageFetchError(
        f"in-page /insights failed: {raw[:600]}",
        status=status,
        body=raw[:1500],
    )


@dataclass
class MetaAdsSession:
    """One Playwright page + one harvested access_token, reusable for
    many ``fetch_insights`` calls in a single CDP lock window."""

    page: Any  # playwright.sync_api.Page when real; mock in tests
    access_token: str
    runner: Callable[[str, str, dict], Any] | None = None
    """Override the JS runner. Test injection point. When None, falls back
    to ``self.page.evaluate``. Production callers should leave this None."""

    def _run(self, initial_url: str, *, max_pages: int) -> dict[str, Any]:
        if self.runner is not None:
            return self.runner(_FETCH_JS, initial_url, {"initialUrl": initial_url, "maxPages": max_pages})
        return self.page.evaluate(
            _FETCH_JS,
            {"initialUrl": initial_url, "maxPages": max_pages},
        )

    def fetch_insights(
        self,
        account_id: str,
        *,
        level: LevelLiteral,
        time_range: dict[str, str],
        fields: Iterable[str],
        time_increment: str = "1",
        limit: int = DEFAULT_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        extra: dict[str, str] | None = None,
    ) -> list[dict]:
        url = _build_insights_url(
            account_id,
            access_token=self.access_token,
            level=level,
            time_range=time_range,
            fields=fields,
            time_increment=time_increment,
            limit=limit,
            extra=extra,
        )
        try:
            result = self._run(url, max_pages=max_pages)
        except Exception as exc:  # noqa: BLE001 - re-typed below
            raise _interpret_runner_error(exc) from exc
        if not isinstance(result, dict) or "rows" not in result:
            raise MetaAdsInPageFetchError(
                f"in-page /insights returned unexpected shape: {type(result).__name__}",
            )
        rows = result.get("rows") or []
        if not isinstance(rows, list):
            raise MetaAdsInPageFetchError("in-page /insights rows is not a list")
        return [r for r in rows if isinstance(r, dict)]


def _build_target_page_url(account) -> str:
    return (
        f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?"
        f"act={account.account_id}&business_id={account.business_id}"
        f"&global_scope_id={account.business_id}"
    )


def _select_session_account():
    from appcore import meta_ad_accounts

    accounts = meta_ad_accounts.get_enabled_accounts()
    if not accounts:
        raise RuntimeError("no enabled Meta ad accounts; cannot open in-page session")
    return accounts[0]


@contextmanager
def open_meta_ads_session(
    *,
    cdp_url: str | None = None,
    lock_timeout_seconds: int = 600,
    page_load_timeout_ms: int = 20000,
    select_account: Callable[[], Any] | None = None,
    playwright_factory: Callable[[], Any] | None = None,
    token_provider: Callable[..., str] | None = None,
) -> Iterator[MetaAdsSession]:
    """Open one Ads Manager Playwright page and yield a reusable session.

    The CDP lock is held for the duration of the ``with`` block, so
    callers should fan out all fetches inside one block instead of
    opening a session per account.
    """
    chosen_cdp = cdp_url or DEFAULT_META_ADS_CDP_URL
    pick_account = select_account or _select_session_account
    account = pick_account()
    page_url = _build_target_page_url(account)

    if playwright_factory is None:
        from playwright.sync_api import sync_playwright as _sync_playwright

        playwright_factory = _sync_playwright

    fetch_token = token_provider or meta_ads_xhr_token.harvest_meta_ads_access_token

    with meta_ads_cdp_lock(
        task_code="meta_ads_in_page_session",
        timeout_seconds=lock_timeout_seconds,
        retry_seconds=5,
        disable_child_lock=True,
    ):
        with playwright_factory() as pw:
            browser = pw.chromium.connect_over_cdp(chosen_cdp)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=page_load_timeout_ms)
                except Exception as exc:  # noqa: BLE001 - we still need a token
                    log.warning("in-page session goto warning: %s", exc)
                # tiny settle so React has a chance to fire am_tabular before harvest
                try:
                    page.wait_for_timeout(2000)
                except Exception:  # noqa: BLE001
                    pass
                # token harvester would re-acquire the same lock if its
                # cache is stale; disable_child_lock above neuters that
                # nested acquisition (see meta_ads_cdp.meta_ads_cdp_lock).
                token = fetch_token()
                yield MetaAdsSession(page=page, access_token=token)
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
