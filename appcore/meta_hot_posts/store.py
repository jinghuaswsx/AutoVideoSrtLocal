from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Mapping

from appcore.db import execute, query
from appcore.meta_hot_posts.category_route import CATEGORY_MODEL, CATEGORY_PROVIDER


QueryFn = Callable[[str, tuple[Any, ...]], list[dict]]
ExecuteFn = Callable[[str, tuple[Any, ...]], Any]

LOCAL_VIDEO_MAX_ATTEMPTS = 5
LOCAL_VIDEO_RETRY_AFTER_HOURS = 12


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def product_url_hash(product_url: str) -> str:
    return hashlib.sha256(str(product_url or "").strip().encode("utf-8")).hexdigest()


def _int_arg(args: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(args.get(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _float_arg(args: Mapping[str, Any], name: str) -> float | None:
    raw = args.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _text_arg(args: Mapping[str, Any], name: str) -> str | None:
    value = str(args.get(name) or "").strip()
    return value or None


def _date_arg(args: Mapping[str, Any], name: str) -> str | None:
    value = _text_arg(args, name)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _mark_status_arg(args: Mapping[str, Any]) -> str | None:
    value = str(args.get("mark_status") or "").strip().lower()
    if value in {"empty", "blank", "none", "unmarked", "空"}:
        return "empty"
    if value in {"ok", "pass", "yes", "行"}:
        return "ok"
    if value in {"bad", "fail", "no", "不行"}:
        return "bad"
    return None


def list_hot_posts(args: Mapping[str, Any], *, query_fn: QueryFn = query) -> dict[str, Any]:
    page = _int_arg(args, "page", 1, 1, 10000)
    page_size = _int_arg(args, "page_size", 30, 10, 100)
    offset = (page - 1) * page_size
    where: list[str] = []
    params: list[Any] = []

    category = _text_arg(args, "category")
    if category:
        where.append("a.category_l1 = %s")
        params.append(category)

    min_price = _float_arg(args, "min_price")
    if min_price is not None:
        where.append("a.price_min >= %s")
        params.append(min_price)

    max_price = _float_arg(args, "max_price")
    if max_price is not None:
        where.append("a.price_min <= %s")
        params.append(max_price)

    min_interactions = _int_arg(args, "min_interactions", 0, 0, 10**12)
    if min_interactions:
        where.append("p.latest_likes >= %s")
        params.append(min_interactions)

    min_comments = _int_arg(args, "min_comments", 0, 0, 10**12)
    if min_comments:
        where.append("p.latest_comments >= %s")
        params.append(min_comments)

    mark_status = _mark_status_arg(args)
    if mark_status == "empty":
        where.append("((p.mark_status IS NULL OR p.mark_status = '') AND COALESCE(p.is_marked, 0) = 0)")
    elif mark_status:
        where.append("p.mark_status = %s")
        params.append(mark_status)

    created_from = _date_arg(args, "created_from")
    if created_from:
        where.append("p.creation_time >= %s")
        params.append(created_from)

    created_to = _date_arg(args, "created_to")
    if created_to:
        where.append("p.creation_time < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(created_to)

    keyword = _text_arg(args, "q")
    if keyword:
        where.append(
            "(p.message_html LIKE %s OR p.message_zh_html LIKE %s "
            "OR a.product_title LIKE %s OR p.product_url LIKE %s)"
        )
        like = f"%{keyword}%"
        params.extend([like, like, like, like])

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    count_rows = query_fn(
        f"""
        SELECT COUNT(*) AS cnt
        FROM meta_hot_posts p
        LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash
        {where_sql}
        """,
        list(params),
    )
    rows = query_fn(
        f"""
        SELECT p.id, p.wedev_post_id, p.page_id, p.post_id, p.bm_page_id,
               p.post_url, p.ad_library_url, p.product_url, p.creation_time,
               p.first_seen_at, p.last_synced_at, p.likes, p.comments, p.shares,
               p.latest_likes, p.latest_comments, p.latest_shares,
               p.sync_period_likes, p.sync_period_hours, p.copycat,
               p.is_marked, p.mark_status, p.marked_at, p.marked_by,
               p.video_url, p.image_url, p.invisible, p.invisible_region,
               p.message_html, p.message_zh_html, p.message_zh_status,
               p.message_zh_attempts, p.message_zh_error, p.message_zh_translated_at,
               p.raw_json,
               p.local_video_path, p.local_video_duration_seconds, p.local_video_cover_path,
               p.local_video_status, p.local_video_error,
               p.local_video_downloaded_at, p.local_video_attempts,
               a.status AS analysis_status,
               a.product_title, a.product_main_image_url, a.price_min,
               a.price_max, a.currency, a.sku_prices_json,
               a.category_l1, a.category_confidence, a.category_reason,
               a.last_error, a.analyzed_at
        FROM meta_hot_posts p
        LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash
        {where_sql}
        ORDER BY COALESCE(p.sync_period_likes, 0) DESC, p.creation_time DESC, p.id DESC
        LIMIT %s OFFSET %s
        """,
        list(params + [page_size, offset]),
    )
    return {
        "items": rows,
        "total": int(count_rows[0]["cnt"] if count_rows else 0),
        "page": page,
        "page_size": page_size,
    }


def list_today_new_hot_posts(
    args: Mapping[str, Any] | None = None,
    *,
    query_fn: QueryFn = query,
) -> dict[str, Any]:
    args = args or {}
    page = _int_arg(args, "page", 1, 1, 10000)
    page_size = _int_arg(args, "page_size", 50, 10, 100)
    offset = (page - 1) * page_size
    where_sql = """
        WHERE p.first_seen_at >= CURDATE()
          AND p.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
    """
    count_rows = query_fn(
        f"""
        SELECT COUNT(*) AS cnt
        FROM meta_hot_posts p
        LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash
        {where_sql}
        """,
        [],
    )
    rows = query_fn(
        f"""
        SELECT p.id, p.wedev_post_id, p.page_id, p.post_id, p.bm_page_id,
               p.post_url, p.ad_library_url, p.product_url, p.creation_time,
               p.first_seen_at, p.last_synced_at, p.likes, p.comments, p.shares,
               p.latest_likes, p.latest_comments, p.latest_shares,
               p.sync_period_likes, p.sync_period_hours, p.copycat,
               p.is_marked, p.mark_status, p.marked_at, p.marked_by,
               p.video_url, p.image_url, p.invisible, p.invisible_region,
               p.message_html, p.message_zh_html, p.message_zh_status,
               p.message_zh_attempts, p.message_zh_error, p.message_zh_translated_at,
               p.raw_json,
               p.local_video_path, p.local_video_duration_seconds, p.local_video_cover_path,
               p.local_video_status, p.local_video_error,
               p.local_video_downloaded_at, p.local_video_attempts,
               a.status AS analysis_status,
               a.product_title, a.product_main_image_url, a.price_min,
               a.price_max, a.currency, a.sku_prices_json,
               a.category_l1, a.category_confidence, a.category_reason,
               a.last_error, a.analyzed_at
        FROM meta_hot_posts p
        LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash
        {where_sql}
        ORDER BY p.first_seen_at DESC, COALESCE(p.sync_period_likes, 0) DESC, p.id DESC
        LIMIT %s OFFSET %s
        """,
        [page_size, offset],
    )
    return {
        "items": rows,
        "total": int(count_rows[0]["cnt"] if count_rows else 0),
        "page": page,
        "page_size": page_size,
    }


def upsert_hot_post(row: Mapping[str, Any], *, execute_fn: ExecuteFn = execute) -> int:
    product_url = str(row.get("product_url") or "").strip()
    url_hash = str(row.get("product_url_hash") or "").strip() or (product_url_hash(product_url) if product_url else None)
    params = (
        row.get("wedev_post_id"),
        row.get("page_id"),
        row.get("post_id"),
        row.get("bm_page_id"),
        row.get("post_url"),
        row.get("ad_library_url"),
        product_url,
        url_hash,
        row.get("creation_time"),
        row.get("last_synced_at"),
        row.get("likes"),
        row.get("comments"),
        row.get("shares"),
        row.get("latest_likes"),
        row.get("latest_comments"),
        row.get("latest_shares"),
        row.get("sync_period_likes"),
        row.get("sync_period_hours"),
        1 if row.get("copycat") else 0,
        _json(row.get("select_json")),
        row.get("video_url"),
        row.get("image_url"),
        1 if row.get("invisible") else 0,
        row.get("invisible_region"),
        row.get("message_html"),
        _json(row.get("raw_json")),
    )
    return execute_fn(
        """
        INSERT INTO meta_hot_posts (
          wedev_post_id, page_id, post_id, bm_page_id, post_url, ad_library_url,
          product_url, product_url_hash, creation_time, last_synced_at,
          likes, comments, shares, latest_likes, latest_comments, latest_shares,
          sync_period_likes, sync_period_hours, copycat, select_json, video_url,
          image_url, invisible, invisible_region, message_html,
          message_zh_html, message_zh_status, local_video_status, raw_json
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, 'pending', 'pending', %s
        )
        ON DUPLICATE KEY UPDATE
          page_id=VALUES(page_id),
          post_id=VALUES(post_id),
          bm_page_id=VALUES(bm_page_id),
          post_url=VALUES(post_url),
          ad_library_url=VALUES(ad_library_url),
          product_url=VALUES(product_url),
          product_url_hash=VALUES(product_url_hash),
          creation_time=VALUES(creation_time),
          last_synced_at=VALUES(last_synced_at),
          likes=VALUES(likes),
          comments=VALUES(comments),
          shares=VALUES(shares),
          latest_likes=VALUES(latest_likes),
          latest_comments=VALUES(latest_comments),
          latest_shares=VALUES(latest_shares),
          sync_period_likes=VALUES(sync_period_likes),
          sync_period_hours=VALUES(sync_period_hours),
          copycat=VALUES(copycat),
          select_json=VALUES(select_json),
          local_video_path=CASE WHEN VALUES(video_url) <=> video_url THEN local_video_path ELSE NULL END,
          local_video_duration_seconds=CASE WHEN VALUES(video_url) <=> video_url THEN local_video_duration_seconds ELSE NULL END,
          local_video_cover_path=CASE WHEN VALUES(video_url) <=> video_url THEN local_video_cover_path ELSE NULL END,
          local_video_status=CASE WHEN VALUES(video_url) <=> video_url THEN local_video_status ELSE 'pending' END,
          local_video_error=CASE WHEN VALUES(video_url) <=> video_url THEN local_video_error ELSE NULL END,
          local_video_downloaded_at=CASE WHEN VALUES(video_url) <=> video_url THEN local_video_downloaded_at ELSE NULL END,
          video_url=VALUES(video_url),
          image_url=VALUES(image_url),
          invisible=VALUES(invisible),
          invisible_region=VALUES(invisible_region),
          message_zh_html=CASE WHEN VALUES(message_html) <=> message_html THEN message_zh_html ELSE NULL END,
          message_zh_status=CASE WHEN VALUES(message_html) <=> message_html THEN message_zh_status ELSE 'pending' END,
          message_zh_attempts=CASE WHEN VALUES(message_html) <=> message_html THEN message_zh_attempts ELSE 0 END,
          message_zh_error=CASE WHEN VALUES(message_html) <=> message_html THEN message_zh_error ELSE NULL END,
          message_zh_translated_at=CASE WHEN VALUES(message_html) <=> message_html THEN message_zh_translated_at ELSE NULL END,
          message_html=VALUES(message_html),
          raw_json=VALUES(raw_json)
        """,
        params,
    )


def set_hot_post_mark_status(
    post_id: int,
    *,
    mark_status: str | None,
    user_id: int | None = None,
    execute_fn: ExecuteFn = execute,
) -> int:
    status_value = str(mark_status or "").strip() or None
    mark_value = 1 if status_value else 0
    actor_id = int(user_id) if user_id else None
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET mark_status=%s,
            is_marked=%s,
            marked_at=CASE WHEN %s = 1 THEN NOW() ELSE NULL END,
            marked_by=CASE WHEN %s = 1 THEN %s ELSE NULL END
        WHERE id=%s
        """,
        (status_value, mark_value, mark_value, mark_value, actor_id, int(post_id)),
    )


def set_hot_post_marked(
    post_id: int,
    *,
    marked: bool,
    user_id: int | None = None,
    execute_fn: ExecuteFn = execute,
) -> int:
    return set_hot_post_mark_status(
        post_id,
        mark_status="bad" if marked else None,
        user_id=user_id,
        execute_fn=execute_fn,
    )


def next_pending_message_translations(
    *,
    limit: int = 50,
    max_attempts: int = 3,
    query_fn: QueryFn = query,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))
    return query_fn(
        """
        SELECT id, message_html
        FROM meta_hot_posts
        WHERE message_html IS NOT NULL
          AND TRIM(message_html) <> ''
          AND (
            message_zh_html IS NULL
            OR message_zh_html = ''
            OR message_zh_status IN ('pending', 'failed')
          )
          AND message_zh_attempts < %s
        ORDER BY updated_at ASC, id ASC
        LIMIT %s
        """,
        (int(max_attempts), safe_limit),
    )


def mark_message_translation_running(
    post_id: int,
    *,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET message_zh_status='running',
            message_zh_attempts=message_zh_attempts + 1,
            message_zh_error=NULL
        WHERE id=%s
        """,
        (int(post_id),),
    )


def finish_message_translation(
    post_id: int,
    *,
    translated_html: str | None,
    error_message: str | None,
    execute_fn: ExecuteFn = execute,
) -> int:
    if error_message:
        return execute_fn(
            """
            UPDATE meta_hot_posts
            SET message_zh_status='failed',
                message_zh_error=%s
            WHERE id=%s
            """,
            (str(error_message)[:1000], int(post_id)),
        )
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET message_zh_html=%s,
            message_zh_status='done',
            message_zh_error=NULL,
            message_zh_translated_at=NOW()
        WHERE id=%s
        """,
        (translated_html or "", int(post_id)),
    )


def reset_stale_running_message_translations(
    *,
    older_than_seconds: int = 3600,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET message_zh_status='failed',
            message_zh_error='message translation stale running reset'
        WHERE message_zh_status='running'
          AND TIMESTAMPDIFF(SECOND, updated_at, NOW()) >= %s
        """,
        (int(older_than_seconds),),
    )


def next_pending_local_videos(
    *,
    limit: int = 20,
    max_attempts: int = LOCAL_VIDEO_MAX_ATTEMPTS,
    retry_after_hours: int = LOCAL_VIDEO_RETRY_AFTER_HOURS,
    query_fn: QueryFn = query,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))
    safe_max_attempts = max(1, int(max_attempts))
    safe_retry_hours = max(1, int(retry_after_hours))
    return query_fn(
        """
        SELECT id, wedev_post_id, page_id, post_id, video_url,
               local_video_path, local_video_status, local_video_attempts
        FROM meta_hot_posts
        WHERE video_url IS NOT NULL
          AND TRIM(video_url) <> ''
          AND (local_video_status IS NULL OR local_video_status = '' OR local_video_status IN ('pending', 'failed'))
          AND (local_video_status IS NULL OR local_video_status <> 'downloaded')
          AND (local_video_status IS NULL OR local_video_status <> 'unavailable')
          AND local_video_attempts < %s
          AND (
            local_video_status IS NULL
            OR local_video_status <> 'failed'
            OR TIMESTAMPDIFF(HOUR, updated_at, NOW()) >= %s
          )
        ORDER BY COALESCE(sync_period_likes, 0) DESC, creation_time DESC, id ASC
        LIMIT %s
        """,
        (safe_max_attempts, safe_retry_hours, safe_limit),
    )


def mark_local_video_downloading(
    post_id: int,
    *,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET local_video_status='downloading',
            local_video_attempts=local_video_attempts + 1,
            local_video_error=NULL
        WHERE id=%s
        """,
        (int(post_id),),
    )


def finish_local_video_download(
    post_id: int,
    *,
    local_video_path: str | None,
    local_video_duration_seconds: float | int | None = None,
    local_video_cover_path: str | None = None,
    error_message: str | None,
    max_attempts: int = LOCAL_VIDEO_MAX_ATTEMPTS,
    execute_fn: ExecuteFn = execute,
) -> int:
    if error_message:
        message = str(error_message)[:1000]
        return execute_fn(
            """
            UPDATE meta_hot_posts
            SET local_video_status=CASE WHEN local_video_attempts >= %s THEN 'unavailable' ELSE 'failed' END,
                local_video_error=CASE
                  WHEN local_video_attempts >= %s THEN CONCAT('unavailable after max retry attempts: ', %s)
                  ELSE %s
                END
            WHERE id=%s
            """,
            (int(max_attempts), int(max_attempts), message, message, int(post_id)),
        )
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET local_video_path=%s,
            local_video_duration_seconds=%s,
            local_video_cover_path=%s,
            local_video_status='downloaded',
            local_video_error=NULL,
            local_video_downloaded_at=NOW()
        WHERE id=%s
        """,
        (
            local_video_path or "",
            _score(local_video_duration_seconds),
            local_video_cover_path or "",
            int(post_id),
        ),
    )


def get_hot_post_local_video(
    post_id: int,
    *,
    query_fn: QueryFn = query,
) -> dict[str, Any] | None:
    rows = query_fn(
        """
        SELECT id, local_video_path, local_video_duration_seconds, local_video_cover_path,
               local_video_status, local_video_error,
               local_video_downloaded_at
        FROM meta_hot_posts
        WHERE id=%s
        """,
        (int(post_id),),
    )
    return rows[0] if rows else None


def list_local_videos_missing_metadata(
    *,
    limit: int | None = 100,
    query_fn: QueryFn = query,
) -> list[dict[str, Any]]:
    sql = """
        SELECT id, local_video_path, local_video_duration_seconds, local_video_cover_path
        FROM meta_hot_posts
        WHERE local_video_status = 'downloaded'
          AND local_video_path IS NOT NULL
          AND TRIM(local_video_path) <> ''
          AND (
            local_video_duration_seconds IS NULL
            OR local_video_duration_seconds <= 0
            OR local_video_cover_path IS NULL
            OR TRIM(local_video_cover_path) = ''
          )
        ORDER BY local_video_downloaded_at DESC, id DESC
        """
    if limit is None or int(limit or 0) <= 0:
        return query_fn(sql, ())
    safe_limit = max(1, min(1000, int(limit)))
    return query_fn(sql + " LIMIT %s", (safe_limit,))


def update_local_video_metadata(
    post_id: int,
    *,
    local_video_duration_seconds: float | int | None,
    local_video_cover_path: str | None,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET local_video_duration_seconds=%s,
            local_video_cover_path=%s
        WHERE id=%s
        """,
        (_score(local_video_duration_seconds), local_video_cover_path or "", int(post_id)),
    )

def ensure_video_copyability_candidates(*, execute_fn: ExecuteFn = execute) -> int:
    return execute_fn(
        """
        INSERT INTO meta_hot_post_video_copyability_analyses (
          hot_post_id, wedev_post_id, product_url, local_video_path, status
        )
        SELECT p.id, p.wedev_post_id, p.product_url, p.local_video_path, 'pending'
        FROM meta_hot_posts p
        WHERE p.local_video_status = 'downloaded'
          AND p.local_video_path IS NOT NULL
          AND TRIM(p.local_video_path) <> ''
          AND p.product_url IS NOT NULL
          AND TRIM(p.product_url) <> ''
        ON DUPLICATE KEY UPDATE
          wedev_post_id=VALUES(wedev_post_id),
          product_url=VALUES(product_url),
          local_video_path=VALUES(local_video_path)
        """,
        (),
    )


def ensure_europe_fit_candidates(*, execute_fn: ExecuteFn = execute) -> int:
    return execute_fn(
        """
        INSERT INTO meta_hot_post_europe_assessments (post_id, status)
        SELECT p.id, 'pending'
        FROM meta_hot_posts p
        WHERE p.local_video_status = 'downloaded'
          AND p.local_video_path IS NOT NULL
          AND TRIM(p.local_video_path) <> ''
          AND p.product_url IS NOT NULL
          AND TRIM(p.product_url) <> ''
        ON DUPLICATE KEY UPDATE
          post_id=VALUES(post_id)
        """,
        (),
    )


def next_pending_video_copyability_analyses(
    *,
    limit: int = 1,
    max_attempts: int = 3,
    query_fn: QueryFn = query,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))
    return query_fn(
        """
        SELECT va.id AS analysis_id,
               va.hot_post_id,
               va.wedev_post_id,
               va.product_url,
               va.local_video_path,
               va.compressed_video_path,
               va.attempts,
               p.post_url,
               p.ad_library_url,
               p.video_url,
               p.page_id,
               p.post_id,
               p.creation_time,
               p.last_synced_at,
               p.latest_likes,
               p.latest_comments,
               p.latest_shares,
               p.sync_period_likes,
               p.sync_period_hours,
               p.message_html,
               p.message_zh_html,
               pa.product_title,
               pa.product_main_image_url,
               pa.price_min,
               pa.price_max,
               pa.currency,
               pa.category_l1,
               pa.category_confidence,
               pa.category_reason
        FROM meta_hot_post_video_copyability_analyses va
        JOIN meta_hot_posts p ON p.id = va.hot_post_id
        LEFT JOIN meta_hot_post_product_analyses pa ON pa.product_url_hash = p.product_url_hash
        WHERE va.status IN ('pending', 'failed')
          AND va.attempts < %s
          AND p.local_video_status = 'downloaded'
          AND p.local_video_path IS NOT NULL
          AND TRIM(p.local_video_path) <> ''
          AND p.product_url IS NOT NULL
          AND TRIM(p.product_url) <> ''
        ORDER BY va.updated_at ASC,
                 COALESCE(p.sync_period_likes, 0) DESC,
                 p.creation_time DESC,
                 va.id ASC
        LIMIT %s
        """,
        (int(max_attempts), safe_limit),
    )


def mark_video_copyability_running(
    analysis_id: int,
    *,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_post_video_copyability_analyses
        SET status='running',
            attempts=attempts + 1,
            last_error=NULL
        WHERE id=%s
        """,
        (int(analysis_id),),
    )


def finish_video_copyability_analysis(
    analysis_id: int,
    *,
    result: Mapping[str, Any] | None = None,
    error_message: str | None = None,
    status_override: str | None = None,
    execute_fn: ExecuteFn = execute,
) -> int:
    payload = dict(result or {})
    status = status_override or ("failed" if error_message else "done")
    if status not in {"pending", "done", "failed", "suspended"}:
        status = "failed" if error_message else "done"
    return execute_fn(
        """
        UPDATE meta_hot_post_video_copyability_analyses
        SET status=%s,
            last_error=%s,
            overall_score=%s,
            copyability_score=%s,
            meta_us_ad_fit_score=%s,
            product_fit_score=%s,
            compliance_risk_score=%s,
            recommendation=%s,
            summary=%s,
            llm_provider=%s,
            llm_model=%s,
            compressed_video_path=%s,
            analysis_json=%s,
            analyzed_at=CASE WHEN %s = 'done' THEN NOW() ELSE analyzed_at END
        WHERE id=%s
        """,
        (
            status,
            str(error_message)[:1000] if error_message else None,
            _score(payload.get("overall_score")),
            _score(payload.get("copyability_score")),
            _score(payload.get("meta_us_ad_fit_score")),
            _score(payload.get("product_fit_score")),
            _score(payload.get("compliance_risk_score")),
            str(payload.get("recommendation") or "")[:32] or None,
            str(payload.get("summary") or "")[:4000] or None,
            str(payload.get("provider") or "")[:64] or None,
            str(payload.get("model") or "")[:128] or None,
            str(payload.get("compressed_video_path") or "")[:2048] or None,
            _json(payload),
            status,
            int(analysis_id),
        ),
    )


def reset_stale_running_video_copyability_analyses(
    *,
    older_than_seconds: int = 3600,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_post_video_copyability_analyses
        SET status='failed',
            last_error='video copyability analysis stale running reset'
        WHERE status='running'
          AND TIMESTAMPDIFF(SECOND, updated_at, NOW()) >= %s
        """,
        (int(older_than_seconds),),
    )


def reset_running_video_copyability_analyses(*, execute_fn: ExecuteFn = execute) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_post_video_copyability_analyses
        SET status='pending',
            last_error='video analysis queue superseded by a new run'
        WHERE status='running'
        """,
        (),
    )


def list_top_video_copyability_analyses(
    *,
    limit: int = 50,
    query_fn: QueryFn = query,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(50, int(limit)))
    return query_fn(
        """
        SELECT va.id AS analysis_id,
               va.hot_post_id AS id,
               va.hot_post_id,
               va.wedev_post_id,
               va.product_url,
               va.local_video_path,
               va.compressed_video_path,
               va.overall_score,
               va.copyability_score,
               va.meta_us_ad_fit_score,
               va.product_fit_score,
               va.compliance_risk_score,
               va.recommendation,
               va.summary,
               va.llm_provider,
               va.llm_model,
               va.analysis_json,
               va.analyzed_at,
               p.post_url,
               p.ad_library_url,
               p.video_url,
               p.image_url,
               p.page_id,
               p.post_id,
               p.creation_time,
               p.last_synced_at,
               p.latest_likes,
               p.latest_comments,
               p.latest_shares,
               p.sync_period_likes,
               p.sync_period_hours,
               p.copycat,
               p.local_video_duration_seconds,
               p.local_video_cover_path,
               p.local_video_status,
               p.message_html,
               p.message_zh_html,
               p.raw_json,
               pa.status AS analysis_status,
               pa.product_title,
               pa.product_main_image_url,
               pa.price_min,
               pa.price_max,
               pa.currency,
               pa.sku_prices_json,
               pa.category_l1,
               pa.category_confidence,
               pa.category_reason
        FROM meta_hot_post_video_copyability_analyses va
        JOIN meta_hot_posts p ON p.id = va.hot_post_id
        LEFT JOIN meta_hot_post_product_analyses pa ON pa.product_url_hash = p.product_url_hash
        WHERE va.status = 'done'
        ORDER BY va.overall_score DESC,
                 va.copyability_score DESC,
                 va.meta_us_ad_fit_score DESC,
                 va.analyzed_at DESC,
                 va.id DESC
        LIMIT %s
        """,
        (safe_limit,),
    )


def next_pending_europe_fit_materials(
    *,
    limit: int = 30,
    max_attempts: int = 3,
    query_fn: QueryFn = query,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))
    return query_fn(
        """
        SELECT p.id, p.wedev_post_id, p.page_id, p.post_id,
               p.product_url, p.creation_time, p.likes, p.comments, p.shares,
               p.latest_likes, p.latest_comments, p.latest_shares,
               p.sync_period_likes, p.sync_period_hours,
               p.video_url, p.local_video_path, p.local_video_status,
               p.message_html,
               a.product_title, a.product_main_image_url, a.price_min,
               a.price_max, a.currency, a.sku_prices_json,
               a.category_l1, a.category_confidence, a.category_reason,
               e.status AS europe_fit_status, e.attempts AS europe_fit_attempts
        FROM meta_hot_posts p
        LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash
        LEFT JOIN meta_hot_post_europe_assessments e ON e.post_id = p.id
        WHERE p.product_url IS NOT NULL
          AND TRIM(p.product_url) <> ''
          AND p.local_video_path IS NOT NULL
          AND TRIM(p.local_video_path) <> ''
          AND p.local_video_status = 'downloaded'
          AND (e.id IS NULL OR e.status IN ('pending', 'failed'))
          AND COALESCE(e.attempts, 0) < %s
        ORDER BY COALESCE(p.sync_period_likes, 0) DESC, p.creation_time DESC, p.id ASC
        LIMIT %s
        """,
        (int(max_attempts), safe_limit),
    )


