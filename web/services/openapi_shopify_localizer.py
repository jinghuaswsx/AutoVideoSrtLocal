"""OpenAPI Shopify image localizer bootstrap response assembly."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
from typing import Callable
from urllib.parse import unquote, urlparse

from appcore import medias, product_link_domains, shopify_image_tasks
from web.services.openapi_materials_serializers import media_download_url, serialize_shopify_image_task


IsValidLanguageFn = Callable[[str], bool]
GetProductByCodeFn = Callable[[str], dict | None]
ResolveShopifyProductIdFn = Callable[[int, str | None], str | None]
ListReferenceImagesFn = Callable[[int, str], list[dict]]
GetLanguageNameFn = Callable[[str], str]
MediaDownloadUrlFn = Callable[[str | None], str | None]
ResolveLinkUrlsFn = Callable[[dict, str], list[dict[str, str]]]
ListDomainsFn = Callable[..., list[dict]]
ClaimNextTaskFn = Callable[..., dict | None]
HeartbeatTaskFn = Callable[[int, str, int], int]
CompleteTaskFn = Callable[[int, dict], dict]
FailTaskFn = Callable[[int, str, str, dict], dict]
SerializeShopifyImageTaskFn = Callable[[dict | None], dict | None]
UpdateProductFn = Callable[..., int]
SOURCE_INDEX_RE = re.compile(r"from_url_en_(\d+)_", re.I)
SOURCE_TOKEN_RE = re.compile(r"([0-9a-f]{28,})", re.I)
SOURCE_IMAGE_EXTENSIONS = ("webp", "jpg", "jpeg", "png", "gif", "avif")


@dataclass(frozen=True)
class ShopifyLocalizerBootstrapError(Exception):
    error: str
    status_code: int
    message: str | None = None


def _source_index_from_filename(value: str | None) -> int | None:
    match = SOURCE_INDEX_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _source_token_from_filename(value: str | None) -> str | None:
    match = SOURCE_TOKEN_RE.search(str(value or ""))
    return match.group(1).lower() if match else None


def _strip_wrapped_source_image_extension(stem: str) -> str:
    normalized = str(stem or "").strip()
    while normalized:
        lowered = normalized.lower()
        for ext in SOURCE_IMAGE_EXTENSIONS:
            dot_suffix = f".{ext}"
            underscore_suffix = f"_{ext}"
            if lowered.endswith(dot_suffix):
                normalized = normalized[: -len(dot_suffix)]
                break
            if lowered.endswith(underscore_suffix):
                normalized = normalized[: -len(underscore_suffix)]
                break
        else:
            break
    return normalized


def _source_name_key(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = urlparse(raw).path if "://" in raw or raw.startswith("//") else raw.split("?", 1)[0]
    name = PurePosixPath(unquote(path).replace("\\", "/")).name
    if not name:
        return None
    match = SOURCE_INDEX_RE.search(name)
    if match:
        name = name[match.end():]
    stem = PurePosixPath(name).stem
    normalized = _strip_wrapped_source_image_extension(stem).strip().lower()
    return f"name:{normalized}" if normalized else None


def _is_gif_candidate(filename: str | None, object_key: str | None) -> bool:
    for value in (filename, object_key):
        lowered = str(value or "").strip().lower().split("?", 1)[0]
        if lowered.endswith(".gif"):
            return True
    return False


def build_shopify_localizer_domains_response(
    *,
    list_domains_fn: ListDomainsFn | None = None,
) -> dict:
    list_domains_fn = list_domains_fn or product_link_domains.list_domains
    rows = list_domains_fn(include_disabled=True)
    items: list[dict] = []
    for row in rows or []:
        domain = str(row.get("domain") or "").strip().lower()
        if not domain:
            continue
        items.append({
            "id": int(row.get("id") or 0),
            "domain": domain,
            "enabled": bool(row.get("enabled", True)),
        })
    return {"items": items}


def _serialize_detail_images(
    rows: list[dict],
    *,
    media_download_url_fn: MediaDownloadUrlFn,
) -> list[dict]:
    images: list[dict] = []
    for item in rows or []:
        object_key = (item.get("object_key") or "").strip()
        filename = item.get("filename")
        if item.get("kind") != "detail" or not object_key:
            continue
        if _is_gif_candidate(filename, object_key):
            continue
        images.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "filename": filename,
            "url": media_download_url_fn(object_key),
            "source_index": _source_index_from_filename(filename),
            "source_name_key": _source_name_key(filename),
            "source_token": _source_token_from_filename(filename),
        })
    token_counts: dict[str, int] = {}
    for image in images:
        token = image.get("source_token")
        if token:
            token_counts[token] = token_counts.get(token, 0) + 1
    for image in images:
        token = image.get("source_token")
        duplicate_count = token_counts.get(token or "", 0)
        image["source_duplicate_count"] = duplicate_count
        image["source_duplicate"] = duplicate_count > 1
    return images


def _parse_lock_seconds(value, *, default: int = 900) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def build_shopify_localizer_bootstrap_response(
    body: dict | None,
    *,
    is_valid_language_fn: IsValidLanguageFn | None = None,
    get_product_by_code_fn: GetProductByCodeFn | None = None,
    resolve_shopify_product_id_fn: ResolveShopifyProductIdFn | None = None,
    list_reference_images_for_lang_fn: ListReferenceImagesFn | None = None,
    get_language_name_fn: GetLanguageNameFn | None = None,
    resolve_link_urls_fn: ResolveLinkUrlsFn | None = None,
    media_download_url_fn: MediaDownloadUrlFn = media_download_url,
) -> dict:
    body = body or {}
    product_code = str(body.get("product_code") or "").strip().lower()
    lang = str(body.get("lang") or "").strip().lower()
    domain = str(body.get("domain") or "").strip().lower()
    if not product_code or not lang:
        raise ShopifyLocalizerBootstrapError("missing product_code or lang", 400)

    is_valid_language_fn = is_valid_language_fn or medias.is_valid_language
    get_product_by_code_fn = get_product_by_code_fn or medias.get_product_by_code
    resolve_shopify_product_id_fn = resolve_shopify_product_id_fn or medias.resolve_shopify_product_id
    list_reference_images_for_lang_fn = list_reference_images_for_lang_fn or medias.list_shopify_localizer_images
    get_language_name_fn = get_language_name_fn or medias.get_language_name
    resolve_link_urls_fn = resolve_link_urls_fn or shopify_image_tasks.resolve_link_urls

    if not is_valid_language_fn(lang):
        raise ShopifyLocalizerBootstrapError("invalid lang", 400)
    if lang == "en":
        raise ShopifyLocalizerBootstrapError(
            "invalid_target_lang",
            400,
            "英文为源语言，不能作为图片本地化目标语言。",
        )

    product = get_product_by_code_fn(product_code)
    if not product:
        raise ShopifyLocalizerBootstrapError("product not found", 404)

    shopify_product_id_override = str(body.get("shopify_product_id") or "").strip()
    shopify_product_id = shopify_product_id_override or resolve_shopify_product_id_fn(int(product["id"]), domain or None)
    # 如果客户端提供了 shopify_product_id 和 domain，保存到 per-domain 缓存
    if shopify_product_id_override and domain and shopify_product_id_override.isdigit():
        try:
            medias.save_shopify_product_id_for_domain(int(product["id"]), domain, shopify_product_id_override)
        except Exception:
            pass
    if not shopify_product_id:
        raise ShopifyLocalizerBootstrapError(
            "shopify_product_id_missing",
            409,
            "未找到 Shopify ID。请先到产品编辑页最底部填写 Shopify ID 后，再执行图片本地化工具。",
        )

    reference_images = _serialize_detail_images(
        list_reference_images_for_lang_fn(int(product["id"]), "en"),
        media_download_url_fn=media_download_url_fn,
    )
    localized_images = _serialize_detail_images(
        list_reference_images_for_lang_fn(int(product["id"]), lang),
        media_download_url_fn=media_download_url_fn,
    )
    if not reference_images:
        raise ShopifyLocalizerBootstrapError("english references not ready", 409)
    if not localized_images:
        raise ShopifyLocalizerBootstrapError("localized images not ready", 409)

    link_urls = resolve_link_urls_fn(product, lang)
    return {
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code"),
            "shopify_product_id": shopify_product_id,
            "name": product.get("name"),
        },
        "link_url": link_urls[0]["url"] if link_urls else "",
        "link_urls": link_urls,
        "language": {
            "code": lang,
            "name_zh": get_language_name_fn(lang),
            "shop_locale": lang,
            "folder_code": lang,
        },
        "reference_images": reference_images,
        "localized_images": localized_images,
    }


def _loads_localized_links(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_shopify_localizer_product_link_save_response(
    body: dict | None,
    *,
    is_valid_language_fn: IsValidLanguageFn | None = None,
    get_product_by_code_fn: GetProductByCodeFn | None = None,
    update_product_fn: UpdateProductFn | None = None,
) -> dict:
    body = body or {}
    product_code = str(body.get("product_code") or "").strip().lower()
    lang = str(body.get("lang") or "").strip().lower()
    domain = str(body.get("domain") or "").strip().lower()
    link_url = str(body.get("link_url") or "").strip()
    if not product_code or not lang or not link_url:
        raise ShopifyLocalizerBootstrapError("missing product_code, lang or link_url", 400)
    if not link_url.startswith(("http://", "https://")):
        raise ShopifyLocalizerBootstrapError("link_url must be http(s)", 400)

    is_valid_language_fn = is_valid_language_fn or medias.is_valid_language
    get_product_by_code_fn = get_product_by_code_fn or medias.get_product_by_code
    update_product_fn = update_product_fn or medias.update_product
    if not is_valid_language_fn(lang):
        raise ShopifyLocalizerBootstrapError("invalid lang", 400)

    product = get_product_by_code_fn(product_code)
    if not product:
        raise ShopifyLocalizerBootstrapError("product not found", 404)

    links = _loads_localized_links(product.get("localized_links_json"))
    if domain:
        try:
            normalized_domain = product_link_domains.normalize_domain(domain)
        except ValueError:
            normalized_domain = product_link_domains.domain_from_url(link_url)
        if not normalized_domain:
            raise ShopifyLocalizerBootstrapError("domain_invalid", 400)
        bucket = links.get(lang)
        bucket = dict(bucket) if isinstance(bucket, dict) else {}
        bucket[normalized_domain] = link_url
        links[lang] = bucket
        saved_domain = normalized_domain
    else:
        links[lang] = link_url
        saved_domain = product_link_domains.domain_from_url(link_url)

    update_product_fn(int(product["id"]), localized_links_json=links)
    return {
        "ok": True,
        "saved": True,
        "product_id": int(product["id"]),
        "product_code": product_code,
        "lang": lang,
        "domain": saved_domain,
        "link_url": link_url,
        "localized_links_json": links,
    }


def build_shopify_localizer_task_claim_response(
    body: dict | None,
    *,
    claim_next_task_fn: ClaimNextTaskFn | None = None,
    serialize_shopify_image_task_fn: SerializeShopifyImageTaskFn = serialize_shopify_image_task,
) -> dict:
    body = body or {}
    claim_next_task_fn = claim_next_task_fn or shopify_image_tasks.claim_next_task
    worker_id = str(body.get("worker_id") or "").strip() or "unknown-worker"
    lock_seconds = _parse_lock_seconds(body.get("lock_seconds"))
    task = claim_next_task_fn(worker_id, lock_seconds=lock_seconds)
    return {"task": serialize_shopify_image_task_fn(task)}


def build_shopify_localizer_task_heartbeat_response(
    task_id: int,
    body: dict | None,
    *,
    heartbeat_task_fn: HeartbeatTaskFn | None = None,
) -> dict:
    body = body or {}
    heartbeat_task_fn = heartbeat_task_fn or shopify_image_tasks.heartbeat_task
    worker_id = str(body.get("worker_id") or "").strip()
    lock_seconds = _parse_lock_seconds(body.get("lock_seconds"))
    updated = heartbeat_task_fn(task_id, worker_id, lock_seconds)
    return {"ok": bool(updated)}


def build_shopify_localizer_task_complete_response(
    task_id: int,
    body: dict | None,
    *,
    complete_task_fn: CompleteTaskFn | None = None,
) -> dict:
    body = body or {}
    complete_task_fn = complete_task_fn or shopify_image_tasks.complete_task
    status = complete_task_fn(task_id, body.get("result") or {})
    return {"ok": True, "status": status}


def build_shopify_localizer_task_fail_response(
    task_id: int,
    body: dict | None,
    *,
    fail_task_fn: FailTaskFn | None = None,
) -> dict:
    body = body or {}
    fail_task_fn = fail_task_fn or shopify_image_tasks.fail_task
    status = fail_task_fn(
        task_id,
        str(body.get("error_code") or "worker_failed"),
        str(body.get("error_message") or ""),
        body.get("result") or {},
    )
    return {"ok": True, "status": status}
