"""Collect Dianxiaomi Listing top recent-sales archive for Mingkong selection.

Spec: docs/superpowers/specs/2026-05-18-dianxiaomi-full-listing-archive-design.md
Spec: docs/superpowers/specs/2026-05-19-mingkong-product-assets-dedup-top500-design.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import local_media_storage, pushes
from appcore import scheduled_tasks
from appcore.db import get_conn, query
from appcore.link_check_fetcher import extract_images_from_html
from tools.shopifyid_dianxiaomi_sync import _connect_existing_browser_context


TASK_CODE = "dianxiaomi_listing_ranking_sync"
TASK_NAME = "店小秘 Listing 近7天有销量全量归档"

DXM02_BROWSER_CDP_URL = "http://127.0.0.1:9223"
DXM02_BROWSER_SERVICE_NAME = "autovideosrt-dxm02-mk-vnc.service"
LISTING_PAGE_URL = "https://www.dianxiaomi.com/web/stat/salesStatistics"
LISTING_API_URL = "https://www.dianxiaomi.com/api/stat/product/statSalesPageListNew.json"

DEFAULT_START_DATE = date(2026, 4, 23)
DEFAULT_TARGET_ROWS = 500
DEFAULT_PAGE_SIZE = 100
DEFAULT_DAILY_OFFSET_DAYS = 1
DEFAULT_SNAPSHOT_WINDOW_DAYS = 7
OUTPUT_DIR = REPO_ROOT / "output" / "dianxiaomi_listing_ranking_sync"
PRODUCT_MAIN_IMAGE_CACHE_PREFIX = "xuanpin/product-main-images"
MAX_PRODUCT_IMAGE_BYTES = 15 * 1024 * 1024
PRODUCT_ASSET_USER_AGENT = "Mozilla/5.0 AutoVideoSrt-XuanpinAssets"
_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.I)
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_LISTING_IMAGE_FIELDS = (
    "imageUrl",
    "image_url",
    "imgUrl",
    "img_url",
    "mainImage",
    "main_image",
    "mainImageUrl",
    "main_image_url",
    "productImage",
    "product_image",
    "productImageUrl",
    "product_image_url",
    "pictureUrl",
    "picUrl",
    "thumbnail",
    "thumbUrl",
    "image",
)


ListingFetchPage = Callable[[date, int, int], dict[str, Any]]


def parse_yyyy_mm_dd(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _date_text(value: str | date | datetime) -> str:
    return parse_yyyy_mm_dd(value).isoformat()


def _iter_dates(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def resolve_daily_target_date(*, today: date | None = None, offset_days: int = DEFAULT_DAILY_OFFSET_DAYS) -> date:
    base = today or date.today()
    return base - timedelta(days=max(0, int(offset_days)))


def resolve_rolling_dates(
    *,
    today: date | None = None,
    rolling_days: int = 7,
    offset_days: int = 0,
) -> list[date]:
    end_date = resolve_daily_target_date(today=today, offset_days=offset_days)
    safe_days = max(1, int(rolling_days))
    start_date = end_date - timedelta(days=safe_days - 1)
    return _iter_dates(start_date, end_date)


def build_listing_payload(
    snapshot_date: str | date | datetime,
    *,
    page_no: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    window_days: int = DEFAULT_SNAPSHOT_WINDOW_DAYS,
    sort_type: str = "paidProductCount",
    is_desc: str = "1",
) -> dict[str, Any]:
    end_date = parse_yyyy_mm_dd(snapshot_date)
    safe_window_days = max(1, int(window_days or DEFAULT_SNAPSHOT_WINDOW_DAYS))
    begin_date = end_date - timedelta(days=safe_window_days - 1)
    return {
        "shopIds": "all",
        "shopGroupId": "",
        "sortType": sort_type,
        "isDesc": is_desc,
        "pageNo": int(page_no),
        "pageSize": int(page_size),
        "beginDate": begin_date.isoformat(),
        "endDate": end_date.isoformat(),
        "searchType": "productId",
        "searchValue": "",
        "searchCondition": "2",
    }


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        text = text.replace("CNY", "").replace("USD", "").replace("$", "").replace("¥", "").strip()
    else:
        text = value
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_cny(value: Any) -> str:
    amount = _as_float(value)
    if amount is None:
        return str(value or "").strip()
    return f"CNY {amount:.2f}"


def _format_percent(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if text.endswith("%"):
        return text
    amount = _as_float(text)
    if amount is None:
        return text
    if amount == int(amount):
        return f"{int(amount)}%"
    return f"{amount:g}%"


def _strip_rjc(value: Any) -> str:
    return _RJC_SUFFIX_RE.sub("", str(value or "").strip()).lower()


def _absolute_url(value: Any, base_url: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    absolute = urljoin(base_url, text)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("meta", {"property": name}) or soup.find("meta", {"name": name})
        if node and node.get("content"):
            return str(node.get("content") or "").strip()
    return ""


def _first_listing_image_url(row: Mapping[str, Any], *, base_url: str = "") -> str:
    for field in _LISTING_IMAGE_FIELDS:
        value = row.get(field)
        if isinstance(value, Mapping):
            value = value.get("url") or value.get("src") or value.get("imageUrl")
        elif isinstance(value, list):
            value = value[0] if value else ""
            if isinstance(value, Mapping):
                value = value.get("url") or value.get("src") or value.get("imageUrl")
        url = _absolute_url(value, base_url or str(row.get("sourceUrl") or row.get("productUrl") or ""))
        if url:
            return url
    return ""


def product_code_from_url(value: Any) -> str:
    parsed = urlparse(str(value or "").strip())
    parts = [part for part in parsed.path.replace("\\", "/").split("/") if part]
    if "products" in parts:
        index = parts.index("products")
        if index + 1 < len(parts):
            return _strip_rjc(parts[index + 1])
    return ""


def extract_product_page_assets_from_html(html: str, *, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    meta_image = _absolute_url(_meta_content(soup, "og:image", "twitter:image"), base_url)
    images = extract_images_from_html(html or "", base_url=base_url)
    carousel_images = [
        str(item.get("source_url") or "").strip()
        for item in images
        if (item.get("kind") or "") == "carousel" and str(item.get("source_url") or "").strip()
    ]
    detail_images = [
        str(item.get("source_url") or "").strip()
        for item in images
        if (item.get("kind") or "") == "detail" and str(item.get("source_url") or "").strip()
    ]
    main_image_url = (carousel_images[0] if carousel_images else "") or meta_image
    return {
        "main_image_url": main_image_url,
        "detail_image_urls": detail_images,
    }


def fetch_product_page_assets(
    product_url: str,
    *,
    session: requests.Session,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not str(product_url or "").strip():
        return {"main_image_url": "", "detail_image_urls": []}
    resp = session.get(
        str(product_url).strip(),
        timeout=timeout_seconds,
        headers={"User-Agent": PRODUCT_ASSET_USER_AGENT, "Accept": "text/html,*/*"},
    )
    resp.raise_for_status()
    return extract_product_page_assets_from_html(resp.text, base_url=getattr(resp, "url", None) or product_url)


def _content_type_for_image_response(response, image_url: str) -> str:
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not content_type:
        content_type = mimetypes.guess_type(urlparse(image_url).path)[0] or "image/jpeg"
    return content_type


def _image_extension(content_type: str, image_url: str) -> str:
    parsed_suffix = Path(urlparse(image_url).path).suffix.lower()
    if parsed_suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if parsed_suffix == ".jpeg" else parsed_suffix
    return _IMAGE_EXTENSIONS.get(content_type, ".jpg")


def _safe_product_asset_slug(*, product_code: str, product_id: str) -> str:
    source = product_code or product_id or "unknown-product"
    slug = re.sub(r"[^a-z0-9-]+", "-", source.lower()).strip("-")
    return slug or "unknown-product"


def product_main_image_object_key(
    image_url: str,
    *,
    product_id: str,
    product_code: str,
    content_type: str = "",
) -> str:
    parsed = urlparse(str(image_url or ""))
    content_type = content_type or mimetypes.guess_type(parsed.path)[0] or "image/jpeg"
    ext = _image_extension(content_type, image_url)
    slug = _safe_product_asset_slug(product_code=product_code, product_id=product_id)
    digest = hashlib.sha256(
        "|".join([str(product_id or ""), str(product_code or ""), str(image_url or "")]).encode("utf-8")
    ).hexdigest()[:24]
    return f"{PRODUCT_MAIN_IMAGE_CACHE_PREFIX}/{slug}/{digest}{ext}"


def cache_product_main_image(
    image_url: str,
    *,
    product_id: str,
    product_code: str,
    storage_exists_fn=local_media_storage.exists,
    write_bytes_fn=local_media_storage.write_bytes,
    http_get_fn=requests.get,
    max_image_bytes: int = MAX_PRODUCT_IMAGE_BYTES,
    timeout_seconds: int = 20,
) -> str:
    parsed = urlparse(str(image_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    object_key = product_main_image_object_key(
        image_url,
        product_id=str(product_id or ""),
        product_code=str(product_code or ""),
    )
    if storage_exists_fn(object_key):
        return object_key

    resp = http_get_fn(
        image_url,
        timeout=timeout_seconds,
        stream=True,
        headers={"User-Agent": PRODUCT_ASSET_USER_AGENT},
    )
    resp.raise_for_status()
    content_type = _content_type_for_image_response(resp, image_url)
    if not content_type.startswith("image/"):
        raise ValueError(f"product main image is not an image: {content_type}")
    object_key = product_main_image_object_key(
        image_url,
        product_id=str(product_id or ""),
        product_code=str(product_code or ""),
        content_type=content_type,
    )
    if storage_exists_fn(object_key):
        return object_key
    payload = bytearray()
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        payload.extend(chunk)
        if len(payload) > max_image_bytes:
            raise ValueError("product main image too large (>15MB)")
    try:
        write_bytes_fn(object_key, bytes(payload))
    except Exception:
        if storage_exists_fn(object_key):
            return object_key
        raise
    return object_key


def extract_product_cn_name_from_material_filename(filename: Any) -> str:
    basename = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    if not basename:
        return ""
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", basename)
    rest = re.sub(r"^\d{4}\.\d{2}\.\d{2}-", "", stem)
    marker = "-原素材"
    if marker in rest:
        return rest.split(marker, 1)[0].strip()
    parts = [part.strip() for part in rest.split("-") if part.strip()]
    return parts[0] if parts else rest.strip()


def _normalize_mk_media_path(raw_path: Any) -> str:
    path = str(raw_path or "").strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    if not path or ".." in path.split("/"):
        return ""
    return path


def _mk_material_url(video: Mapping[str, Any], *, base_url: str) -> str:
    for key in ("url", "href"):
        raw_url = str(video.get(key) or "").strip()
        parsed = urlparse(raw_url)
        if parsed.scheme in {"http", "https"}:
            return raw_url
    media_path = _normalize_mk_media_path(video.get("path"))
    if not media_path or not base_url:
        return ""
    return f"{base_url.rstrip('/')}/medias/{quote(media_path, safe='/')}"


def _mingkong_item_product_codes(item: Mapping[str, Any]) -> set[str]:
    codes = {
        _strip_rjc(item.get(key))
        for key in ("product_code", "code", "handle")
        if str(item.get(key) or "").strip()
    }
    for link in item.get("product_links") or []:
        code = product_code_from_url(link)
        if code:
            codes.add(code)
    return {code for code in codes if code}


def find_first_mingkong_material_for_product_code(
    items: list[dict[str, Any]],
    product_code: str,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    target = _strip_rjc(product_code)
    empty = {
        "product_cn_name": "",
        "mk_first_material_name": "",
        "mk_first_material_path": "",
        "mk_first_material_url": "",
    }
    if not target:
        return empty
    for item in items or []:
        if target not in _mingkong_item_product_codes(item):
            continue
        for video in item.get("videos") or []:
            if not isinstance(video, Mapping) or video.get("hidden"):
                continue
            name = str(video.get("name") or os.path.basename(str(video.get("path") or ""))).strip()
            path = _normalize_mk_media_path(video.get("path"))
            if not name and not path:
                continue
            return {
                "product_cn_name": extract_product_cn_name_from_material_filename(name or path),
                "mk_first_material_name": name,
                "mk_first_material_path": path,
                "mk_first_material_url": _mk_material_url(video, base_url=base_url),
            }
    return empty


def _build_mingkong_headers() -> dict[str, str]:
    headers = dict(pushes.build_localized_texts_headers())
    headers.pop("Content-Type", None)
    headers["Accept"] = "application/json"
    return headers


def _mingkong_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def search_mingkong_materials_for_product_code(
    product_code: str,
    *,
    session: requests.Session,
    headers: dict[str, str],
    base_url: str,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    if not product_code or ("Authorization" not in headers and "Cookie" not in headers):
        return []
    resp = session.get(
        f"{base_url}/api/marketing/medias",
        params={"page": 1, "q": product_code, "source": "", "level": "", "show_attention": 0},
        headers=headers,
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
        raise RuntimeError("Mingkong credentials expired")
    return [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]


def enrich_listing_rows(
    rows: list[dict[str, Any]],
    *,
    timeout_seconds: int = 20,
) -> list[dict[str, Any]]:
    page_session = requests.Session()
    mk_session = requests.Session()
    try:
        mk_headers = _build_mingkong_headers()
    except Exception:
        mk_headers = {}
    base_url = _mingkong_base_url()
    product_asset_cache: dict[str, dict[str, Any]] = {}
    mingkong_cache: dict[str, dict[str, Any]] = {}

    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        product_url = str(item.get("product_url") or "").strip()
        product_id = str(item.get("product_id") or "").strip()
        product_code = str(item.get("product_code") or product_code_from_url(product_url)).strip().lower()
        item["product_code"] = product_code

        asset_cache_key = product_url or product_id or product_code
        if asset_cache_key and asset_cache_key in product_asset_cache:
            item.update(product_asset_cache[asset_cache_key])
        else:
            asset_update = {
                "product_main_image_url": item.get("product_main_image_url") or "",
                "product_main_image_object_key": item.get("product_main_image_object_key"),
                "product_detail_images_json": item.get("product_detail_images_json"),
                "product_assets_error": None,
            }
            try:
                page_assets = fetch_product_page_assets(
                    product_url,
                    session=page_session,
                    timeout_seconds=timeout_seconds,
                ) if product_url else {"main_image_url": "", "detail_image_urls": []}
                if page_assets.get("main_image_url"):
                    asset_update["product_main_image_url"] = page_assets["main_image_url"]
                detail_urls = page_assets.get("detail_image_urls") or []
                if detail_urls:
                    asset_update["product_detail_images_json"] = json.dumps(
                        detail_urls,
                        ensure_ascii=False,
                    )
            except Exception as exc:
                asset_update["product_assets_error"] = str(exc)[:1000]

            image_url = str(asset_update.get("product_main_image_url") or "").strip()
            if image_url:
                try:
                    asset_update["product_main_image_object_key"] = cache_product_main_image(
                        image_url,
                        product_id=product_id,
                        product_code=product_code,
                        timeout_seconds=timeout_seconds,
                    )
                except Exception as exc:
                    error = str(exc)[:1000]
                    existing_error = str(asset_update.get("product_assets_error") or "").strip()
                    asset_update["product_assets_error"] = "; ".join(
                        [part for part in (existing_error, error) if part]
                    )
            if asset_cache_key:
                product_asset_cache[asset_cache_key] = asset_update
            item.update(asset_update)

        if product_code and product_code in mingkong_cache:
            item.update(mingkong_cache[product_code])
        else:
            mk_update = {
                "product_cn_name": item.get("product_cn_name") or "",
                "mk_first_material_name": item.get("mk_first_material_name") or "",
                "mk_first_material_path": item.get("mk_first_material_path") or "",
                "mk_first_material_url": item.get("mk_first_material_url") or "",
                "mk_material_error": None,
            }
            try:
                mk_items = search_mingkong_materials_for_product_code(
                    product_code,
                    session=mk_session,
                    headers=mk_headers,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                )
                mk_update.update(
                    find_first_mingkong_material_for_product_code(
                        mk_items,
                        product_code,
                        base_url=base_url,
                    )
                )
            except Exception as exc:
                mk_update["mk_material_error"] = str(exc)[:1000]
            if product_code:
                mingkong_cache[product_code] = mk_update
            item.update(mk_update)
        out.append(item)
    return out


def normalize_listing_row(
    row: Mapping[str, Any],
    *,
    snapshot_date: date,
    rank_position: int,
) -> dict[str, Any]:
    product_id = str(row.get("productId") or row.get("shopifyProductId") or "").strip()
    product_name = str(row.get("productName") or row.get("title") or "").strip()
    product_url = str(row.get("sourceUrl") or row.get("productUrl") or row.get("onlineUrl") or "").strip()
    store = str(row.get("shopName") or row.get("store") or row.get("shopId") or "").strip()
    platform = str(row.get("platform") or row.get("platformName") or "").strip()
    product_code = product_code_from_url(product_url)

    return {
        "product_id": product_id,
        "product_name": product_name,
        "product_url": product_url,
        "store": store,
        "platform": platform,
        "parent_sku": str(row.get("parentSku") or row.get("parent_sku") or "").strip(),
        "order_count": _as_int(row.get("paidOrderCount", row.get("orderCount"))),
        "sales_count": _as_int(row.get("paidProductCount", row.get("salesCount"))),
        "revenue_main": _format_cny(row.get("paidAmountCny", row.get("revenue"))),
        "revenue_split": _format_cny(row.get("averagePaidAmountCny", row.get("revenueSplit"))),
        "refund_orders": _as_int(row.get("refundOrderCount", row.get("refundOrders"))),
        "refund_qty": _as_int(row.get("refundProductCount", row.get("refundQty"))),
        "refund_amt": _format_cny(row.get("refundAmountCny", row.get("refundAmt"))),
        "refund_rate": _format_percent(row.get("refundRate")),
        "media_product_id": None,
        "product_code": product_code,
        "product_main_image_url": _first_listing_image_url(row, base_url=product_url),
        "product_main_image_object_key": None,
        "product_detail_images_json": None,
        "product_assets_error": None,
        "product_cn_name": "",
        "mk_first_material_name": "",
        "mk_first_material_path": "",
        "mk_first_material_url": "",
        "mk_material_error": None,
        "snapshot_date": snapshot_date,
        "rank_position": int(rank_position),
    }


def ensure_dianxiaomi_success(payload: Mapping[str, Any]) -> None:
    try:
        code = int(payload.get("code", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Dianxiaomi response has invalid code: {payload!r}") from exc
    if code != 0:
        raise RuntimeError(f"Dianxiaomi API failed: code={payload.get('code')} msg={payload.get('msg')}")


def extract_listing_page(payload: Mapping[str, Any]) -> dict[str, Any]:
    ensure_dianxiaomi_success(payload)
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    page = data.get("page") if isinstance(data.get("page"), Mapping) else {}
    items = page.get("list") or []
    return {
        "items": [item for item in items if isinstance(item, Mapping)],
        "page_no": _as_int(page.get("pageNo"), 1),
        "page_size": _as_int(page.get("pageSize"), DEFAULT_PAGE_SIZE),
        "total_size": _as_int(page.get("totalSize")),
        "total_page": _as_int(page.get("totalPage")),
    }


def collect_top_rankings_for_date(
    snapshot_date: date,
    *,
    fetch_page: ListingFetchPage,
    target_rows: int = DEFAULT_TARGET_ROWS,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pages_fetched = 0
    api_total_size = 0
    api_total_page = 0
    limit = max(0, int(target_rows or 0))
    page_no = 1

    while True:
        payload = fetch_page(snapshot_date, page_no, page_size)
        page = extract_listing_page(payload)
        pages_fetched += 1
        if page_no == 1:
            api_total_size = int(page["total_size"])
            api_total_page = int(page["total_page"])

        for index, raw in enumerate(page["items"]):
            rank_position = (page_no - 1) * page_size + index + 1
            normalized = normalize_listing_row(
                raw,
                snapshot_date=snapshot_date,
                rank_position=rank_position,
            )
            if normalized["product_id"] and int(normalized.get("sales_count") or 0) > 0:
                rows.append(normalized)
            if limit and len(rows) >= limit:
                break

        if limit and len(rows) >= limit:
            break
        if not page["items"]:
            break
        if api_total_page and page_no >= api_total_page:
            break
        if not api_total_page and len(page["items"]) < int(page_size):
            break
        page_no += 1

    out_rows = rows[:limit] if limit else rows
    return out_rows, {
        "pages_fetched": pages_fetched,
        "api_total_size": api_total_size,
        "api_total_page": api_total_page,
        "rows_fetched": len(out_rows),
    }


def select_missing_dates(
    *,
    start_date: date,
    end_date: date,
    existing_counts: Mapping[str | date | datetime, int],
    target_rows: int = DEFAULT_TARGET_ROWS,
) -> list[date]:
    normalized_counts = {
        parse_yyyy_mm_dd(key): int(value or 0)
        for key, value in existing_counts.items()
    }
    target = max(0, int(target_rows or 0))
    return [
        day
        for day in _iter_dates(start_date, end_date)
        if (normalized_counts.get(day, 0) <= 0 if target == 0 else normalized_counts.get(day, 0) < target)
    ]


def load_existing_counts(start_date: date, end_date: date) -> dict[date, int]:
    rows = query(
        """
        SELECT snapshot_date, COUNT(*) AS cnt
        FROM dianxiaomi_rankings
        WHERE snapshot_date BETWEEN %s AND %s
        GROUP BY snapshot_date
        """,
        (start_date, end_date),
    )
    return {
        parse_yyyy_mm_dd(row["snapshot_date"]): int(row.get("cnt") or 0)
        for row in rows
    }


def _clean_name(name: str) -> str:
    text = re.sub(r"[^\w\s-]", "", name)
    return re.sub(r"\s+", " ", text).strip().lower()


def _fetch_one(cursor, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    cursor.execute(sql, args)
    return cursor.fetchone()


def _match_media_product_id(cursor, row: Mapping[str, Any]) -> int | None:
    product_url = str(row.get("product_url") or "")
    match = re.search(r"/products/([^/?#]+)", product_url)
    if match:
        media = _fetch_one(
            cursor,
            "SELECT id FROM media_products WHERE product_code = %s AND deleted_at IS NULL LIMIT 1",
            (match.group(1),),
        )
        if media:
            return int(media["id"])

    product_name = str(row.get("product_name") or "").strip()
    if product_name:
        media = _fetch_one(
            cursor,
            "SELECT id FROM media_products WHERE name = %s AND deleted_at IS NULL LIMIT 1",
            (product_name,),
        )
        if media:
            return int(media["id"])

        keyword = _clean_name(product_name)[:40]
        if keyword:
            media = _fetch_one(
                cursor,
                "SELECT id FROM media_products WHERE LOWER(name) LIKE %s AND deleted_at IS NULL LIMIT 1",
                (f"%{keyword}%",),
            )
            if media:
                return int(media["id"])
    return None


def product_asset_key_for_row(row: Mapping[str, Any]) -> str:
    product_url = str(row.get("product_url") or "").strip()
    product_code = str(row.get("product_code") or product_code_from_url(product_url)).strip().lower()
    if product_code:
        return "code:" + hashlib.sha256(product_code.encode("utf-8")).hexdigest()
    if product_url:
        digest = hashlib.sha256(product_url.encode("utf-8")).hexdigest()
        return f"url:{digest}"
    product_id = str(row.get("product_id") or "").strip()
    if product_id:
        return "product_id:" + hashlib.sha256(product_id.encode("utf-8")).hexdigest()
    return ""


def _asset_db_value(value: Any) -> Any:
    if value == "":
        return None
    return value


def _merge_product_asset_record(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key == "asset_key":
            continue
        if value not in (None, ""):
            target[key] = value


def build_product_asset_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        asset_key = product_asset_key_for_row(row)
        if not asset_key:
            continue
        product_url = str(row.get("product_url") or "").strip()
        product_code = str(row.get("product_code") or product_code_from_url(product_url)).strip().lower()
        record = {
            "asset_key": asset_key,
            "product_id": str(row.get("product_id") or "").strip(),
            "product_code": product_code,
            "product_url": product_url,
            "product_name": str(row.get("product_name") or "").strip(),
            "product_main_image_url": row.get("product_main_image_url") or None,
            "product_main_image_object_key": row.get("product_main_image_object_key") or None,
            "product_detail_images_json": row.get("product_detail_images_json") or None,
            "product_assets_error": row.get("product_assets_error") or None,
            "product_cn_name": row.get("product_cn_name") or None,
            "mk_first_material_name": row.get("mk_first_material_name") or None,
            "mk_first_material_path": row.get("mk_first_material_path") or None,
            "mk_first_material_url": row.get("mk_first_material_url") or None,
            "mk_material_error": row.get("mk_material_error") or None,
        }
        if asset_key not in records:
            records[asset_key] = record
        else:
            _merge_product_asset_record(records[asset_key], record)
    return list(records.values())


def upsert_product_assets(cursor, rows: list[dict[str, Any]]) -> int:
    records = build_product_asset_records(rows)
    for record in records:
        cursor.execute(
            """
            INSERT INTO dianxiaomi_product_assets
                (asset_key, product_id, product_code, product_url, product_name,
                 product_main_image_url, product_main_image_object_key, product_detail_images_json,
                 product_assets_error, product_cn_name, mk_first_material_name,
                 mk_first_material_path, mk_first_material_url, mk_material_error, last_synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                product_id=COALESCE(NULLIF(VALUES(product_id), ''), product_id),
                product_code=COALESCE(NULLIF(VALUES(product_code), ''), product_code),
                product_url=COALESCE(NULLIF(VALUES(product_url), ''), product_url),
                product_name=COALESCE(NULLIF(VALUES(product_name), ''), product_name),
                product_main_image_url=COALESCE(NULLIF(VALUES(product_main_image_url), ''), product_main_image_url),
                product_main_image_object_key=COALESCE(NULLIF(VALUES(product_main_image_object_key), ''), product_main_image_object_key),
                product_detail_images_json=COALESCE(VALUES(product_detail_images_json), product_detail_images_json),
                product_assets_error=VALUES(product_assets_error),
                product_cn_name=COALESCE(NULLIF(VALUES(product_cn_name), ''), product_cn_name),
                mk_first_material_name=COALESCE(NULLIF(VALUES(mk_first_material_name), ''), mk_first_material_name),
                mk_first_material_path=COALESCE(NULLIF(VALUES(mk_first_material_path), ''), mk_first_material_path),
                mk_first_material_url=COALESCE(NULLIF(VALUES(mk_first_material_url), ''), mk_first_material_url),
                mk_material_error=VALUES(mk_material_error),
                last_synced_at=VALUES(last_synced_at),
                updated_at=NOW()
            """,
            (
                record["asset_key"],
                _asset_db_value(record.get("product_id")),
                _asset_db_value(record.get("product_code")),
                _asset_db_value(record.get("product_url")),
                _asset_db_value(record.get("product_name")),
                _asset_db_value(record.get("product_main_image_url")),
                _asset_db_value(record.get("product_main_image_object_key")),
                _asset_db_value(record.get("product_detail_images_json")),
                _asset_db_value(record.get("product_assets_error")),
                _asset_db_value(record.get("product_cn_name")),
                _asset_db_value(record.get("mk_first_material_name")),
                _asset_db_value(record.get("mk_first_material_path")),
                _asset_db_value(record.get("mk_first_material_url")),
                _asset_db_value(record.get("mk_material_error")),
            ),
        )
    return len(records)


def persist_rankings(snapshot_date: date, rows: list[dict[str, Any]]) -> dict[str, int]:
    conn = get_conn()
    matched = 0
    product_assets_upserted = 0
    product_ids = sorted({str(row.get("product_id") or "").strip() for row in rows if row.get("product_id")})
    try:
        conn.begin()
        with conn.cursor() as cursor:
            product_assets_upserted = upsert_product_assets(cursor, rows)
            for row in rows:
                media_product_id = _match_media_product_id(cursor, row)
                if media_product_id:
                    matched += 1
                cursor.execute(
                    """
                    INSERT INTO dianxiaomi_rankings
                        (product_id, product_name, product_url, store, platform, parent_sku,
                         order_count, sales_count, revenue_main, revenue_split,
                         refund_orders, refund_qty, refund_amt, refund_rate,
                         media_product_id, product_code, snapshot_date, rank_position)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        product_name=VALUES(product_name),
                        product_url=VALUES(product_url),
                        store=VALUES(store),
                        platform=VALUES(platform),
                        parent_sku=VALUES(parent_sku),
                        order_count=VALUES(order_count),
                        sales_count=VALUES(sales_count),
                        revenue_main=VALUES(revenue_main),
                        revenue_split=VALUES(revenue_split),
                        refund_orders=VALUES(refund_orders),
                        refund_qty=VALUES(refund_qty),
                        refund_amt=VALUES(refund_amt),
                        refund_rate=VALUES(refund_rate),
                        media_product_id=VALUES(media_product_id),
                        product_code=VALUES(product_code),
                        rank_position=VALUES(rank_position)
                    """,
                    (
                        row["product_id"],
                        row["product_name"],
                        row["product_url"],
                        row["store"],
                        row["platform"],
                        row["parent_sku"],
                        row["order_count"],
                        row["sales_count"],
                        row["revenue_main"],
                        row["revenue_split"],
                        row["refund_orders"],
                        row["refund_qty"],
                        row["refund_amt"],
                        row["refund_rate"],
                        media_product_id,
                        row.get("product_code") or None,
                        snapshot_date,
                        row["rank_position"],
                    ),
                )
            if product_ids:
                placeholders = ", ".join(["%s"] * len(product_ids))
                cursor.execute(
                    f"""
                    DELETE FROM dianxiaomi_rankings
                    WHERE snapshot_date = %s AND product_id NOT IN ({placeholders})
                    """,
                    [snapshot_date, *product_ids],
                )
            else:
                cursor.execute(
                    "DELETE FROM dianxiaomi_rankings WHERE snapshot_date = %s",
                    (snapshot_date,),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "stored_rows": len(rows),
        "matched_media_products": matched,
        "product_assets_upserted": product_assets_upserted,
    }


def guard_against_windows_local_mysql() -> None:
    if os.name != "nt":
        return
    from config import DB_HOST, DB_PORT

    host = str(DB_HOST or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"} and int(DB_PORT) == 3306:
        raise RuntimeError(
            "项目规则禁止在 Windows 本机连接 127.0.0.1:3306 MySQL；"
            "请在服务器环境运行店小秘 Listing 排名采集。"
        )


def _stringify_form_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): "" if value is None else str(value) for key, value in payload.items()}


def _parse_response_text(*, ok: bool, status: int | None, text: str) -> dict[str, Any]:
    if not ok:
        raise RuntimeError(f"Dianxiaomi request failed: HTTP {status}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Dianxiaomi returned non-JSON content: {text[:200]}") from exc
    ensure_dianxiaomi_success(payload)
    return payload


def _build_context_fetcher(context, *, timeout_ms: int, window_days: int) -> ListingFetchPage:
    def _fetch(snapshot_date: date, page_no: int, page_size: int) -> dict[str, Any]:
        payload = build_listing_payload(
            snapshot_date,
            page_no=page_no,
            page_size=page_size,
            window_days=window_days,
        )
        response = context.request.post(
            LISTING_API_URL,
            form=_stringify_form_payload(payload),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://www.dianxiaomi.com",
                "Referer": LISTING_PAGE_URL,
            },
            timeout=timeout_ms,
        )
        response_ok = getattr(response, "ok", False)
        if callable(response_ok):
            response_ok = response_ok()
        return _parse_response_text(
            ok=bool(response_ok),
            status=getattr(response, "status", None),
            text=response.text(),
        )

    return _fetch


def _select_dates(args: argparse.Namespace) -> tuple[list[date], dict[str, Any]]:
    if args.mode == "rolling":
        if args.target_date:
            target_date = parse_yyyy_mm_dd(args.target_date)
            selected = resolve_rolling_dates(
                today=target_date,
                rolling_days=args.rolling_days,
                offset_days=0,
            )
        else:
            selected = resolve_rolling_dates(
                rolling_days=args.rolling_days,
                offset_days=args.daily_offset_days,
            )
        return selected, {
            "mode": "rolling",
            "date_from": selected[0].isoformat(),
            "date_to": selected[-1].isoformat(),
            "rolling_days": int(args.rolling_days),
            "skipped_complete_dates": 0,
            "refresh_existing": True,
        }
    if args.mode == "daily":
        target_date = parse_yyyy_mm_dd(args.target_date) if args.target_date else resolve_daily_target_date(
            offset_days=args.daily_offset_days
        )
        return [target_date], {
            "mode": "daily",
            "date_from": target_date.isoformat(),
            "date_to": target_date.isoformat(),
            "skipped_complete_dates": 0,
        }
    if args.mode == "date":
        target_date = parse_yyyy_mm_dd(args.target_date)
        return [target_date], {
            "mode": "date",
            "date_from": target_date.isoformat(),
            "date_to": target_date.isoformat(),
            "skipped_complete_dates": 0,
        }

    start_date = parse_yyyy_mm_dd(args.start_date)
    end_date = parse_yyyy_mm_dd(args.end_date) if args.end_date else date.today()
    existing_counts = load_existing_counts(start_date, end_date)
    missing = select_missing_dates(
        start_date=start_date,
        end_date=end_date,
        existing_counts=existing_counts,
        target_rows=args.target_rows,
    )
    selected = missing[: max(1, int(args.max_days_per_run))]
    return selected, {
        "mode": "backfill",
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "missing_dates": [item.isoformat() for item in missing],
        "skipped_complete_dates": max(0, len(_iter_dates(start_date, end_date)) - len(missing)),
    }


def _write_report(summary: dict[str, Any]) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"dianxiaomi-listing-ranking-sync-{stamp}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def run_collection(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    guard_against_windows_local_mysql()
    selected_dates, base_summary = _select_dates(args)
    summary: dict[str, Any] = {
        **base_summary,
        "target_rows": int(args.target_rows),
        "page_size": int(args.page_size),
        "snapshot_window_days": int(args.snapshot_window_days),
        "uncapped": int(args.target_rows) <= 0,
        "selected_dates": [item.isoformat() for item in selected_dates],
        "fetched_days": 0,
        "pages_fetched": 0,
        "rows_fetched": 0,
        "rows_stored": 0,
        "matched_media_products": 0,
        "incomplete_dates": [],
        "daily_offset_days": int(args.daily_offset_days),
        "rolling_days": int(getattr(args, "rolling_days", 0) or 0),
    }
    if not selected_dates:
        output_file = _write_report(summary)
        return summary, output_file

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser, context = _connect_existing_browser_context(
            playwright,
            args.browser_cdp_url,
            browser_service_name=DXM02_BROWSER_SERVICE_NAME,
        )
        del browser
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LISTING_PAGE_URL, wait_until="domcontentloaded", timeout=args.timeout_seconds * 1000)
        page.wait_for_timeout(1000)
        fetch_page = _build_context_fetcher(
            context,
            timeout_ms=args.timeout_seconds * 1000,
            window_days=args.snapshot_window_days,
        )

        for snapshot_date in selected_dates:
            rows, fetch_stats = collect_top_rankings_for_date(
                snapshot_date,
                fetch_page=fetch_page,
                target_rows=args.target_rows,
                page_size=args.page_size,
            )
            if not args.skip_product_assets:
                rows = enrich_listing_rows(
                    rows,
                    timeout_seconds=args.asset_timeout_seconds,
                )
            if not rows and args.mode == "rolling":
                summary["incomplete_dates"].append({
                    "date": snapshot_date.isoformat(),
                    "rows": 0,
                    "api_total_size": fetch_stats["api_total_size"],
                    "skipped_persist": True,
                })
                continue
            persist_stats = persist_rankings(snapshot_date, rows)
            summary["fetched_days"] += 1
            summary["pages_fetched"] += int(fetch_stats["pages_fetched"])
            summary["rows_fetched"] += int(fetch_stats["rows_fetched"])
            summary["rows_stored"] += int(persist_stats["stored_rows"])
            summary["matched_media_products"] += int(persist_stats["matched_media_products"])
            if int(args.target_rows) > 0 and len(rows) < args.target_rows:
                summary["incomplete_dates"].append({
                    "date": snapshot_date.isoformat(),
                    "rows": len(rows),
                    "api_total_size": fetch_stats["api_total_size"],
                })

    output_file = _write_report(summary)
    return summary, output_file


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Dianxiaomi Listing recent-sales archive into dianxiaomi_rankings.")
    parser.add_argument("--mode", choices=("backfill", "daily", "date", "rolling"), default="backfill")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE.isoformat())
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--target-date", default="")
    parser.add_argument("--daily-offset-days", type=int, default=DEFAULT_DAILY_OFFSET_DAYS)
    parser.add_argument("--rolling-days", type=int, default=7)
    parser.add_argument("--max-days-per-run", type=int, default=1)
    parser.add_argument("--target-rows", type=int, default=DEFAULT_TARGET_ROWS, help="Rows per snapshot; default 500. Use 0 only for a manual uncapped archive.")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--snapshot-window-days", type=int, default=DEFAULT_SNAPSHOT_WINDOW_DAYS)
    parser.add_argument("--skip-product-assets", action="store_true")
    parser.add_argument("--asset-timeout-seconds", type=int, default=20)
    parser.add_argument(
        "--browser-cdp-url",
        default=os.environ.get("DXM_LISTING_BROWSER_CDP_URL", DXM02_BROWSER_CDP_URL),
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    guard_against_windows_local_mysql()
    run_id = scheduled_tasks.start_run(TASK_CODE)
    try:
        summary, output_file = run_collection(args)
    except Exception as exc:
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary={"error": str(exc), "mode": args.mode},
            error_message=str(exc),
        )
        raise
    scheduled_tasks.finish_run(
        run_id,
        status="success",
        summary=summary,
        output_file=output_file,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
