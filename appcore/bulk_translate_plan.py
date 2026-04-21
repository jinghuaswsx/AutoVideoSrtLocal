"""bulk_translate 父任务 · plan 生成器。

根据产品 + 目标语言 + 内容类型,把"要做什么事情"展开成一个有序的 plan 项列表,
每个 plan 项对应父任务调度器将要派发的一个子任务。

plan 项结构:
    {
      "idx": int,
      "kind": "copy" | "cover" | "detail" | "video",
      "lang": str,
      "ref": dict,            # 定位源素材(每种 kind 自己定义)
      "sub_task_id": str|None, # 调度器派发后写入
      "status": "pending",    # pending/running/done/error/skipped
      "error": str|None,
      "started_at": str|None,
      "finished_at": str|None,
    }

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 2.1 节
"""
from __future__ import annotations

from appcore.db import query
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS


def generate_plan(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
    raw_source_ids: list[int] | None = None,
) -> list[dict]:
    """生成 plan 项列表。

    展开规则:
      - copy   : 每条英文 × 每目标语言 = 一个 plan 项
      - detail : 每目标语言 1 个(batch,下挂所有英文详情图 id)
      - cover  : 每目标语言 1 个(batch,下挂所有英文主图 id)
      - video  : 每个英文视频 × 每(de|fr)目标 = 一个 plan 项
                  非 de/fr 语言不生成 plan 项(不是 skipped,是"不规划")
    """
    plan: list[dict] = []
    idx_counter = _Counter()

    # 1. 文案(media_copywritings 无 deleted_at,不加软删过滤)
    if "copy" in content_types:
        copy_rows = query(
            "SELECT id FROM media_copywritings "
            "WHERE product_id = %s AND lang = 'en' "
            "ORDER BY idx ASC, id ASC",
            (product_id,),
        )
        for row in copy_rows:
            for lang in target_langs:
                plan.append(_new_item(
                    idx_counter.next(), "copy", lang,
                    {"source_copy_id": row["id"]},
                ))

    # 2. 详情图(按语种 batch)
    if "detail" in content_types:
        detail_ids = _list_en_ids(product_id, "media_product_detail_images")
        if detail_ids:
            for lang in target_langs:
                plan.append(_new_item(
                    idx_counter.next(), "detail", lang,
                    {"source_detail_ids": detail_ids},
                ))

    # 3. 主图(按语种 batch)
    if "cover" in content_types:
        cover_ids = _list_en_ids(product_id, "media_product_covers")
        if cover_ids:
            for lang in target_langs:
                plan.append(_new_item(
                    idx_counter.next(), "cover", lang,
                    {"source_cover_ids": cover_ids},
                ))

    # 4. 视频
    if "video" in content_types:
        if not raw_source_ids:
            raise ValueError("video kind requires non-empty raw_source_ids")
        placeholders = ",".join(["%s"] * len(raw_source_ids))
        video_rows = query(
            f"SELECT id FROM media_raw_sources "
            f"WHERE id IN ({placeholders}) "
            f"  AND product_id = %s AND deleted_at IS NULL "
            f"ORDER BY sort_order ASC, id ASC",
            (*raw_source_ids, product_id),
        )
        found_ids = {int(r["id"]) for r in video_rows}
        missing = [rid for rid in raw_source_ids if int(rid) not in found_ids]
        if missing:
            raise ValueError(f"raw_source_ids not found or soft-deleted: {missing}")
        for row in video_rows:
            for lang in target_langs:
                if lang not in VIDEO_SUPPORTED_LANGS:
                    continue   # 不支持的目标语言直接不规划
                plan.append(_new_item(
                    idx_counter.next(), "video", lang,
                    {"source_raw_id": row["id"]},
                ))

    return plan


# ------------------------------------------------------------
# 内部工具
# ------------------------------------------------------------
class _Counter:
    def __init__(self):
        self.n = 0

    def next(self):
        v = self.n
        self.n += 1
        return v


def _new_item(idx: int, kind: str, lang: str, ref: dict) -> dict:
    return {
        "idx": idx,
        "kind": kind,
        "lang": lang,
        "ref": ref,
        "sub_task_id": None,
        "status": "pending",
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


_SOFT_DELETE_TABLES = {"media_items", "media_product_detail_images"}


def _list_en_ids(product_id: int, table: str) -> list[int]:
    where_del = " AND deleted_at IS NULL" if table in _SOFT_DELETE_TABLES else ""
    rows = query(
        f"SELECT id FROM {table} "
        f"WHERE product_id = %s AND lang = 'en'{where_del} "
        f"ORDER BY id ASC",
        (product_id,),
    )
    return [r["id"] for r in rows]
