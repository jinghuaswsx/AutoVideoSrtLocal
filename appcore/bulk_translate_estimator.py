"""bulk_translate 费用与资源预估。

精度目标: ±20% 以内,给二次确认弹窗做心理预期。不做精确计费。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 5 章
"""
from __future__ import annotations

from appcore.db import query, query_one
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS

# ---------- 单价(本期硬编码,后续可迁 system_settings)----------
COST_PER_1K_TOKENS_CNY = 0.60
COST_PER_IMAGE_CNY = 0.18
COST_PER_VIDEO_MINUTE_CNY = 0.95

# ---------- 估算系数 ----------
CHARS_TO_TOKENS = 1.3              # 英文 1 字符 ≈ 1.3 tokens
TRANSLATION_EXPANSION = 1.5        # 英→德/法 文字膨胀


# ============================================================
# 公共入口
# ============================================================
def estimate(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
) -> dict:
    """返回 {copy_tokens, image_count, video_minutes, skipped, estimated_cost_cny, breakdown}"""
    skipped = {"copy": 0, "cover": 0, "detail": 0, "video": 0}

    copy_tokens = 0
    if "copy" in content_types:
        copy_tokens = _estimate_copy(product_id, target_langs,
                                       force_retranslate, skipped)

    image_count = 0
    if "detail" in content_types:
        image_count += _estimate_images(
            "media_product_detail_images", product_id,
            target_langs, force_retranslate, skipped, key="detail",
        )
    if "cover" in content_types:
        image_count += _estimate_images(
            "media_product_covers", product_id,
            target_langs, force_retranslate, skipped, key="cover",
        )

    video_minutes = 0.0
    if "video" in content_types:
        video_minutes = _estimate_video(product_id, target_langs,
                                          force_retranslate, skipped)

    copy_cny = (copy_tokens / 1000.0) * COST_PER_1K_TOKENS_CNY
    image_cny = image_count * COST_PER_IMAGE_CNY
    video_cny = video_minutes * COST_PER_VIDEO_MINUTE_CNY
    total = round(copy_cny + image_cny + video_cny, 2)

    return {
        "copy_tokens": int(copy_tokens),
        "image_count": int(image_count),
        "video_minutes": round(video_minutes, 2),
        "skipped": skipped,
        "estimated_cost_cny": total,
        "breakdown": {
            "copy_cny": round(copy_cny, 2),
            "image_cny": round(image_cny, 2),
            "video_cny": round(video_cny, 2),
        },
    }


# ============================================================
# 内部估算分块
# ============================================================
_COPY_TEXT_FIELDS = (
    "title", "body", "description",
    "ad_carrier", "ad_copy", "ad_keywords",
)

# 四张素材表里,只有这两张带 deleted_at 列(软删)。
# copywritings 和 covers 是硬删表,SQL 不能引用 deleted_at。
_SOFT_DELETE_TABLES = {"media_items", "media_product_detail_images"}


def _del_clause(table: str) -> str:
    return " AND deleted_at IS NULL" if table in _SOFT_DELETE_TABLES else ""


def _estimate_copy(product_id, target_langs, force, skipped):
    """文案:一条英文源 × 一个目标语言 = 一个预估单元。"""
    rows = query(
        "SELECT " + ", ".join(["id"] + [
            f"COALESCE(CHAR_LENGTH({f}), 0) AS len_{f}"
            for f in _COPY_TEXT_FIELDS
        ]) + " FROM media_copywritings "
        "WHERE product_id = %s AND lang = 'en'",
        (product_id,),
    )
    if not rows:
        return 0

    tokens = 0.0
    for r in rows:
        char_len = sum(r[f"len_{f}"] for f in _COPY_TEXT_FIELDS)
        for lang in target_langs:
            if not force and _translation_exists_copy(product_id, lang, r["id"]):
                skipped["copy"] += 1
                continue
            tokens += char_len * CHARS_TO_TOKENS * TRANSLATION_EXPANSION
    return tokens


def _translation_exists_copy(product_id, lang, source_ref_id):
    row = query_one(
        "SELECT 1 AS x FROM media_copywritings "
        "WHERE product_id = %s AND lang = %s AND source_ref_id = %s LIMIT 1",
        (product_id, lang, source_ref_id),
    )
    return row is not None


def _estimate_images(table, product_id, target_langs, force, skipped, key):
    """图片:一张英文 × 一个目标语言 = 一张。"""
    rows = query(
        f"SELECT id FROM {table} "
        f"WHERE product_id = %s AND lang = 'en'{_del_clause(table)}",
        (product_id,),
    )
    if not rows:
        return 0

    count = 0
    for r in rows:
        src_id = r["id"]
        for lang in target_langs:
            if not force and _translation_exists(table, product_id, lang, src_id):
                skipped[key] += 1
                continue
            count += 1
    return count


def _translation_exists(table, product_id, lang, source_ref_id):
    row = query_one(
        f"SELECT 1 AS x FROM {table} "
        f"WHERE product_id = %s AND lang = %s AND source_ref_id = %s"
        f"{_del_clause(table)} LIMIT 1",
        (product_id, lang, source_ref_id),
    )
    return row is not None


def _estimate_video(product_id, target_langs, force, skipped):
    """视频:每个英文视频 × de/fr 目标 = 一次;其他语言静默跳过。"""
    rows = query(
        "SELECT id, duration_seconds FROM media_items "
        "WHERE product_id = %s AND lang = 'en' AND deleted_at IS NULL",
        (product_id,),
    )
    if not rows:
        return 0.0

    minutes = 0.0
    for r in rows:
        src_id = r["id"]
        dur_sec = r["duration_seconds"] or 0
        dur_min = dur_sec / 60.0
        for lang in target_langs:
            if lang not in VIDEO_SUPPORTED_LANGS:
                skipped["video"] += 1
                continue
            if not force and _translation_exists(
                "media_items", product_id, lang, src_id,
            ):
                skipped["video"] += 1
                continue
            minutes += dur_min
    return minutes
