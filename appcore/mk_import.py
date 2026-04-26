"""明空选品 → 素材库 自动入库 service。

详见 docs/superpowers/specs/2026-04-26-mk-import-design.md
"""
from __future__ import annotations

import logging
import re
from typing import Any

import requests

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


def _normalize_product_code(code: str | None) -> str:
    """Strip -RJC suffix (case-insensitive) and lowercase. Empty/None → ''."""
    if not code:
        return ""
    return _RJC_SUFFIX_RE.sub("", code.strip()).lower()


from appcore.db import query_one


def _find_existing_product(normalized_code: str) -> dict | None:
    """Find media_product whose product_code, after stripping -RJC, equals normalized_code.

    Note: media_products.product_code is already stored lowercase per existing
    _validate_product_code logic, but may or may not have -RJC suffix.
    """
    if not normalized_code:
        return None
    return query_one(
        "SELECT * FROM media_products "
        "WHERE deleted_at IS NULL "
        "AND LOWER(REGEXP_REPLACE(COALESCE(product_code, ''), '-rjc$', '')) = %s "
        "LIMIT 1",
        (normalized_code,),
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


def _download_mp4(url: str, dest_path: str, timeout: int = 120) -> int:
    """Stream MP4 to dest_path. Returns bytes written. Raises DownloadError."""
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
            return total
    except requests.RequestException as e:
        raise DownloadError(f"download failed: {e}") from e


def _download_cover(url: str | None, dest_path: str, timeout: int = 30) -> str | None:
    """Download cover image. Returns dest_path if downloaded, None if no URL provided."""
    if not url:
        return None
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=16 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest_path
    except requests.RequestException as e:
        log.warning("cover download failed url=%s: %s", url, e)
        return None  # cover 下载失败不阻塞，只记日志


# ---- main entry ----

import os
import shutil
import tempfile
import time

from appcore.db import execute
from appcore.medias import create_item as _medias_create_item


def _build_create_product_payload(meta: dict, translator_id: int) -> dict:
    return {
        "name": (meta.get("product_name") or "").strip()[:255],
        "product_code": _normalize_product_code(meta.get("product_code")),
        "product_link": meta.get("product_link"),
        "main_image": meta.get("main_image"),
        "mk_id": meta.get("mk_id"),
    }


def import_mk_video(
    *,
    mk_video_metadata: dict,
    translator_id: int,
    actor_user_id: int,
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
    existing = _find_existing_product(normalized)
    is_new = existing is None

    if is_new:
        payload = _build_create_product_payload(meta, translator_id)
        try:
            product_id = execute(
                "INSERT INTO media_products "
                "(user_id, name, product_code, product_link, main_image, mk_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    int(translator_id),
                    payload["name"],
                    payload["product_code"],
                    payload.get("product_link"),
                    payload.get("main_image"),
                    payload.get("mk_id"),
                ),
            )
        except Exception as e:
            raise DBError(f"create product failed: {e}") from e
    else:
        product_id = existing["id"]

    # 3. Download MP4 to local temp
    tmp_dir = tempfile.mkdtemp(prefix="mki_")
    mp4_dest = os.path.join(tmp_dir, filename)
    try:
        _download_mp4(meta["mp4_url"], mp4_dest, timeout=120)
    except DownloadError:
        if is_new:
            try:
                execute("DELETE FROM media_products WHERE id=%s", (product_id,))
            except Exception:
                pass
        raise

    # 4. Optional cover
    cover_dest = os.path.join(tmp_dir, f"cover_{filename}.jpg")
    _download_cover(meta.get("cover_url"), cover_dest)

    # 5. Insert media_items row via medias.create_item
    object_key = f"mk-import/{int(product_id)}/{filename}"
    owner_uid = int(translator_id) if is_new else int(existing["user_id"])
    try:
        item_id = _medias_create_item(
            product_id=int(product_id),
            user_id=owner_uid,
            filename=filename,
            object_key=object_key,
            display_name=(meta.get("product_name") or "")[:255] or None,
            duration_seconds=meta.get("duration_seconds"),
            lang="en",
        )
    except Exception as e:
        raise StorageError(f"insert media_item failed: {e}") from e

    # 6. Move local mp4 to final storage location (mirrors object_key)
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    final_path = os.path.join(upload_dir, object_key)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    try:
        shutil.move(mp4_dest, final_path)
    except Exception as e:
        log.error("move mp4 failed: %s → %s: %s", mp4_dest, final_path, e)

    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "media_item_id": int(item_id),
        "media_product_id": int(product_id),
        "is_new_product": is_new,
        "duration_ms": duration_ms,
    }
