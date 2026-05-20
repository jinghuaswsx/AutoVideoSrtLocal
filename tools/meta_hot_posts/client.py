from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse, urlunparse

import requests


FetchFn = Callable[..., dict[str, Any]]
HeadersFn = Callable[[], dict[str, str]]


class WedevCredentialsMissingError(RuntimeError):
    pass


class WedevCredentialsExpiredError(RuntimeError):
    pass


def normalize_product_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    if not text.startswith(("http://", "https://")):
        text = "https://" + text.lstrip("/")
    parsed = urlparse(text)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def product_url_hash(value: Any) -> str:
    return hashlib.sha256(normalize_product_url(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_marker(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _text(value).lower()
    if not text:
        return False
    return text in {
        "1",
        "true",
        "yes",
        "on",
        "pushed",
        "already_pushed",
        "已推送",
        "素材已推送",
    }


def _datetime_text(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return None
    text = _text(value)
    if not text:
        return None
    if text.isdigit():
        return _datetime_text(int(text))
    normalized = text.replace("T", " ").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return text[:19]
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def post_url(row: dict[str, Any]) -> str:
    page_id = _text(row.get("page_id"))
    post_id = _text(row.get("post_id"))
    return f"https://facebook.com/{page_id}/posts/{post_id}" if page_id and post_id else ""


def ad_library_url(row: dict[str, Any]) -> str:
    bm_page_id = _text(row.get("bm_page_id"))
    if not bm_page_id:
        return ""
    params = {
        "active_status": "active",
        "ad_type": "all",
        "country": "ALL",
        "media_type": "all",
        "search_type": "page",
        "source": "page-transparency-widget",
        "view_all_page_id": bm_page_id,
    }
    return "https://www.facebook.com/ads/library/?" + urlencode(params)


def _is_pushed(row: dict[str, Any]) -> bool:
    selected_at = _text(row.get("selected_at")).lower()
    if selected_at and selected_at not in {"0", "false", "none", "null"}:
        return True
    select_payload = row.get("select")
    if isinstance(select_payload, dict):
        if _int(select_payload.get("id")):
            return True
        if _int(select_payload.get("is_done")):
            return True
    direct_keys = (
        "is_pushed",
        "pushed",
        "has_pushed",
        "material_pushed",
        "push_status",
        "pushed_status",
        "pushStatus",
        "status",
        "pushed_at",
    )
    for key in direct_keys:
        if key not in row:
            continue
        if key == "status":
            text = _text(row.get(key)).lower()
            if text in {"pushed", "already_pushed", "已推送", "素材已推送"}:
                return True
            continue
        if _bool_marker(row.get(key)):
            return True
    for key, value in row.items():
        if "push" in str(key).lower() and _bool_marker(value):
            return True
    return False


def normalize_hot_post(row: dict[str, Any]) -> dict[str, Any]:
    product_url = normalize_product_url(row.get("product_url"))
    metrics = {
        "likes": _int(row.get("likes")),
        "comments": _int(row.get("comments")),
        "shares": _int(row.get("shares")),
        "latest_likes": _int(row.get("latest_likes")),
        "latest_comments": _int(row.get("latest_comments")),
        "latest_shares": _int(row.get("latest_shares")),
        "sync_period_likes": _int(row.get("sync_period_likes")),
        "sync_period_hours": _float(row.get("sync_period_hours")),
    }
    return {
        "wedev_post_id": _int(row.get("id")) or 0,
        "page_id": _text(row.get("page_id")),
        "post_id": _text(row.get("post_id")),
        "bm_page_id": _text(row.get("bm_page_id")),
        "post_url": post_url(row),
        "ad_library_url": ad_library_url(row),
        "product_url": product_url,
        "product_url_hash": product_url_hash(product_url) if product_url else "",
        "creation_time": _datetime_text(row.get("creation_time")),
        "last_synced_at": _datetime_text(row.get("last_synced_at")),
        "likes": metrics["likes"],
        "comments": metrics["comments"],
        "shares": metrics["shares"],
        "latest_likes": metrics["latest_likes"],
        "latest_comments": metrics["latest_comments"],
        "latest_shares": metrics["latest_shares"],
        "sync_period_likes": metrics["sync_period_likes"],
        "sync_period_hours": metrics["sync_period_hours"],
        "copycat": bool(row.get("copycat")),
        "is_pushed": _is_pushed(row),
        "select_json": row.get("select") or {},
        "video_url": _text(row.get("video")),
        "image_url": _text(row.get("image")),
        "invisible": bool(row.get("invisible")),
        "invisible_region": _text(row.get("invisible_region")),
        "message_html": str(row.get("message") or ""),
        "card_metrics": metrics,
        "raw_json": dict(row),
    }


def _default_base_url() -> str:
    try:
        from appcore import pushes

        return pushes.get_localized_texts_base_url() or "https://os.wedev.vip"
    except Exception:
        return "https://os.wedev.vip"


def _default_headers() -> dict[str, str]:
    try:
        from appcore import pushes

        headers = dict(pushes.build_localized_texts_headers())
        headers.pop("Content-Type", None)
        headers["Accept"] = "application/json"
        return headers
    except Exception:
        return {"Accept": "application/json"}


def _requests_fetch(method: str, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = requests.request(method, url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def _is_login_expired(payload: dict[str, Any]) -> bool:
    return payload.get("is_guest") is True or str(payload.get("message") or "").startswith("登录")


class MetaHotPostsClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        fetch_fn: FetchFn | None = None,
        headers_fn: HeadersFn | None = None,
        min_interval_seconds: float = 3.2,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = (base_url or _default_base_url()).rstrip("/")
        self._fetch_fn = fetch_fn or _requests_fetch
        self._headers_fn = headers_fn or _default_headers
        self._min_interval_seconds = max(3.0, float(min_interval_seconds))
        self._sleep_fn = sleep_fn
        self._monotonic_fn = monotonic_fn
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        now = self._monotonic_fn()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < self._min_interval_seconds:
                self._sleep_fn(round(self._min_interval_seconds - elapsed, 6))

    def fetch_page(self, *, page: int = 1, params: dict[str, Any] | None = None, **extra_params: Any) -> dict[str, Any]:
        headers = self._headers_fn()
        if "Authorization" not in headers and "Cookie" not in headers:
            raise WedevCredentialsMissingError("wedev credentials are missing")
        query_params = dict(params or {})
        query_params.update(extra_params)
        query_params["page"] = int(page)

        self._throttle()
        payload = self._fetch_fn(
            "GET",
            f"{self._base_url}/api/spy/hot/posts",
            params=query_params,
            headers=headers,
        )
        self._last_request_at = self._monotonic_fn()
        if _is_login_expired(payload):
            raise WedevCredentialsExpiredError(str(payload.get("message") or "wedev login expired"))
        data = payload.get("data") or {}
        raw_items = data.get("items") or []
        items = [normalize_hot_post(item) for item in raw_items if isinstance(item, dict)]
        return {
            "items": items,
            "raw_items": raw_items,
            "total": int(data.get("total") or len(items)),
            "size": int(data.get("size") or len(items) or 0),
            "raw_json": json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
        }
