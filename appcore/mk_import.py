"""明空选品 → 素材库 自动入库 service。

详见 docs/superpowers/specs/2026-04-26-mk-import-design.md
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from appcore import local_media_storage, object_keys, product_link_domains, pushes

log = logging.getLogger(__name__)

# ---- 异常 ----
class MkImportError(Exception):
    """A 子系统通用基类。"""

class DuplicateError(MkImportError):
    """视频 filename 已在素材库。"""

class DownloadError(MkImportError):
    """MP4 下载失败。"""

class StorageError(MkImportError):
    """本地写盘 / 现有 add_item service 失败。"""

class DBError(MkImportError):
    """DB 操作失败。"""


# ---- helpers ----
_RJC_SUFFIX_RE = re.compile(r"-rjc$", re.IGNORECASE)
_MK_VIDEO_PROXY_PATHS = {"/xuanpin/api/mk-video", "/medias/api/mk-video"}
_MK_MEDIA_PROXY_PATHS = {"/xuanpin/api/mk-media", "/medias/api/mk-media"}
_PRODUCT_LINK_HEAD_TIMEOUT_SECONDS = 2.5
_PRODUCT_LINK_GET_TIMEOUT_SECONDS = 3.0
_MK_DETAIL_FETCH_TIMEOUT_SECONDS = 5


def _normalize_product_code(code: str | None) -> str:
    """Strip -RJC suffix (case-insensitive) and lowercase. Empty/None → ''."""
    if not code:
        return ""
    return _RJC_SUFFIX_RE.sub("", code.strip()).lower()


def _product_code_with_rjc(code: str | None) -> str:
    normalized = _normalize_product_code(code)
    return f"{normalized}-rjc" if normalized else ""


def _canonical_product_link(product_link: str | None, product_code: str) -> str:
    raw_link = str(product_link or "").strip()
    code = _product_code_with_rjc(product_code) if product_code else ""
    if not code:
        return raw_link
    domain = product_link_domains.domain_from_url(raw_link)
    if not domain:
        domain = product_link_domains.DEFAULT_PRODUCT_LINK_DOMAINS[0]
    return product_link_domains.build_product_page_url(domain, "en", code)


def _probe_product_link(url: str) -> tuple[bool, str | None]:
    if not url:
        return False, "empty url"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    head_ok = False
    head_status = None
    try:
        resp = requests.head(url, headers=headers, timeout=_PRODUCT_LINK_HEAD_TIMEOUT_SECONDS, allow_redirects=True)
        head_status = resp.status_code
        if 200 <= head_status < 400:
            head_ok = True
    except requests.RequestException:
        pass

    if head_ok:
        return True, None

    # Fallback to GET for any non-success HEAD status or exception
    try:
        get_resp = requests.get(
            url,
            headers=headers,
            timeout=_PRODUCT_LINK_GET_TIMEOUT_SECONDS,
            allow_redirects=True,
            stream=True,
        )
    except requests.RequestException as exc:
        if head_status is not None:
            return False, f"HTTP {head_status} (GET failed: {exc})"
        return False, str(exc)

    try:
        if 200 <= get_resp.status_code < 400:
            return True, None
        if head_status is not None and get_resp.status_code != head_status:
            return False, f"HTTP {get_resp.status_code} (HEAD: {head_status})"
        return False, f"HTTP {get_resp.status_code}"
    finally:
        get_resp.close()


def _product_link_warning(url: str) -> dict | None:
    ok, detail = _probe_product_link(url)
    if ok:
        return None
    return {
        "type": "product_link_unavailable",
        "message": "商品链接可能不可访问",
        "url": url,
        "detail": detail or "unavailable",
    }


def _append_step_result(
    step_results: dict[str, list[dict]],
    step_key: str,
    *,
    key: str,
    title: str,
    status: str,
    message: str,
    logs: list[str] | None = None,
) -> None:
    row = {
        "key": key,
        "title": title,
        "status": status,
        "message": message,
    }
    if logs:
        row["logs"] = [str(item) for item in logs if str(item or "").strip()]
    step_results.setdefault(step_key, []).append(row)


def _bind_imported_mk_material(**kwargs) -> dict:
    from appcore import media_video_materials

    return media_video_materials.bind_mk_material(**kwargs)


def _mk_product_links_from_meta(meta: dict, fallback: str | None = None) -> list[str]:
    links = []
    raw_links = meta.get("product_links")
    if isinstance(raw_links, list):
        links.extend(str(item or "").strip() for item in raw_links if str(item or "").strip())
    if links:
        return links
    first = str(meta.get("product_link") or fallback or "").strip()
    if first and first not in links:
        links.insert(0, first)
    return links


def _mk_binding_metadata(meta: dict, *, product_link: str | None, video_path: str, cover_path: str) -> dict:
    metadata = dict(meta)
    links = _mk_product_links_from_meta(meta, product_link)
    if links:
        metadata["product_link"] = links[0]
        metadata["product_links"] = links
    if video_path:
        metadata["video_path"] = video_path
    if cover_path:
        metadata["cover_path"] = cover_path
    return metadata


from appcore.db import execute, query_all, query_one
from appcore.medias import (
    create_item as _medias_create_item,
    replace_copywritings as _medias_replace_copywritings,
    set_product_cover as _medias_set_product_cover,
)


def _find_existing_product(normalized_code: str) -> dict | None:
    """Find media_product whose product_code, after stripping -RJC, equals normalized_code.

    Note: media_products.product_code is already stored lowercase per existing
    _validate_product_code logic, but may or may not have -RJC suffix.
    """
    normalized = _normalize_product_code(normalized_code)
    if not normalized:
        return None
    for code in dict.fromkeys([normalized, _product_code_with_rjc(normalized)]):
        if not code:
            continue
        row = query_one(
            "SELECT * FROM media_products "
            "WHERE deleted_at IS NULL "
            "AND product_code=%s "
            "LIMIT 1",
            (code,),
        )
        if row:
            return row
    return query_one(
        "SELECT * FROM media_products "
        "WHERE deleted_at IS NULL "
        "AND LOWER(REGEXP_REPLACE(COALESCE(product_code, ''), '-rjc$', '')) = %s "
        "LIMIT 1",
        (normalized,),
    )


def _find_existing_product_by_id(product_id: int | str | None, normalized_code: str) -> dict | None:
    try:
        resolved_id = int(product_id or 0)
    except (TypeError, ValueError):
        return None
    if resolved_id <= 0:
        return None
    row = query_one(
        "SELECT * FROM media_products "
        "WHERE id=%s AND deleted_at IS NULL "
        "LIMIT 1",
        (resolved_id,),
    )
    if not row:
        return None
    normalized = _normalize_product_code(normalized_code)
    if normalized and _normalize_product_code(row.get("product_code")) != normalized:
        log.warning(
            "mk import ignored mismatched media_product_id=%s product_code=%s expected=%s",
            resolved_id,
            row.get("product_code"),
            normalized,
        )
        return None
    return row


def _find_existing_product_from_meta(meta: dict, normalized_code: str) -> dict | None:
    for key in ("media_product_id", "product_id", "local_product_id"):
        existing = _find_existing_product_by_id(meta.get(key), normalized_code)
        if existing:
            return existing
    return _find_existing_product(normalized_code)


def _find_active_product_by_exact_code(product_code: str | None) -> dict | None:
    code = str(product_code or "").strip().lower()
    if not code:
        return None
    return query_one(
        "SELECT * FROM media_products "
        "WHERE deleted_at IS NULL "
        "AND LOWER(COALESCE(product_code, '')) = %s "
        "LIMIT 1",
        (code,),
    )


def _is_product_code_duplicate_error(exc: Exception) -> bool:
    parts = getattr(exc, "args", None)
    text = " ".join(str(part) for part in parts) if parts else str(exc)
    lowered = text.lower()
    return (
        "duplicate entry" in lowered
        and (
            "uk_media_products_product_code" in lowered
            or "product_code" in lowered
        )
    )


def _is_video_already_imported(filename: str) -> bool:
    """True if a media_item with this filename already exists (and not soft-deleted)."""
    if not filename:
        return False
    row = query_one(
        "SELECT 1 AS ok FROM media_items WHERE filename=%s AND deleted_at IS NULL LIMIT 1",
        (filename,),
    )
    return bool(row)


def list_imported_filenames(filenames: list[str]) -> set[str]:
    """Return filenames that already exist in non-deleted media_items."""
    if not filenames:
        return set()
    rows = query_all(
        "SELECT filename FROM media_items "
        "WHERE filename IN (" + ",".join(["%s"] * len(filenames)) + ") "
        "AND deleted_at IS NULL",
        tuple(filenames),
    )
    return {row["filename"] for row in rows}


def list_imported_metadata(filenames: list[str]) -> dict[str, dict[str, int]]:
    """Return dict mapping filename to its media_product_id and media_item_id."""
    if not filenames:
        return {}
    rows = query_all(
        "SELECT id, product_id, filename FROM media_items "
        "WHERE filename IN (" + ",".join(["%s"] * len(filenames)) + ") "
        "AND deleted_at IS NULL",
        tuple(filenames),
    )
    return {
        row["filename"]: {
            "media_product_id": int(row["product_id"]),
            "media_item_id": int(row["id"]),
        }
        for row in rows
    }



def _normalize_mk_media_path(raw_path: str) -> str:
    path = str(raw_path or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    return path


def _mk_proxy_media_path(value: str | None, allowed_paths: set[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.path in allowed_paths:
        path_values = parse_qs(parsed.query).get("path") or []
        return _normalize_mk_media_path(path_values[0] if path_values else "")
    if not parsed.scheme and not parsed.netloc and not raw.startswith("/"):
        return _normalize_mk_media_path(raw)
    return ""


def _mk_media_download_request(value: str, *, allowed_paths: set[str], accept: str) -> tuple[str, dict | None]:
    media_path = _mk_proxy_media_path(value, allowed_paths)
    if not media_path:
        return value, None

    base_url = (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")
    headers = dict(pushes.build_localized_texts_headers())
    headers.pop("Content-Type", None)
    headers["Accept"] = accept
    if "Authorization" not in headers and "Cookie" not in headers:
        raise DownloadError("明空凭据未配置，请先在设置页同步 wedev 凭据")
    return f"{base_url}/medias/{quote(media_path, safe='/')}", headers


def _download_mp4(url: str, dest_path: str, timeout: int = 120) -> int:
    """Stream MP4 to dest_path. Returns bytes written. Raises DownloadError."""
    try:
        download_url, headers = _mk_media_download_request(
            url,
            allowed_paths=_MK_VIDEO_PROXY_PATHS,
            accept="video/*,*/*;q=0.8",
        )
        request_kwargs = {"stream": True, "timeout": timeout}
        if headers is not None:
            request_kwargs["headers"] = headers
        with requests.get(download_url, **request_kwargs) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
            return total
    except DownloadError:
        raise
    except requests.RequestException as e:
        raise DownloadError(f"download failed: {e}") from e


def _download_cover(url: str | None, dest_path: str, timeout: int = 30) -> str | None:
    """Download cover image. Returns dest_path if downloaded, None if no URL provided."""
    if not url:
        return None
    try:
        download_url, headers = _mk_media_download_request(
            url,
            allowed_paths=_MK_MEDIA_PROXY_PATHS,
            accept="image/*,*/*;q=0.8",
        )
        request_kwargs = {"stream": True, "timeout": timeout}
        if headers is not None:
            request_kwargs["headers"] = headers
        with requests.get(download_url, **request_kwargs) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=16 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest_path
    except (DownloadError, requests.RequestException) as e:
        log.warning("cover download failed url=%s: %s", url, e)
        return None  # cover 下载失败不阻塞，只记日志


def _local_media_object_key_from_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.path == "/medias/object":
        values = parse_qs(parsed.query).get("object_key") or []
        return unquote(values[0]).strip() if values else ""
    prefix = "/medias/obj/"
    if parsed.path.startswith(prefix):
        return unquote(parsed.path[len(prefix):]).strip()
    if not parsed.scheme and not parsed.netloc and not raw.startswith("/"):
        normalized = raw.replace("\\", "/").lstrip("/")
        if (
            normalized.startswith(("artifacts/", "uploads/", "xuanpin/"))
            or re.match(r"^\d+/medias/", normalized)
        ):
            return normalized
    return ""


def _suffix_from_key_or_url(value: str | None, default: str = ".jpg") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    parsed = urlparse(raw)
    path = parsed.path if parsed.path else raw.split("?", 1)[0]
    suffix = Path(unquote(path)).suffix.lower()
    if suffix and re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return default


def _safe_image_filename(stem: str, suffix: str) -> str:
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(stem or "image")).strip("._")
    return f"{safe_stem or 'image'}{suffix or '.jpg'}"


def _write_file_to_media_store(path: str, object_key: str) -> int:
    with open(path, "rb") as handle:
        local_media_storage.write_stream(object_key, handle)
    return int(Path(path).stat().st_size)


def _copy_media_object(source_key: str, dest_key: str) -> int:
    fd, temp_name = tempfile.mkstemp(prefix="mki_media_copy_")
    os.close(fd)
    try:
        local_media_storage.download_to(source_key, temp_name)
        return _write_file_to_media_store(temp_name, dest_key)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _import_image_object(
    *,
    owner_uid: int,
    product_id: int,
    stem: str,
    source_url: str | None = None,
    source_object_key: str | None = None,
    tmp_dir: str,
    default_ext: str = ".jpg",
) -> str | None:
    source_key = str(source_object_key or "").strip() or _local_media_object_key_from_url(source_url)
    suffix = _suffix_from_key_or_url(source_key or source_url, default_ext)
    dest_key = object_keys.build_media_object_key(
        owner_uid,
        product_id,
        _safe_image_filename(stem, suffix),
    )
    try:
        if source_key:
            _copy_media_object(source_key, dest_key)
            return dest_key
        if source_url:
            tmp_path = os.path.join(tmp_dir, _safe_image_filename(f"download_{stem}", suffix))
            downloaded = _download_cover(source_url, tmp_path)
            if downloaded:
                _write_file_to_media_store(downloaded, dest_key)
                return dest_key
    except Exception as exc:
        log.warning("mk import image failed stem=%s source=%s: %s", stem, source_key or source_url, exc)
    return None


def _find_product_asset(normalized_code: str) -> dict | None:
    if not normalized_code:
        return None
    try:
        return query_one(
            "SELECT * FROM dianxiaomi_product_assets "
            "WHERE LOWER(COALESCE(product_code, '')) = %s "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            (normalized_code.lower(),),
        )
    except Exception as exc:
        log.debug("mk import product asset lookup failed code=%s: %s", normalized_code, exc)
        return None


def _fetch_mk_product_detail(mk_id: int | str | None) -> dict:
    if not mk_id:
        return {}
    try:
        resolved_mk_id = int(mk_id)
    except (TypeError, ValueError):
        return {}
    try:
        base_url = (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")
        headers = dict(pushes.build_localized_texts_headers())
        headers.pop("Content-Type", None)
        headers["Accept"] = "application/json"
        if "Authorization" not in headers and "Cookie" not in headers:
            return {}
        resp = requests.get(
            f"{base_url}/api/marketing/medias/{resolved_mk_id}",
            headers=headers,
            timeout=_MK_DETAIL_FETCH_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        item = (payload.get("data") or {}).get("item") or {}
        return item if isinstance(item, dict) else {}
    except Exception as exc:
        log.warning("mk import detail fetch failed mk_id=%s: %s", mk_id, exc)
        return {}


def _mk_text_body(text: dict) -> str:
    lines = []
    if text.get("title"):
        lines.append(f"标题: {str(text.get('title')).strip()}")
    if text.get("message"):
        lines.append(f"文案: {str(text.get('message')).strip()}")
    if text.get("description"):
        lines.append(f"描述: {str(text.get('description')).strip()}")
    return "\n".join(line for line in lines if line)


def _first_mk_copywriting(mk_detail: dict | None) -> dict | None:
    texts = (mk_detail or {}).get("texts") or []
    if not isinstance(texts, list):
        return None
    for text in texts:
        if not isinstance(text, dict):
            continue
        body = _mk_text_body(text)
        if body:
            return {"body": body}
    return None


def _first_non_empty(*values) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _extract_cn_product_name_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    name = os.path.basename(filename.replace("\\", "/"))
    parts = name.split("-")
    if len(parts) < 2:
        return None
    candidate = parts[1].strip()
    if any("\u4e00" <= char <= "\u9fff" for char in candidate):
        return candidate
    return None


def _build_create_product_payload(
    meta: dict,
    translator_id: int | None,
    product_asset: dict | None = None,
    mk_detail: dict | None = None,
) -> dict:
    product_asset = product_asset or {}
    mk_detail = mk_detail or {}
    product_code = _product_code_with_rjc(meta.get("product_code") or product_asset.get("product_code"))
    name = _first_non_empty(
        _extract_cn_product_name_from_filename(meta.get("filename")),
        meta.get("product_name"),
        mk_detail.get("product_name"),
        product_asset.get("product_cn_name"),
        product_asset.get("product_name"),
    ) or ""
    product_link = _first_non_empty(meta.get("product_link"), product_asset.get("product_url"))
    main_image = _first_non_empty(
        meta.get("main_image"),
        product_asset.get("product_main_image_url"),
        mk_detail.get("main_image"),
        mk_detail.get("image"),
    )
    shopify_title = _first_non_empty(
        meta.get("product_english_title"),
        product_asset.get("product_english_title"),
        product_asset.get("product_name"),
    )
    return {
        "name": name[:255],
        "product_code": product_code,
        "product_link": _canonical_product_link(product_link, product_code),
        "main_image": main_image,
        "mk_id": meta.get("mk_id") or mk_detail.get("id"),
        "shopify_title": shopify_title,
    }


def _existing_product_link(meta: dict, existing: dict | None) -> str:
    existing = existing or {}
    product_code = _product_code_with_rjc(existing.get("product_code") or meta.get("product_code"))
    return _canonical_product_link(existing.get("product_link") or meta.get("product_link"), product_code)


def import_mk_video(
    *,
    mk_video_metadata: dict,
    translator_id: int | None,
    actor_user_id: int,
    task_id: int | None = None,
) -> dict:
    """入库一条明空视频。

    Returns:
        {
            "media_item_id": int,
            "media_product_id": int,
            "is_new_product": bool,
            "duration_ms": int,
        }

    Raises:
        DuplicateError  — filename 已存在
        DownloadError   — MP4 下载失败
        StorageError    — 写盘 / DB insert 失败
        DBError         — product insert 失败
    """
    started = time.monotonic()
    step_results: dict[str, list[dict]] = {}
    meta = mk_video_metadata
    filename = meta.get("filename")
    if not filename:
        raise StorageError("filename missing in mk_video_metadata")

    # 1. Dedup by filename
    if _is_video_already_imported(filename):
        raise DuplicateError(f"video filename already imported: {filename}")

    # 2. Find or create product
    raw_code = meta.get("product_code") or ""
    normalized = _normalize_product_code(raw_code)
    existing = _find_existing_product_from_meta(meta, normalized)
    is_new = existing is None
    product_asset = _find_product_asset(normalized) if is_new else None
    mk_detail = _fetch_mk_product_detail(meta.get("mk_id")) if is_new else {}
    payload = (
        _build_create_product_payload(
            meta,
            translator_id,
            product_asset=product_asset,
            mk_detail=mk_detail,
        )
        if is_new
        else {}
    )
    product_link = payload.get("product_link") if is_new else _existing_product_link(meta, existing)
    warnings = []
    product_link_warning = _product_link_warning(product_link)
    if product_link_warning:
        warnings.append(product_link_warning)

    if is_new:
        if translator_id is None:
            raise ValueError("product_owner_id required for new product")
        try:
            product_id = execute(
                "INSERT INTO media_products "
                "(user_id, name, product_code, product_link, main_image, mk_id, shopify_title) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    int(translator_id),
                    payload["name"],
                    payload["product_code"],
                    payload.get("product_link"),
                    payload.get("main_image"),
                    payload.get("mk_id"),
                    payload.get("shopify_title"),
                ),
            )
        except Exception as e:
            if _is_product_code_duplicate_error(e):
                existing = _find_active_product_by_exact_code(payload.get("product_code"))
                if existing:
                    product_id = existing["id"]
                    is_new = False
                    log.warning(
                        "mk import product_code duplicate; reuse product_id=%s product_code=%s",
                        product_id,
                        payload.get("product_code"),
                    )
                else:
                    raise DBError(f"create product failed: {e}") from e
            else:
                raise DBError(f"create product failed: {e}") from e
    else:
        product_id = existing["id"]

    owner_uid = int(translator_id) if is_new else int(existing["user_id"])
    _append_step_result(
        step_results,
        "product",
        key="product_lookup",
        title="产品记录",
        status="done",
        message=f"新建产品：产品 #{product_id}" if is_new else f"复用已有产品：产品 #{product_id}",
        logs=[
            f"product_code={payload.get('product_code') if is_new else existing.get('product_code')}",
            f"owner_user_id={owner_uid}",
        ],
    )
    if product_link_warning:
        _append_step_result(
            step_results,
            "product",
            key="product_link_probe",
            title="商品链接探测",
            status="warning",
            message="商品链接可能不可访问",
            logs=[product_link_warning.get("detail") or "unavailable"],
        )
    else:
        _append_step_result(
            step_results,
            "product",
            key="product_link_probe",
            title="商品链接探测",
            status="done",
            message="商品链接探测通过",
            logs=[product_link] if product_link else None,
        )

    # 3. Download MP4 to local temp
    tmp_dir = tempfile.mkdtemp(prefix="mki_")
    mp4_dest = os.path.join(tmp_dir, filename)
    try:
        downloaded_size = _download_mp4(meta["mp4_url"], mp4_dest, timeout=120)
        _append_step_result(
            step_results,
            "download",
            key="download_mp4",
            title="原视频下载",
            status="done",
            message=f"已下载 {downloaded_size} 字节",
            logs=[str(meta.get("mp4_url") or "")],
        )
        object_key = object_keys.build_media_object_key(owner_uid, int(product_id), filename)
        file_size = _write_file_to_media_store(mp4_dest, object_key)
        _append_step_result(
            step_results,
            "store",
            key="store_media",
            title="媒体文件写入",
            status="done",
            message=f"已写入媒体存储：{file_size} 字节",
            logs=[object_key],
        )
    except DownloadError:
        if is_new:
            try:
                execute("DELETE FROM media_products WHERE id=%s", (product_id,))
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        if is_new:
            try:
                execute("DELETE FROM media_products WHERE id=%s", (product_id,))
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise StorageError(f"store media file failed: {e}") from e

    # 4. Optional item cover
    cover_object_key = _import_image_object(
        owner_uid=owner_uid,
        product_id=int(product_id),
        stem=f"item_cover_{Path(filename).stem}",
        source_url=meta.get("cover_url"),
        source_object_key=meta.get("cover_object_key"),
        tmp_dir=tmp_dir,
        default_ext=".jpg",
    )
    if (meta.get("cover_object_key") or meta.get("cover_url")) and not cover_object_key:
        _append_step_result(
            step_results,
            "store",
            key="item_cover",
            title="视频封面",
            status="warning",
            message="视频封面写入素材库失败",
            logs=["cover source unavailable"],
        )
        warnings.append({
            "type": "item_cover_import_failed",
            "message": "视频封面写入素材库失败",
            "detail": "cover source unavailable",
        })

    elif cover_object_key:
        _append_step_result(
            step_results,
            "store",
            key="item_cover",
            title="视频封面",
            status="done",
            message="视频封面已写入素材库",
            logs=[cover_object_key],
        )

    # 5. Insert media_items row via medias.create_item
    try:
        item_id = _medias_create_item(
            product_id=int(product_id),
            user_id=owner_uid,
            filename=filename,
            object_key=object_key,
            display_name=filename,
            duration_seconds=meta.get("duration_seconds"),
            file_size=file_size,
            cover_object_key=cover_object_key,
            lang="en",
            task_id=task_id,
            skip_push=1,  # 默认不推送英文原始素材，避免干扰待推送列表
        )
        _append_step_result(
            step_results,
            "store",
            key="media_item",
            title="素材记录",
            status="done",
            message=f"素材 ID：{item_id}",
            logs=[f"filename={filename}", "lang=en"],
        )
    except Exception as e:
        try:
            local_media_storage.delete(object_key)
        except Exception:
            pass
        if is_new:
            try:
                execute("DELETE FROM media_products WHERE id=%s", (product_id,))
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise StorageError(f"insert media_item failed: {e}") from e

    video_path = _mk_proxy_media_path(
        meta.get("video_path") or meta.get("mk_video_path") or meta.get("mp4_url"),
        _MK_VIDEO_PROXY_PATHS,
    )
    cover_path = _mk_proxy_media_path(
        meta.get("cover_path") or meta.get("mk_video_image_path") or meta.get("cover_url"),
        _MK_MEDIA_PROXY_PATHS,
    )
    if video_path:
        try:
            _bind_imported_mk_material(
                media_item_id=int(item_id),
                mk_product_id=meta.get("mk_product_id") or meta.get("mk_id") or payload.get("mk_id"),
                mk_product_name=meta.get("mk_product_name") or meta.get("product_name") or payload.get("name"),
                mk_video_path=video_path,
                mk_video_name=filename,
                mk_video_image_path=cover_path or None,
                mk_video_metadata=_mk_binding_metadata(
                    meta,
                    product_link=meta.get("product_link") or product_link,
                    video_path=video_path,
                    cover_path=cover_path,
                ),
                bound_by=int(actor_user_id),
            )
        except Exception as exc:
            log.warning("mk import binding failed item_id=%s video_path=%s: %s", item_id, video_path, exc)
            _append_step_result(
                step_results,
                "store",
                key="mk_binding",
                title="明空素材绑定",
                status="warning",
                message="明空素材绑定信息写入失败",
                logs=[str(exc)],
            )
            warnings.append({
                "type": "mk_material_binding_failed",
                "message": "明空素材绑定信息写入失败",
                "detail": str(exc),
            })

        else:
            _append_step_result(
                step_results,
                "store",
                key="mk_binding",
                title="明空素材绑定",
                status="done",
                message="已记录明空素材来源",
                logs=[video_path],
            )

    try:
        os.unlink(mp4_dest)
    except FileNotFoundError:
        pass

    # 7. New-product enrichment: product cover and first English copywriting
    if is_new:
        product_cover_source_url = _first_non_empty(
            meta.get("main_image"),
            (product_asset or {}).get("product_main_image_url"),
            mk_detail.get("main_image"),
            mk_detail.get("image"),
        )
        product_cover_source_key = _first_non_empty(
            meta.get("main_image_object_key"),
            (product_asset or {}).get("product_main_image_object_key"),
            _local_media_object_key_from_url(product_cover_source_url),
        )
        product_cover_key = _import_image_object(
            owner_uid=owner_uid,
            product_id=int(product_id),
            stem="product_cover_en",
            source_url=product_cover_source_url,
            source_object_key=product_cover_source_key,
            tmp_dir=tmp_dir,
            default_ext=".jpg",
        )
        if product_cover_key:
            try:
                _medias_set_product_cover(int(product_id), "en", product_cover_key)
                _append_step_result(
                    step_results,
                    "store",
                    key="new_product_enrichment",
                    title="新品资料补充",
                    status="done",
                    message="商品主图已写入英文素材资料",
                    logs=[product_cover_key],
                )
            except Exception as exc:
                _append_step_result(
                    step_results,
                    "store",
                    key="new_product_enrichment",
                    title="新品资料补充",
                    status="warning",
                    message="商品主图写入素材库失败",
                    logs=[str(exc)],
                )
                warnings.append({
                    "type": "product_cover_import_failed",
                    "message": "商品主图写入素材库失败",
                    "detail": str(exc),
                })
        elif product_cover_source_url or product_cover_source_key:
            _append_step_result(
                step_results,
                "store",
                key="new_product_enrichment",
                title="新品资料补充",
                status="warning",
                message="商品主图写入素材库失败",
                logs=["cover source unavailable"],
            )
            warnings.append({
                "type": "product_cover_import_failed",
                "message": "商品主图写入素材库失败",
                "detail": "cover source unavailable",
            })

        copy_item = _first_mk_copywriting(mk_detail)
        if copy_item:
            try:
                _medias_replace_copywritings(int(product_id), [copy_item], lang="en")
                _append_step_result(
                    step_results,
                    "store",
                    key="new_product_enrichment",
                    title="新品资料补充",
                    status="done",
                    message="英文文案已写入素材资料",
                    logs=[str(copy_item.get("title") or copy_item.get("content") or "copywriting")],
                )
            except Exception as exc:
                _append_step_result(
                    step_results,
                    "store",
                    key="new_product_enrichment",
                    title="新品资料补充",
                    status="warning",
                    message="英文文案写入素材库失败",
                    logs=[str(exc)],
                )
                warnings.append({
                    "type": "mk_copywriting_import_failed",
                    "message": "英文文案写入素材库失败",
                    "detail": str(exc),
                })

    shutil.rmtree(tmp_dir, ignore_errors=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    result = {
        "media_item_id": int(item_id),
        "media_product_id": int(product_id),
        "is_new_product": is_new,
        "duration_ms": duration_ms,
        "step_results": step_results,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def find_existing_product_item_by_meta(mk_video_metadata: dict) -> dict | None:
    """Given mk metadata, return {product_id, item_id} if an English item exists."""
    raw_code = mk_video_metadata.get("product_code") or ""
    normalized = _normalize_product_code(raw_code)
    existing = _find_existing_product_from_meta(mk_video_metadata, normalized)
    if not existing:
        return None
    warnings = []
    product_link_warning = _product_link_warning(_existing_product_link(mk_video_metadata, existing))
    if product_link_warning:
        warnings.append(product_link_warning)
    item = query_one(
        "SELECT id FROM media_items "
        "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (existing["id"],),
    )
    if not item:
        return None
    result = {"product_id": existing["id"], "item_id": item["id"]}
    if warnings:
        result["warnings"] = warnings
    return result