def mark_europe_fit_running(
    post_id: int,
    *,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        INSERT INTO meta_hot_post_europe_assessments (post_id, status, attempts, last_error)
        VALUES (%s, 'running', 1, NULL)
        ON DUPLICATE KEY UPDATE
          status='running',
          attempts=attempts + 1,
          last_error=NULL
        """,
        (int(post_id),),
    )


def finish_europe_fit_assessment(
    post_id: int,
    *,
    status: str,
    result: Mapping[str, Any] | None = None,
    video_optimization: Mapping[str, Any] | None = None,
    error_message: str | None = None,
    execute_fn: ExecuteFn = execute,
) -> int:
    result = result or {}
    return execute_fn(
        """
        UPDATE meta_hot_post_europe_assessments
        SET status=%s,
            last_error=%s,
            suitability_score=%s,
            recommendation=%s,
            direct_reuse=%s,
            best_countries_json=%s,
            country_scores_json=%s,
            strengths_json=%s,
            risks_json=%s,
            required_changes_json=%s,
            reasoning=%s,
            llm_provider=%s,
            llm_model=%s,
            llm_response_json=%s,
            video_optimization_json=%s,
            assessed_at=CASE WHEN %s = 'done' THEN NOW() ELSE assessed_at END
        WHERE post_id=%s
        """,
        (
            status,
            error_message,
            result.get("suitability_score"),
            result.get("recommendation"),
            1 if result.get("direct_reuse") else 0,
            _json(result.get("best_countries") or []),
            _json(result.get("country_scores") or {}),
            _json(result.get("strengths") or []),
            _json(result.get("risks") or []),
            _json(result.get("required_changes") or []),
            result.get("reasoning"),
            result.get("provider"),
            result.get("model"),
            _json(result.get("raw_response") or result),
            _json(video_optimization or result.get("video_optimization") or {}),
            status,
            int(post_id),
        ),
    )


def reset_running_europe_fit_assessments(*, execute_fn: ExecuteFn = execute) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_post_europe_assessments
        SET status='pending',
            last_error='Europe fit assessment superseded by a new run'
        WHERE status='running'
        """,
        (),
    )


