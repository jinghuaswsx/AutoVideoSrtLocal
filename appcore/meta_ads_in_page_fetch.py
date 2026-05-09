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

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Literal
from urllib.parse import urlencode

from appcore import meta_ads_xhr_token
from appcore.meta_ads_cdp import DEFAULT_META_ADS_CDP_URL, meta_ads_cdp_lock
from appcore.meta_ads_xhr_token import (
    AM_TABULAR_URL_FRAGMENT,
    DEFAULT_TOKEN_TTL_MINUTES,
    HARVEST_TIMEOUT_SECONDS,
    TokenHarvestError,
    extract_access_token_from_url,
    load_cached_token,
    save_cached_token,
)

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


def _harvest_token_from_existing_page(
    page: Any,
    *,
    page_url: str,
    page_load_timeout_ms: int,
    harvest_wait_seconds: int,
    account_code: str,
    ttl_minutes: int,
) -> str:
    """Capture access_token by listening for am_tabular requests on a
    page we already opened. Reuses the host Playwright session so we
    avoid the ``sync_playwright`` cannot be nested error that bites
    when ``meta_ads_xhr_token.harvest_meta_ads_access_token`` opens
    its own Playwright instance."""
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

    page.on("request", on_request)
    try:
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=page_load_timeout_ms)
        except Exception as exc:  # noqa: BLE001 - listener may still fire on partial load
            log.warning("in-page session goto warning: %s", exc)
        deadline_ms = int(harvest_wait_seconds * 1000)
        elapsed = 0
        step = 200
        while elapsed < deadline_ms and "token" not in captured:
            page.wait_for_timeout(step)
            elapsed += step
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:  # noqa: BLE001
            pass

    if "token" not in captured:
        raise TokenHarvestError(
            f"timed out after {harvest_wait_seconds}s waiting for am_tabular request "
            f"on {page_url}; check that DXM01-Meta is logged in"
        )
    save_cached_token(
        captured["token"],
        harvested_via_account=account_code,
        ttl_minutes=ttl_minutes,
    )
    return captured["token"]


