from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Mapping

from appcore.db import execute, query
from appcore.meta_hot_posts.category_route import CATEGORY_MODEL, CATEGORY_PROVIDER


QueryFn = Callable[[str, tuple[Any, ...]], list[dict]]
ExecuteFn = Callable[[str, tuple[Any, ...]], Any]


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


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
               p.last_synced_at, p.likes, p.comments, p.shares,
               p.latest_likes, p.latest_comments, p.latest_shares,
               p.sync_period_likes, p.sync_period_hours, p.copycat,
               p.is_marked, p.mark_status, p.marked_at, p.marked_by,
               p.video_url, p.image_url, p.invisible, p.invisible_region,
               p.message_html, p.message_zh_html, p.message_zh_status,
               p.message_zh_attempts, p.message_zh_error, p.message_zh_translated_at,
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
          message_zh_html, message_zh_status, raw_json
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, 'pending', %s
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
