"""把"自动翻译"关联关系写入四张素材表的辅助函数。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 2.3 节

四张表结构一致(都有 source_ref_id / bulk_task_id / auto_translated /
manually_edited_at 四列),用白名单限制表名防 SQL 注入。
"""
from __future__ import annotations

from appcore.db import execute

_ALLOWED_TABLES = {
    "media_copywritings",
    "media_product_detail_images",
    "media_items",
    "media_product_covers",
    "media_raw_source_translations",
}


def _check_table(table: str) -> None:
    if table not in _ALLOWED_TABLES:
        raise ValueError(
            f"Unsupported table for bulk-translate associations: {table}. "
            f"Allowed: {sorted(_ALLOWED_TABLES)}"
        )


def mark_auto_translated(
    table: str,
    target_id: int,
    source_ref_id: int,
    bulk_task_id: str | None,
) -> int:
    """把 target_id 这条素材标记为"由 source_ref_id 自动翻译生成"。

    返回受影响行数(预期 1)。
    """
    _check_table(table)
    return execute(
        f"""
        UPDATE {table}
           SET source_ref_id   = %s,
               bulk_task_id    = %s,
               auto_translated = 1
         WHERE id = %s
        """,
        (source_ref_id, bulk_task_id, target_id),
    )


def mark_manually_edited(table: str, target_id: int) -> int:
    """用户手工编辑了自动翻译结果,打上"已人工修改"时间戳。

    不清除 auto_translated / source_ref_id(UI 需同时展示"英文译本 · ✏️ 已人工修改")。
    返回受影响行数。
    """
    _check_table(table)
    return execute(
        f"UPDATE {table} SET manually_edited_at = NOW() WHERE id = %s",
        (target_id,),
    )
