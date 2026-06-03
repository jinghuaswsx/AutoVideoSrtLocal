"""Tabcut API/CDP client.

Docs-anchor: docs/superpowers/specs/2026-05-12-tabcut-crawler-design.md
"""
from __future__ import annotations

import os
from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode


FetchFn = Callable[[str, str], dict[str, Any]]

GOODS_RANK_PERIOD_TO_TYPE = {
    "1d": 1,
    "7d": 2,
    "30d": 3,
}
GOODS_RANK_KIND_TO_ORDER_TYPE = {
    "hot": "1",
    "new": "2",
}

TABCUT_USERNAME_ENV_KEYS = (
    "TABCUT_LOGIN_ACCOUNT",
    "TABCUT_LOGIN_USERNAME",
    "TABCUT_LOGIN_EMAIL",
    "TABCUT_ACCOUNT",
    "TABCUT_USERNAME",
    "TABCUT_USER",
    "TABCUT_EMAIL",
    "TABCUT_PHONE",
)
TABCUT_PASSWORD_ENV_KEYS = (
    "TABCUT_LOGIN_PASSWORD",
    "TABCUT_LOGIN_PASS",
    "TABCUT_PASSWORD",
    "TABCUT_PASS",
    "TABCUT_PWD",
)
TABCUT_LOGIN_REQUIRED_MARKERS = (
    "游客模式",
    "登录 / 注册",
    "登录/注册",
    "login / register",
    "log in / register",
)
TABCUT_HUMAN_REQUIRED_MARKERS = (
    "安全验证",
    "验证码",
    "人机验证",
    "滑块",
    "风险验证",
    "captcha",
    "verification code",
    "security verification",
    "two factor",
    "two-factor",
    "2fa",
)
TABCUT_LOGIN_ENTRY_SELECTORS = (
    'button:has-text("登录")',
    'a:has-text("登录")',
    'div[role=button]:has-text("登录")',
    "text=登录 / 注册",
    "text=登录/注册",
)
TABCUT_PASSWORD_LOGIN_SELECTORS = (
    "text=密码登录",
    "text=账号密码登录",
    "text=使用密码登录",
    "text=Password login",
    "text=Log in with password",
)
TABCUT_ACCOUNT_INPUT_SELECTORS = (
    'input[placeholder*="手机号"]',
    'input[placeholder*="手机"]',
    'input[placeholder*="邮箱"]',
    'input[placeholder*="账号"]',
    'input[placeholder*="email"]',
    'input[placeholder*="Email"]',
    'input[type="email"]',
    'input[type="tel"]',
    'input[type="text"]',
    "input:not([type])",
)
TABCUT_PASSWORD_INPUT_SELECTORS = (
    'input[type="password"]',
    'input[placeholder*="密码"]',
    'input[placeholder*="password"]',
    'input[placeholder*="Password"]',
)
TABCUT_LOGIN_SUBMIT_SELECTORS = (
    'button:has-text("登录")',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button[type="submit"]',
    'div[role=button]:has-text("登录")',
)


@dataclass(frozen=True)
class TabcutLoginCredentials:
    username: str
    password: str


CredentialStoreLoader = Callable[[], TabcutLoginCredentials | None]


