"""bulk_translate 计划生成器。

同时兼容两套 content_types：
- 旧版：copy / detail / cover / video
- 新版：copywriting / detail_images / video_covers / videos

新旧两套都会输出统一的 item schema，便于父任务调度器做状态编排。
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
    del user_id, force_retranslate
    plan: list[dict] = []
    idx_counter = _Counter()

    copy_kind = _pick_kind(content_types, "copywriting", "copy")
    if copy_kind:
        copy_rows = query(
            "SELECT id FROM media_copywritings "
            "WHERE product_id = %s AND lang = 'en' "
            "ORDER BY idx ASC, id ASC",
            (product_id,),
        )
        for row in copy_rows:
            for lang in target_langs:
                plan.append(
                    _new_item(
                        idx_counter.next(),
                        copy_kind,
                        lang,
                        {"source_copy_id": row["id"]},
                    )
                )

    detail_kind = _pick_kind(content_types, "detail_images", "detail")
    if detail_kind:
        detail_ids = _list_en_ids(product_id, "media_product_detail_images")
        if detail_ids:
            for offset, lang in enumerate(target_langs):
                dispatch_after_seconds = 30 * offset if detail_kind == "detail_images" else 0
                plan.append(
                    _new_item(
                        idx_counter.next(),
                        detail_kind,
                        lang,
                        {"source_detail_ids": detail_ids},
                        dispatch_after_seconds=dispatch_after_seconds,
                    )
                )

    cover_kind = _pick_kind(content_types, "video_covers", "cover")
    if cover_kind:
        if cover_kind == "cover":
            cover_ids = _list_en_ids(product_id, "media_product_covers")
            if cover_ids:
                for lang in target_langs:
                    plan.append(
                        _new_item(
                            idx_counter.next(),
                            cover_kind,
                            lang,
                            {"source_cover_ids": cover_ids},
                        )
                    )
        else:
            raw_rows = _list_requested_raw_source_rows(product_id, raw_source_ids)
            raw_ids = [int(row["id"]) for row in raw_rows]
            if raw_ids:
                for lang in target_langs:
                    plan.append(
                        _new_item(
                            idx_counter.next(),
                            cover_kind,
                            lang,
                            {"source_raw_ids": raw_ids},
                        )
                    )

    video_kind = _pick_kind(content_types, "videos", "video")
    if video_kind:
        raw_rows = _list_requested_raw_source_rows(product_id, raw_source_ids)
        dispatch_index = 0
        for row in raw_rows:
            for lang in target_langs:
                if video_kind == "video" and lang not in VIDEO_SUPPORTED_LANGS:
                    continue
                plan.append(
                    _new_item(
                        idx_counter.next(),
                        video_kind,
                        lang,
                        {"source_raw_id": row["id"]},
                        dispatch_after_seconds=120 * dispatch_index if video_kind == "videos" else 0,
                    )
                )
                if video_kind == "videos":
                    dispatch_index += 1

    return plan


class _Counter:
    def __init__(self):
        self.n = 0

    def next(self) -> int:
        current = self.n
        self.n += 1
        return current


def _pick_kind(content_types: list[str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in content_types:
            return candidate
    return None


def _new_item(
    idx: int,
    kind: str,
    lang: str,
    ref: dict,
    *,
    dispatch_after_seconds: int = 0,
) -> dict:
    return {
        "idx": idx,
        "kind": kind,
        "lang": lang,
        "ref": ref,
        "sub_task_id": None,
        "child_task_id": None,
        "child_task_type": None,
        "status": "pending",
        "dispatch_after_seconds": dispatch_after_seconds,
        "result_synced": False,
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
    return [int(row["id"]) for row in rows]


def _list_requested_raw_source_rows(
    product_id: int,
    raw_source_ids: list[int] | None,
) -> list[dict]:
    if not raw_source_ids:
        raise ValueError("raw_source_ids not found or empty")
    placeholders = ",".join(["%s"] * len(raw_source_ids))
    rows = query(
        f"SELECT id FROM media_raw_sources "
        f"WHERE id IN ({placeholders}) "
        f"  AND product_id = %s AND deleted_at IS NULL "
        f"ORDER BY sort_order ASC, id ASC",
        (*raw_source_ids, product_id),
    )
    found_ids = {int(row["id"]) for row in rows}
    missing = [int(raw_id) for raw_id in raw_source_ids if int(raw_id) not in found_ids]
    if missing:
        raise ValueError(f"raw_source_ids not found or soft-deleted: {missing}")
    return rows