def list_top_europe_fit_materials(
    *,
    limit: int = 50,
    query_fn: QueryFn = query,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(50, int(limit)))
    return query_fn(
        """
        SELECT p.id, p.wedev_post_id, p.page_id, p.post_id, p.bm_page_id,
               p.post_url, p.ad_library_url, p.product_url, p.creation_time,
               p.last_synced_at, p.likes, p.comments, p.shares,
               p.latest_likes, p.latest_comments, p.latest_shares,
               p.sync_period_likes, p.sync_period_hours, p.copycat,
               p.is_marked, p.mark_status, p.marked_at, p.marked_by,
               p.video_url, p.image_url, p.invisible, p.invisible_region,
               p.message_html, p.message_zh_html, p.message_zh_status,
               p.raw_json,
               p.local_video_path, p.local_video_duration_seconds, p.local_video_cover_path,
               p.local_video_status, p.local_video_error,
               p.local_video_downloaded_at, p.local_video_attempts,
               a.status AS analysis_status,
               a.product_title, a.product_main_image_url, a.price_min,
               a.price_max, a.currency, a.sku_prices_json,
               a.category_l1, a.category_confidence, a.category_reason,
               a.last_error, a.analyzed_at,
               e.status AS europe_fit_status,
               e.suitability_score AS europe_fit_score,
               e.recommendation AS europe_fit_recommendation,
               e.direct_reuse AS europe_fit_direct_reuse,
               e.best_countries_json AS europe_fit_best_countries_json,
               e.country_scores_json AS europe_fit_country_scores_json,
               e.strengths_json AS europe_fit_strengths_json,
               e.risks_json AS europe_fit_risks_json,
               e.required_changes_json AS europe_fit_required_changes_json,
               e.reasoning AS europe_fit_reasoning,
               e.llm_provider AS europe_fit_provider,
               e.llm_model AS europe_fit_model,
               e.video_optimization_json AS europe_fit_video_optimization_json,
               e.assessed_at AS europe_fit_assessed_at
        FROM meta_hot_post_europe_assessments e
        JOIN meta_hot_posts p ON p.id = e.post_id
        LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash
        WHERE e.status = 'done'
        ORDER BY e.suitability_score DESC,
                 COALESCE(p.sync_period_likes, 0) DESC,
                 e.assessed_at DESC,
                 p.id DESC
        LIMIT %s
        """,
        (safe_limit,),
    )


