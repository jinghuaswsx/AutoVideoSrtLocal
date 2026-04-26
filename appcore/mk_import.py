"""明空选品 → 素材库 自动入库 service。

详见 docs/superpowers/specs/2026-04-26-mk-import-design.md
"""
from __future__ import annotations

import logging
import re
from typing import Any

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
