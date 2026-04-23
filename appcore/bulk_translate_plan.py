"""bulk_translate 计划生成器。

同时兼容两套 content_types：
- 旧版：copy / detail / cover / video
- 新版：copywriting / detail_images / video_covers / videos

新旧两套都会输出统一的 item schema，便于父任务调度器做状态编排。
"""
from __future__ import annotations

from appcore import medias
from appcore.db import query
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS


COPYWRITING_LANG_DISPATCH_SECONDS = 3
DETAIL_IMAGES_LANG_DISPATCH_SECONDS = 10
VIDEOS_DISPATCH_SECONDS = 5


def generate_plan(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
    raw_source_ids: list[int] | None = None,
) -> list[dict]:
    del user_id
    plan: list[dict] = []
    idx_counter = _Counter()

    copy_kind = _pick_kind(content_types, "copywriting", "copy")
    if copy_kind:
        copy_dispatch_offsets = _lang_dispatch_offsets(
            target_langs,
            COPYWRITING_LANG_DISPATCH_SECONDS,
        )
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
                        dispatch_after_seconds=copy_dispatch_offsets.get(lang, 0),
                    )
                )

    detail_kind = _pick_kind(content_types, "detail_images", "detail")
    if detail_kind:
        detail_ids = _list_en_detail_ids(product_id)
        if detail_ids:
            for offset, lang in enumerate(target_langs):
                dispatch_after_seconds = (
                    DETAIL_IMAGES_LANG_DISPATCH_SECONDS * offset
                    if detail_kind == "detail_images"
                    else 0
                )
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
                existing_cover_pairs = (
                    set()
                    if force_retranslate
                    else _list_existing_source_lang_pairs(
                        "media_raw_source_translations",
                        "source_ref_id",
                        product_id,
                        raw_ids,
                        target_langs,
                    )
                )
                for lang in target_langs:
                    pending_raw_ids = [
                        raw_id for raw_id in raw_ids
                        if (raw_id, lang) not in existing_cover_pairs
                    ]
                    if not pending_raw_ids:
                        continue
                    plan.append(
                        _new_item(
                            idx_counter.next(),
                            cover_kind,
                            lang,
                            {"source_raw_ids": pending_raw_ids},
                        )
                    )

    video_kind = _pick_kind(content_types, "videos", "video")
    if video_kind:
        raw_rows = _list_requested_raw_source_rows(product_id, raw_source_ids)
        raw_ids = [int(row["id"]) for row in raw_rows]
        existing_video_pairs = (
            set()
            if force_retranslate or video_kind != "videos"
            else _list_existing_source_lang_pairs(
                "media_items",
                "source_raw_id",
                product_id,
                raw_ids,
                target_langs,
            )
        )
        dispatch_index = 0
        for row in raw_rows:
            raw_id = int(row["id"])
            for lang in target_langs:
                if video_kind == "video" and lang not in VIDEO_SUPPORTED_LANGS:
                    continue
                if (raw_id, lang) in existing_video_pairs:
                    continue
                plan.append(
                    _new_item(
                        idx_counter.next(),
                        video_kind,
                        lang,
                        {"source_raw_id": raw_id},
                        dispatch_after_seconds=VIDEOS_DISPATCH_SECONDS * dispatch_index if video_kind == "videos" else 0,
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


def _lang_dispatch_offsets(target_langs: list[str], spacing_seconds: int) -> dict[str, int]:
    return {
        lang: spacing_seconds * idx
        for idx, lang in enumerate(target_langs)
    }


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


def _list_en_detail_ids(product_id: int) -> list[int]:
    rows = query(
        "SELECT id, object_key, content_type "
        "FROM media_product_detail_images "
        "WHERE product_id = %s AND lang = 'en' AND deleted_at IS NULL "
        "ORDER BY id ASC",
        (product_id,),
    )
    return [
        int(row["id"])
        for row in rows
        if int(row.get("id") or 0) and not medias.detail_image_is_gif(row)
    ]


def _list_en_ids(product_id: int, table: str) -> list[int]:
    where_del = " AND deleted_at IS NULL" if table in _SOFT_DELETE_TABLES else ""
    rows = query(
        f"SELECT id FROM {table} "
        f"WHERE product_id = %s AND lang = 'en'{where_del} "
        f"ORDER BY id ASC",
        (product_id,),
    )
    return [int(row["id"]) for row in rows]


def _list_existing_source_lang_pairs(
    table: str,
    source_column: str,
    product_id: int,
    source_ids: list[int],
    target_langs: list[str],
) -> set[tuple[int, str]]:
    if not source_ids or not target_langs:
        return set()
    source_placeholders = ",".join(["%s"] * len(source_ids))
    lang_placeholders = ",".join(["%s"] * len(target_langs))
    del_clause = " AND deleted_at IS NULL" if table in {"media_items", "media_raw_source_translations"} else ""
    rows = query(
        f"SELECT {source_column}, lang FROM {table} "
        f"WHERE product_id = %s "
        f"  AND {source_column} IN ({source_placeholders}) "
        f"  AND lang IN ({lang_placeholders})"
        f"{del_clause}",
        (product_id, *source_ids, *target_langs),
    )
    result: set[tuple[int, str]] = set()
    for row in rows:
        raw_id = int(row.get(source_column) or 0)
        lang = str(row.get("lang") or "").strip()
        if raw_id and lang:
            result.add((raw_id, lang))
    return result


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