def _first_env_value(env: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return None


def _resolve_stored_tabcut_login_credentials() -> TabcutLoginCredentials | None:
    try:
        from appcore import browser_login_credentials

        stored = browser_login_credentials.get_tabcut_credential()
    except Exception:
        return None
    if stored is None:
        return None
    username = (stored.username or "").strip()
    password = (stored.password or "").strip()
    if not username or not password:
        return None
    return TabcutLoginCredentials(username=username, password=password)


def resolve_tabcut_login_credentials(
    env: Mapping[str, str] | None = None,
    *,
    store_loader: CredentialStoreLoader | None = None,
) -> TabcutLoginCredentials | None:
    source = os.environ if env is None else env
    username = _first_env_value(source, TABCUT_USERNAME_ENV_KEYS)
    password = _first_env_value(source, TABCUT_PASSWORD_ENV_KEYS)
    if username and password:
        return TabcutLoginCredentials(username=username, password=password)
    if env is not None and store_loader is None:
        return None
    loader = store_loader or _resolve_stored_tabcut_login_credentials
    return loader()


def _mark_tabcut_login_result(status: str, error: str | None = None) -> None:
    try:
        from appcore import browser_login_credentials

        browser_login_credentials.mark_login_result(
            browser_login_credentials.TABCUT_ENV_CODE,
            browser_login_credentials.TABCUT_PROVIDER,
            status,
            error,
        )
    except Exception:
        return


def classify_tabcut_login_state(url: str, body_text: str = "") -> str:
    haystack = f"{url or ''}\n{body_text or ''}".lower()
    if any(marker.lower() in haystack for marker in TABCUT_HUMAN_REQUIRED_MARKERS):
        return "needs_human"
    if any(marker.lower() in haystack for marker in TABCUT_LOGIN_REQUIRED_MARKERS):
        return "login_required"
    if "/login" in haystack:
        return "login_required"
    return "logged_in"


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


def _locator_target(locator: Any) -> Any:
    return getattr(locator, "first", locator)


def _locator_count(locator: Any) -> int | None:
    try:
        count = locator.count()
        return int(count) if isinstance(count, int) else None
    except Exception:
        return None


def _locator_is_visible(locator: Any, timeout_ms: int = 1000) -> bool:
    target = _locator_target(locator)
    if not hasattr(target, "is_visible"):
        return True
    try:
        return bool(target.is_visible(timeout=timeout_ms))
    except TypeError:
        try:
            return bool(target.is_visible())
        except Exception:
            return False
    except Exception:
        return False


def _click_first_visible(page: Any, selectors: tuple[str, ...], *, timeout_ms: int = 10000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector)
        except Exception:
            continue
        count = _locator_count(locator)
        if count == 0 or not _locator_is_visible(locator):
            continue
        target = _locator_target(locator)
        try:
            target.click(timeout=timeout_ms)
        except TypeError:
            try:
                target.click()
            except Exception:
                continue
        except Exception:
            continue
        return True
    return False


def _fill_first_visible(page: Any, selectors: tuple[str, ...], value: str, *, timeout_ms: int = 10000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector)
        except Exception:
            continue
        count = _locator_count(locator)
        if count == 0 or not _locator_is_visible(locator):
            continue
        target = _locator_target(locator)
        try:
            target.fill(value, timeout=timeout_ms)
        except TypeError:
            try:
                target.fill(value)
            except Exception:
                continue
        except Exception:
            continue
        return True
    return False


def _press_first_visible(page: Any, selectors: tuple[str, ...], key: str, *, timeout_ms: int = 10000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector)
        except Exception:
            continue
        count = _locator_count(locator)
        if count == 0 or not _locator_is_visible(locator):
            continue
        target = _locator_target(locator)
        try:
            target.press(key, timeout=timeout_ms)
        except TypeError:
            try:
                target.press(key)
            except Exception:
                continue
        except Exception:
            continue
        return True
    return False


def _page_has_visible_login_entry(page: Any) -> bool:
    for selector in TABCUT_LOGIN_ENTRY_SELECTORS:
        try:
            locator = page.locator(selector)
        except Exception:
            continue
        count = _locator_count(locator)
        if count == 0:
            continue
        if _locator_is_visible(locator):
            return True
    return False


def _wait_for_tabcut_page_ready(page: Any) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    try:
        page.wait_for_timeout(1000)
    except Exception:
        pass


def ensure_tabcut_login_on_page(
    page: Any,
    *,
    credentials: TabcutLoginCredentials | None = None,
    timeout_ms: int = 10000,
) -> dict[str, Any]:
    _wait_for_tabcut_page_ready(page)
    state = classify_tabcut_login_state(getattr(page, "url", ""), _page_body_text(page))
    if state == "logged_in" and _page_has_visible_login_entry(page):
        state = "login_required"
    if state == "logged_in":
        _mark_tabcut_login_result("already_logged_in", None)
        return {"status": "already_logged_in", "title": _page_title(page), "current_url": getattr(page, "url", "")}
    if state == "needs_human":
        _mark_tabcut_login_result("needs_human", "human_verification_required")
        raise RuntimeError("Tabcut login requires human verification; open the TABCUT browser and complete the challenge.")

    credential = credentials or resolve_tabcut_login_credentials()
    if credential is None:
        _mark_tabcut_login_result("failed", "missing_credential")
        raise RuntimeError(
            "Tabcut login required but credentials are not configured. "
            "Configure TABCUT / tabcut in /settings?tab=browser_credentials, "
            "or set TABCUT_LOGIN_ACCOUNT and TABCUT_LOGIN_PASSWORD."
        )

    clicked_login = _click_first_visible(page, TABCUT_LOGIN_ENTRY_SELECTORS, timeout_ms=timeout_ms)
    clicked_password_mode = _click_first_visible(page, TABCUT_PASSWORD_LOGIN_SELECTORS, timeout_ms=timeout_ms)
    if not clicked_login and not clicked_password_mode:
        _mark_tabcut_login_result("failed", "login_button_not_found")
        raise RuntimeError("Tabcut login required but the login button was not found.")

    if not _fill_first_visible(page, TABCUT_ACCOUNT_INPUT_SELECTORS, credential.username, timeout_ms=timeout_ms):
        _mark_tabcut_login_result("failed", "account_input_not_found")
        raise RuntimeError("Tabcut login form account input was not found.")
    if not _fill_first_visible(page, TABCUT_PASSWORD_INPUT_SELECTORS, credential.password, timeout_ms=timeout_ms):
        _mark_tabcut_login_result("failed", "password_input_not_found")
        raise RuntimeError("Tabcut login form password input was not found.")

    if not _press_first_visible(page, TABCUT_PASSWORD_INPUT_SELECTORS, "Enter", timeout_ms=timeout_ms):
        _click_first_visible(page, TABCUT_LOGIN_SUBMIT_SELECTORS, timeout_ms=timeout_ms)

    try:
        page.wait_for_timeout(5000)
    except Exception:
        pass

    final_state = classify_tabcut_login_state(getattr(page, "url", ""), _page_body_text(page))
    if final_state == "needs_human":
        _mark_tabcut_login_result("needs_human", "human_verification_required")
        raise RuntimeError("Tabcut login requires human verification after submitting credentials.")
    if final_state == "login_required" or _page_has_visible_login_entry(page):
        _mark_tabcut_login_result("failed", "login_still_required")
        raise RuntimeError("Tabcut login did not complete; check credentials or complete any browser challenge.")
    _mark_tabcut_login_result("success", None)
    return {"status": "success", "title": _page_title(page), "current_url": getattr(page, "url", "")}


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_payload(item)
            for key, item in value.items()
            if key not in {"videoUrl", "videoPlayUrl"}
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str) and "auth_key=" in value:
        return None
    return value