def reset_stale_running_local_videos(
    *,
    older_than_seconds: int = 7200,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET local_video_status='failed',
            local_video_error='local video download stale running reset'
        WHERE local_video_status='downloading'
          AND TIMESTAMPDIFF(SECOND, updated_at, NOW()) >= %s
        """,
        (int(older_than_seconds),),
    )


def reset_running_local_videos(*, execute_fn: ExecuteFn = execute) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_posts
        SET local_video_status='failed',
            local_video_error='local video download superseded by a new run'
        WHERE local_video_status='downloading'
        """,
        (),
    )


def ensure_product_analysis(product_url: str, *, execute_fn: ExecuteFn = execute) -> int:
    url = str(product_url or "").strip()
    if not url:
        return 0
    return execute_fn(
        """
        INSERT INTO meta_hot_post_product_analyses (product_url, product_url_hash, status)
        VALUES (%s, %s, 'pending')
        ON DUPLICATE KEY UPDATE product_url = VALUES(product_url)
        """,
        (url, product_url_hash(url)),
    )


def next_pending_product_analyses(
    *,
    limit: int = 5,
    max_attempts: int = 3,
    query_fn: QueryFn = query,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))
    return query_fn(
        """
        SELECT id, product_url, product_url_hash, attempts
        FROM meta_hot_post_product_analyses
        WHERE status IN ('pending', 'failed')
          AND attempts < %s
        ORDER BY updated_at ASC, id ASC
        LIMIT %s
        """,
        (int(max_attempts), safe_limit),
    )


