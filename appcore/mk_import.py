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