def _has_running_asyncio_loop() -> bool:
    """Detect whether the current thread already runs an asyncio loop.

    Playwright's ``sync_playwright()`` raises if the calling thread has
    a running asyncio loop (the API explicitly forbids the mix). When
    that happens we fall back to a worker-thread isolated session.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return loop is not None


def _setup_session_in_thread(
    *,
    playwright_factory: Callable[[], Any],
    chosen_cdp: str,
    page_url: str,
    page_load_timeout_ms: int,
    harvest_wait_seconds: int,
    token_ttl_minutes: int,
    account_code: str,
    token_provider: Callable[..., str] | None,
    state: dict[str, Any],
) -> str:
    """Open Playwright + page, harvest or load token. Mutates ``state``
    so the teardown helper can release the same handles. Returns the
    access_token. Must run on the same thread that will subsequently
    invoke ``page.evaluate`` for fetch_insights."""
    pw_ctx = playwright_factory()
    pw = pw_ctx.__enter__()
    state["pw_ctx"] = pw_ctx
    try:
        browser = pw.chromium.connect_over_cdp(chosen_cdp)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        state["page"] = page

        token: str | None = None
        if token_provider is not None:
            token = token_provider()
        else:
            cached = load_cached_token()
            if cached and cached.is_fresh():
                token = cached.access_token

        if token is not None:
            try:
                page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=page_load_timeout_ms,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("in-page session goto warning: %s", exc)
            try:
                page.wait_for_timeout(1000)
            except Exception:  # noqa: BLE001
                pass
        else:
            token = _harvest_token_from_existing_page(
                page,
                page_url=page_url,
                page_load_timeout_ms=page_load_timeout_ms,
                harvest_wait_seconds=harvest_wait_seconds,
                account_code=account_code,
                ttl_minutes=token_ttl_minutes,
            )
        return token
    except Exception:
        try:
            pw_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        state.pop("pw_ctx", None)
        state.pop("page", None)
        raise


def _teardown_session_in_thread(state: dict[str, Any]) -> None:
    page = state.get("page")
    if page is not None:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
    pw_ctx = state.get("pw_ctx")
    if pw_ctx is not None:
        try:
            pw_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def _isolated_runner_factory(executor: ThreadPoolExecutor, state: dict[str, Any]):
    """Build a ``MetaAdsSession.runner`` that marshals ``page.evaluate``
    onto the executor's single worker thread, so callers can stay on the
    main thread (where an asyncio loop may still be running)."""

    def runner(_fetch_js: str, _initial_url: str, params: dict[str, Any]) -> Any:
        page = state.get("page")
        if page is None:
            raise RuntimeError("isolated session has no live page")
        return executor.submit(page.evaluate, _fetch_js, params).result()

    return runner


@contextmanager
def open_meta_ads_session(
    *,
    cdp_url: str | None = None,
    lock_timeout_seconds: int = 600,
    page_load_timeout_ms: int = 20000,
    harvest_wait_seconds: int = HARVEST_TIMEOUT_SECONDS,
    token_ttl_minutes: int = DEFAULT_TOKEN_TTL_MINUTES,
    select_account: Callable[[], Any] | None = None,
    playwright_factory: Callable[[], Any] | None = None,
    token_provider: Callable[..., str] | None = None,
    force_isolated_thread: bool | None = None,
) -> Iterator[MetaAdsSession]:
    """Open one Ads Manager Playwright page and yield a reusable session.

    The CDP lock is held for the duration of the ``with`` block, so
    callers should fan out all fetches inside one block instead of
    opening a session per account.

    Token strategy:
    - Cache hit on ``system_settings.meta_xhr_token_cache`` → reuse it,
      skip am_tabular listener entirely.
    - Cache miss / expired → attach a request listener to the **same**
      Playwright page we will use for fetch_insights, navigate it,
      capture the access_token, save back to cache. This avoids
      nesting ``sync_playwright`` which is unsupported.

    ``token_provider`` (test injection) bypasses the listener entirely.

    ``force_isolated_thread`` (test injection) overrides the asyncio-loop
    detection. ``True`` always runs Playwright on a worker thread;
    ``False`` always runs in the current thread; ``None`` (production
    default) picks based on ``_has_running_asyncio_loop()``.
    """
    chosen_cdp = cdp_url or DEFAULT_META_ADS_CDP_URL
    pick_account = select_account or _select_session_account
    account = pick_account()
    page_url = _build_target_page_url(account)

    if playwright_factory is None:
        from playwright.sync_api import sync_playwright as _sync_playwright

        playwright_factory = _sync_playwright

    if force_isolated_thread is None:
        use_isolated = _has_running_asyncio_loop()
    else:
        use_isolated = bool(force_isolated_thread)

    with meta_ads_cdp_lock(
        task_code="meta_ads_in_page_session",
        timeout_seconds=lock_timeout_seconds,
        retry_seconds=5,
        disable_child_lock=True,
    ):
        if use_isolated:
            log.info(
                "open_meta_ads_session: running Playwright on a worker thread "
                "(asyncio loop detected on caller thread)"
            )
            executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="meta-ads-session"
            )
            state: dict[str, Any] = {}
            try:
                token = executor.submit(
                    _setup_session_in_thread,
                    playwright_factory=playwright_factory,
                    chosen_cdp=chosen_cdp,
                    page_url=page_url,
                    page_load_timeout_ms=page_load_timeout_ms,
                    harvest_wait_seconds=harvest_wait_seconds,
                    token_ttl_minutes=token_ttl_minutes,
                    account_code=account.code,
                    token_provider=token_provider,
                    state=state,
                ).result()
                page = state["page"]
                yield MetaAdsSession(
                    page=page,
                    access_token=token,
                    runner=_isolated_runner_factory(executor, state),
                )
            finally:
                try:
                    executor.submit(_teardown_session_in_thread, state).result()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "isolated session teardown error: %s", exc
                    )
                executor.shutdown(wait=True)
        else:
            with playwright_factory() as pw:
                browser = pw.chromium.connect_over_cdp(chosen_cdp)
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                page = ctx.new_page()
                try:
                    # Decide token source first, then navigate. We always
                    # need the page on the Ads Manager origin so that
                    # fetch_insights (credentials:'include') ships first-
                    # party cookies — but we only attach the am_tabular
                    # listener if we actually need to harvest.
                    token: str | None = None
                    if token_provider is not None:
                        token = token_provider()
                    else:
                        cached = load_cached_token()
                        if cached and cached.is_fresh():
                            token = cached.access_token

                    if token is not None:
                        try:
                            page.goto(
                                page_url,
                                wait_until="domcontentloaded",
                                timeout=page_load_timeout_ms,
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.warning("in-page session goto warning: %s", exc)
                        try:
                            page.wait_for_timeout(1000)
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        token = _harvest_token_from_existing_page(
                            page,
                            page_url=page_url,
                            page_load_timeout_ms=page_load_timeout_ms,
                            harvest_wait_seconds=harvest_wait_seconds,
                            account_code=account.code,
                            ttl_minutes=token_ttl_minutes,
                        )
                    yield MetaAdsSession(page=page, access_token=token)
                finally:
                    try:
                        page.close()
                    except Exception:  # noqa: BLE001
                        pass