def _get_path(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    paths = [
        ("result", "data"),
        ("result", "data", "result", "data"),
        ("result", "data", "result", "list"),
        ("result", "data", "result", "items"),
        ("data", "list"),
        ("data", "items"),
    ]
    for path in paths:
        value = _get_path(payload, path)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def extract_total(payload: dict[str, Any], fallback: int) -> int:
    paths = [
        ("result", "total"),
        ("result", "data", "result", "total"),
        ("result", "data", "result", "totalSize"),
        ("data", "total"),
    ]
    for path in paths:
        value = _get_path(payload, path)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return fallback


def assert_success(payload: dict[str, Any]) -> None:
    code = payload.get("code") or _get_path(payload, ("result", "data", "code"))
    if code and str(code) != "200":
        message = payload.get("message") or _get_path(payload, ("result", "data", "message"))
        raise RuntimeError(f"Tabcut API returned code={code}: {message or 'unknown error'}")


def video_ranking_url(*, sort: int, page_no: int, rank_day: int = 7, page_size: int = 100) -> str:
    params = {
        "region": "US",
        "regionId": "1",
        "rankDay": str(rank_day),
        "itemCategoryId": "0",
        "sort": str(sort),
        "pageNo": str(page_no),
        "pageSize": str(page_size),
    }
    return "https://www.tabcut.com/api/ranking/videos?" + urlencode(params)


def trpc_url(name: str, payload: dict[str, Any]) -> str:
    return f"https://www.tabcut.com/api/trpc/{name}?input={urlencode({'': json.dumps(payload, ensure_ascii=False)})[1:]}"


def goods_ranking_url(
    *,
    biz_date: str,
    page_no: int,
    page_size: int = 100,
    category_id: str | int = "0",
    rank_kind: str = "hot",
    rank_period: str = "1d",
) -> str:
    rank_type = GOODS_RANK_PERIOD_TO_TYPE.get(str(rank_period), 1)
    order_type = GOODS_RANK_KIND_TO_ORDER_TYPE.get(str(rank_kind), "1")
    return trpc_url(
        "ranking.goods.rankingData",
        {
            "region": "US",
            "bizDate": biz_date,
            "rankType": rank_type,
            "orderType": order_type,
            "categoryId": str(category_id),
            "pageNo": page_no,
            "pageSize": page_size,
        },
    )


def analysis_video_search_payload(
    *,
    page_no: int,
    page_size: int = 100,
    region: str = "US",
    sort_field: str = "video_sold_count",
    video_create_time_begin: str,
    video_create_time_end: str,
    item_video_flag: str = "1",
) -> dict[str, Any]:
    return {
        "pageNo": int(page_no),
        "pageSize": str(page_size),
        "region": region,
        "sortField": sort_field,
        "videoCreateTimeBegin": video_create_time_begin,
        "videoCreateTimeEnd": video_create_time_end,
        "itemVideoFlag": str(item_video_flag),
        "categoryQuery": {"lv1List": [], "lv2List": [], "lv3List": []},
    }


class TabcutApiClient:
    def __init__(
        self,
        *,
        fetch_fn: Callable[..., dict[str, Any]] | None = None,
        cdp_url: str | None = None,
        min_interval_seconds: float = 3.3,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch_fn = fetch_fn or _CdpFetcher(cdp_url or "http://127.0.0.1:9227")
        self._min_interval_seconds = max(3.0, float(min_interval_seconds))
        self._sleep_fn = sleep_fn
        self._monotonic_fn = monotonic_fn
        self._last_request_at: float | None = None

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._monotonic_fn()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < self._min_interval_seconds:
                self._sleep_fn(round(self._min_interval_seconds - elapsed, 6))
        payload = self._fetch_fn(method, url, params=params, json_body=json_body)
        self._last_request_at = self._monotonic_fn()
        assert_success(payload)
        return sanitize_payload(payload)

    def fetch_items(self, url: str) -> tuple[list[dict[str, Any]], int]:
        payload = self.request_json("GET", url)
        items = extract_items(payload)
        return items, extract_total(payload, len(items))


class _CdpFetcher:
    def __init__(
        self,
        cdp_url: str,
        *,
        login_fn: Callable[[Any], Any] | None = ensure_tabcut_login_on_page,
    ) -> None:
        self._cdp_url = cdp_url
        self._login_fn = login_fn
        self._login_checked = False
        self._playwright = None
        self._browser = None
        self._page = None

    def __call__(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params:
            separator = "&" if "?" in url else "?"
            url = url + separator + urlencode(params)
        page = self._ensure_page()
        self._ensure_login(page)
        result = page.evaluate(
            """
            async ({ method, url, jsonBody }) => {
              const init = {
                method,
                credentials: "include",
                headers: { "accept": "application/json,text/plain,*/*" },
              };
              if (jsonBody) {
                init.headers["content-type"] = "application/json";
                init.body = JSON.stringify(jsonBody);
              }
              const response = await fetch(url, init);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (_) {}
              if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${text.slice(0, 800)}`);
              }
              return json || { text };
            }
            """,
            {"method": method.upper(), "url": url, "jsonBody": json_body},
        )
        if not isinstance(result, dict):
            raise RuntimeError("Tabcut API did not return a JSON object")
        return result

    def _ensure_login(self, page: Any) -> None:
        if self._login_checked or self._login_fn is None:
            return
        self._login_fn(page)
        self._login_checked = True

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
        context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        pages = context.pages
        self._page = next((page for page in pages if "tabcut.com" in page.url), pages[0] if pages else context.new_page())
        if "tabcut.com" not in self._page.url:
            self._page.goto("https://www.tabcut.com/zh-CN/workbench", wait_until="domcontentloaded", timeout=60000)
        return self._page

