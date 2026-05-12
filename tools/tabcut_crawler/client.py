from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlencode


FetchFn = Callable[[str, str], dict[str, Any]]


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


def goods_ranking_url(*, biz_date: str, page_no: int, page_size: int = 100) -> str:
    return trpc_url(
        "ranking.goods.rankingData",
        {
            "region": "US",
            "bizDate": biz_date,
            "rankType": 1,
            "orderType": "1",
            "categoryId": "0",
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
    def __init__(self, cdp_url: str) -> None:
        self._cdp_url = cdp_url
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