def next_category_reanalysis_candidates(
    *,
    limit: int = 100,
    include_all: bool = False,
    query_fn: QueryFn = query,
) -> list[dict]:
    safe_limit = max(1, min(100, int(limit)))
    not_current_route = "(COALESCE(llm_provider, '') <> %s OR COALESCE(llm_model, '') <> %s)"
    category_failed_pattern = "category failed:%"
    category_params: list[Any] = [CATEGORY_PROVIDER, CATEGORY_MODEL]
    if include_all:
        category_clause = not_current_route
    else:
        category_clause = (
            f"({not_current_route} AND "
            "(last_error LIKE %s "
            "OR (category_l1 = 'Other' AND COALESCE(category_confidence, 0) = 0) "
            "OR category_l1 IS NULL "
            "OR category_l1 = ''))"
        )
        category_params.append(category_failed_pattern)
    return query_fn(
        f"""
        SELECT id, product_url, product_title, category_l1, last_error
        FROM meta_hot_post_product_analyses
        WHERE status = 'done'
          AND product_title IS NOT NULL
          AND product_title <> ''
          AND {category_clause}
        ORDER BY
          CASE WHEN last_error LIKE %s THEN 0 ELSE 1 END,
          updated_at ASC,
          id ASC
        LIMIT %s
        """,
        tuple(category_params + [category_failed_pattern, safe_limit]),
    )


def list_failed_product_analyses(
    *,
    limit: int = 100,
    query_fn: QueryFn = query,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(100, int(limit)))
    return query_fn(
        """
        SELECT id, product_url, attempts, last_error,
               product_title, product_main_image_url, price_min, price_max,
               currency, category_l1, analyzed_at, updated_at
        FROM meta_hot_post_product_analyses
        WHERE status = 'failed'
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        (safe_limit,),
    )


def reset_stale_running_product_analyses(
    *,
    older_than_seconds: int = 3600,
    execute_fn: ExecuteFn = execute,
) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_post_product_analyses
        SET status='failed',
            last_error='stale running analysis exceeded timeout and was reset'
        WHERE status='running'
          AND TIMESTAMPDIFF(SECOND, updated_at, NOW()) >= %s
        """,
        (int(older_than_seconds),),
    )


def mark_analysis_running(analysis_id: int, *, execute_fn: ExecuteFn = execute) -> int:
    return execute_fn(
        """
        UPDATE meta_hot_post_product_analyses
        SET status='running', attempts=attempts+1, last_error=NULL
        WHERE id=%s
        """,
        (int(analysis_id),),
    )


def finish_analysis(
    analysis_id: int,
    *,
    status: str,
    result: Mapping[str, Any] | None = None,
    category: Mapping[str, Any] | None = None,
    error_message: str | None = None,
    execute_fn: ExecuteFn = execute,
) -> int:
    result = result or {}
    category = category or {}
    return execute_fn(
        """
        UPDATE meta_hot_post_product_analyses
        SET status=%s,
            last_error=%s,
            product_title=%s,
            product_main_image_url=%s,
            price_min=%s,
            price_max=%s,
            currency=%s,
            sku_prices_json=%s,
            category_l1=%s,
            category_confidence=%s,
            category_reason=%s,
            llm_provider=%s,
            llm_model=%s,
            llm_response_json=%s,
            extracted_json=%s,
            analyzed_at=CASE WHEN %s = 'done' THEN NOW() ELSE analyzed_at END
        WHERE id=%s
        """,
        (
            status,
            error_message,
            result.get("title"),
            result.get("main_image_url"),
            result.get("price_min"),
            result.get("price_max"),
            result.get("currency"),
            _json(result.get("skus") or []),
            category.get("category"),
            category.get("confidence"),
            category.get("reason"),
            category.get("provider"),
            category.get("model"),
            _json(category.get("raw_response") or category),
            _json(result),
            status,
            int(analysis_id),
        ),
    )


def finish_category_reanalysis(
    analysis_id: int,
    *,
    category: Mapping[str, Any] | None = None,
    error_message: str | None = None,
    execute_fn: ExecuteFn = execute,
) -> int:
    category = category or {}
    return execute_fn(
        """
        UPDATE meta_hot_post_product_analyses
        SET last_error=%s,
            category_l1=%s,
            category_confidence=%s,
            category_reason=%s,
            llm_provider=%s,
            llm_model=%s,
            llm_response_json=%s,
            analyzed_at=NOW()
        WHERE id=%s
        """,
        (
            error_message,
            category.get("category"),
            category.get("confidence"),
            category.get("reason"),
            category.get("provider"),
            category.get("model"),
            _json(category.get("raw_response") or category),
            int(analysis_id),
        ),
    )


def list_category_options(*, query_fn: QueryFn = query) -> list[dict[str, Any]]:
    rows = query_fn(
        """
        SELECT category_l1 AS value,
               category_l1 AS label,
               COUNT(*) AS count
        FROM meta_hot_post_product_analyses
        WHERE category_l1 IS NOT NULL AND category_l1 <> ''
        GROUP BY category_l1
        ORDER BY category_l1 ASC
        """,
        (),
    )
    return list(rows)
