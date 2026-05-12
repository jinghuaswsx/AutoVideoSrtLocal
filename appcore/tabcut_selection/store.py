from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable, Mapping

from appcore.db import execute, query
from appcore.tabcut_selection.categories import goods_category_source


QueryFn = Callable[[str, list[Any]], list[dict]]
ExecuteFn = Callable[[str, list[Any]], Any]

VIDEO_SORTS = {
    "score": "c.score",
    "play_count": "c.play_count",
    "item_sold_count": "c.item_sold_count",
    "video_split_sold_count": "c.video_split_sold_count",
    "video_split_gmv": "c.video_split_gmv",
    "goods_sold_count_7d": "c.goods_sold_count_7d",
    "goods_gmv_7d": "c.goods_gmv_7d",
    "goods_growth_rate_7d": "c.goods_growth_rate_7d",
}

GOODS_SORTS = {
    "sold_count_7d": "COALESCE(s.sold_count_7d, s.sold_count_period)",
    "gmv_7d": "COALESCE(s.gmv_7d, s.gmv_period)",
    "sold_count_total": "s.sold_count_total",
    "gmv_total": "s.gmv_total",
    "sold_growth_rate_7d": "s.sold_growth_rate_7d",
    "related_video_count": "s.related_video_count",
}

SOURCE_RANKS = {
    "1d": ("video_1d_play", "video_1d_sales"),
    "7d": ("video_7d_play", "video_7d_sales"),
    "30d": ("video_30d_play", "video_30d_sales"),
}


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


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


def list_video_candidates(args: Mapping[str, Any], *, query_fn: QueryFn = query) -> dict[str, Any]:
    page = _int_arg(args, "page", 1, 1, 10000)
    page_size = _int_arg(args, "page_size", 50, 10, 200)
    offset = (page - 1) * page_size
    sort_column = VIDEO_SORTS.get(str(args.get("sort") or "play_count"), "c.play_count")
    where = ["c.region = %s"]
    params: list[Any] = [str(args.get("region") or "US")]

    for arg_name, column in [
        ("category_l1", "c.category_l1_name"),
        ("category_l2", "c.category_l2_name"),
        ("category_l3", "c.category_l3_name"),
    ]:
        value = _text_arg(args, arg_name)
        if value:
            where.append(f"{column} = %s")
            params.append(value)

    publish_date_from = _date_arg(args, "publish_date_from")
    if publish_date_from:
        where.append("v.create_time >= %s")
        params.append(publish_date_from)

    publish_date_to = _date_arg(args, "publish_date_to")
    if publish_date_to:
        where.append("v.create_time < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(publish_date_to)

    source_rank_values = SOURCE_RANKS.get(str(args.get("source_rank") or ""))
    if source_rank_values:
        placeholders = ", ".join(["%s"] * len(source_rank_values))
        where.append(
            f"""
            EXISTS (
                SELECT 1
                FROM tabcut_video_snapshots source_vs
                WHERE source_vs.biz_date = c.biz_date
                  AND source_vs.region = c.region
                  AND source_vs.video_id = c.video_id
                  AND source_vs.source_sort IN ({placeholders})
            )
            """
        )
        params.extend(source_rank_values)

    min_video_sales = _int_arg(args, "min_video_sales", 0, 0, 10**12)
    if min_video_sales:
        where.append("c.item_sold_count >= %s")
        params.append(min_video_sales)

    for arg_name, column in [
        ("min_goods_sales_7d", "c.goods_sold_count_7d"),
        ("min_total_sales", "c.goods_sold_count_total"),
    ]:
        value = _int_arg(args, arg_name, 0, 0, 10**12)
        if value:
            where.append(f"{column} >= %s")
            params.append(value)

    for arg_name, column in [
        ("min_goods_gmv_7d", "c.goods_gmv_7d"),
        ("min_video_gmv", "c.video_split_gmv"),
    ]:
        value = _float_arg(args, arg_name)
        if value is not None:
            where.append(f"{column} >= %s")
            params.append(value)

    where_sql = " AND ".join(where)
    count_rows = query_fn(
        f"""
        SELECT COUNT(*) AS cnt
        FROM tabcut_video_candidates c
        LEFT JOIN tabcut_videos v ON v.video_id = c.video_id
        WHERE {where_sql}
        """,
        list(params),
    )
    rows = query_fn(
        f"""
        SELECT c.id, c.biz_date, c.region, c.video_id, c.primary_item_id,
               c.score, c.score_parts_json, COALESCE(vs.play_count, c.play_count) AS play_count,
               vs.like_count, vs.share_count, vs.comment_count,
               c.item_sold_count, c.video_split_sold_count, c.video_split_gmv,
               c.goods_sold_count_7d, c.goods_gmv_7d,
               c.goods_sold_count_total, c.goods_gmv_total, c.goods_growth_rate_7d,
               c.category_l1_name, c.category_l2_name, c.category_l3_name,
               c.candidate_json, c.crawled_at,
               v.video_cover_url, v.tk_video_url, v.video_desc, v.author_name,
               v.author_avatar_url, v.video_duration_ms, v.create_time,
               v.raw_json AS video_raw_json,
               g.item_name AS primary_item_name, g.item_pic_url AS primary_item_pic_url,
               gs.price_min AS primary_item_price_min,
               gs.price_max AS primary_item_price_max,
               COALESCE(gs.sold_count_7d, gs.sold_count_period) AS primary_item_sold_count
        FROM tabcut_video_candidates c
        LEFT JOIN (
            SELECT biz_date, region, video_id,
                   MAX(play_count) AS play_count,
                   MAX(like_count) AS like_count,
                   MAX(share_count) AS share_count,
                   MAX(comment_count) AS comment_count
            FROM tabcut_video_snapshots
            GROUP BY biz_date, region, video_id
        ) vs ON vs.biz_date = c.biz_date
            AND vs.region = c.region
            AND vs.video_id = c.video_id
        LEFT JOIN tabcut_videos v ON v.video_id = c.video_id
        LEFT JOIN tabcut_goods g ON g.item_id = c.primary_item_id
        LEFT JOIN tabcut_goods_snapshots gs
               ON gs.biz_date = c.biz_date
              AND gs.region = c.region
              AND gs.item_id = c.primary_item_id
        WHERE {where_sql}
        ORDER BY {sort_column} DESC, c.video_id ASC
        LIMIT %s OFFSET %s
        """,
        list(params) + [page_size, offset],
    )
    return {
        "items": rows,
        "total": int(count_rows[0]["cnt"] if count_rows else 0),
        "page": page,
        "page_size": page_size,
    }


def list_goods(args: Mapping[str, Any], *, query_fn: QueryFn = query) -> dict[str, Any]:
    page = _int_arg(args, "page", 1, 1, 10000)
    page_size = _int_arg(args, "page_size", 50, 10, 200)
    offset = (page - 1) * page_size
    sort_column = GOODS_SORTS.get(str(args.get("sort") or "sold_count_7d"), "s.sold_count_7d")
    where = ["s.region = %s"]
    params: list[Any] = [str(args.get("region") or "US")]

    biz_date = _date_arg(args, "biz_date")
    if biz_date:
        where.append("s.biz_date = %s")
        params.append(biz_date)

    source_category = goods_category_source(_text_arg(args, "source_category"))
    if source_category:
        where.append("s.source = %s")
        params.append(source_category)

    for arg_name, column in [
        ("category_l1", "g.category_l1_name"),
        ("category_l2", "g.category_l2_name"),
        ("category_l3", "g.category_l3_name"),
    ]:
        value = _text_arg(args, arg_name)
        if value:
            where.append(f"{column} = %s")
            params.append(value)

    min_sales = _int_arg(args, "min_sales_7d", 0, 0, 10**12)
    if min_sales:
        where.append("COALESCE(s.sold_count_7d, s.sold_count_period) >= %s")
        params.append(min_sales)

    min_gmv = _float_arg(args, "min_gmv_7d")
    if min_gmv is not None:
        where.append("COALESCE(s.gmv_7d, s.gmv_period) >= %s")
        params.append(min_gmv)

    where_sql = " AND ".join(where)
    count_rows = query_fn(
        f"""
        SELECT COUNT(*) AS cnt
        FROM tabcut_goods_snapshots s
        JOIN tabcut_goods g ON g.item_id = s.item_id
        WHERE {where_sql}
        """,
        list(params),
    )
    rows = query_fn(
        f"""
        SELECT s.*, g.item_name, g.item_pic_url, g.category_l1_name, g.category_l2_name,
               g.category_l3_name, g.seller_name, g.seller_type
        FROM tabcut_goods_snapshots s
        JOIN tabcut_goods g ON g.item_id = s.item_id
        WHERE {where_sql}
        ORDER BY {sort_column} DESC, s.item_id ASC
        LIMIT %s OFFSET %s
        """,
        list(params) + [page_size, offset],
    )
    return {
        "items": rows,
        "total": int(count_rows[0]["cnt"] if count_rows else 0),
        "page": page,
        "page_size": page_size,
    }


def list_category_options(
    args: Mapping[str, Any] | None = None,
    *,
    query_fn: QueryFn = query,
) -> list[dict[str, Any]]:
    args = args or {}
    region = str(args.get("region") or "US")
    rows = query_fn(
        """
        SELECT category_l1_name AS value,
               category_l1_name AS label,
               SUM(video_count) AS video_count,
               SUM(goods_count) AS goods_count
        FROM (
            SELECT c.category_l1_name,
                   COUNT(DISTINCT c.video_id) AS video_count,
                   0 AS goods_count
            FROM tabcut_video_candidates c
            WHERE c.region = %s
              AND c.category_l1_name IS NOT NULL
              AND c.category_l1_name <> ''
            GROUP BY c.category_l1_name
            UNION ALL
            SELECT g.category_l1_name,
                   0 AS video_count,
                   COUNT(DISTINCT g.item_id) AS goods_count
            FROM tabcut_goods g
            WHERE g.region = %s
              AND g.category_l1_name IS NOT NULL
              AND g.category_l1_name <> ''
            GROUP BY g.category_l1_name
        ) category_sources
        GROUP BY category_l1_name
        ORDER BY category_l1_name ASC
        """,
        [region, region],
    )
    return [
        {
            "value": str(row.get("value") or ""),
            "label": str(row.get("label") or row.get("value") or ""),
            "video_count": int(row.get("video_count") or 0),
            "goods_count": int(row.get("goods_count") or 0),
        }
        for row in rows
        if row.get("value")
    ]


def upsert_video(video: Mapping[str, Any], *, execute_fn: ExecuteFn = execute) -> Any:
    params = [
        video.get("video_id"),
        video.get("region") or "US",
        video.get("author_name"),
        video.get("author_avatar_url"),
        video.get("video_cover_url"),
        video.get("tk_video_url"),
        video.get("video_desc"),
        video.get("video_duration_ms"),
        video.get("create_time"),
        video.get("primary_item_id"),
        video.get("primary_item_name"),
        _json(video.get("raw")),
    ]
    return execute_fn(
        """
        INSERT INTO tabcut_videos (
            video_id, region, author_name, author_avatar_url, video_cover_url,
            tk_video_url, video_desc, video_duration_ms, create_time,
            primary_item_id, primary_item_name, raw_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            region=VALUES(region),
            author_name=VALUES(author_name),
            author_avatar_url=VALUES(author_avatar_url),
            video_cover_url=VALUES(video_cover_url),
            tk_video_url=VALUES(tk_video_url),
            video_desc=VALUES(video_desc),
            video_duration_ms=VALUES(video_duration_ms),
            create_time=VALUES(create_time),
            primary_item_id=VALUES(primary_item_id),
            primary_item_name=VALUES(primary_item_name),
            raw_json=VALUES(raw_json),
            last_seen_at=CURRENT_TIMESTAMP
        """,
        params,
    )


def upsert_video_snapshot(video: Mapping[str, Any], *, execute_fn: ExecuteFn = execute) -> Any:
    params = [
        video.get("biz_date"),
        video.get("region") or "US",
        video.get("video_id"),
        video.get("source_sort") or "unknown",
        video.get("rank_position"),
        video.get("play_count"),
        video.get("like_count"),
        video.get("share_count"),
        video.get("comment_count"),
        video.get("item_sold_count"),
        video.get("video_split_sold_count"),
        video.get("video_split_gmv"),
        video.get("related_item_id"),
        video.get("related_item_name"),
        _json(video.get("raw")),
    ]
    return execute_fn(
        """
        INSERT INTO tabcut_video_snapshots (
            biz_date, region, video_id, source_sort, rank_position,
            play_count, like_count, share_count, comment_count, item_sold_count,
            video_split_sold_count, video_split_gmv, related_item_id,
            related_item_name, snapshot_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rank_position=VALUES(rank_position),
            play_count=VALUES(play_count),
            like_count=VALUES(like_count),
            share_count=VALUES(share_count),
            comment_count=VALUES(comment_count),
            item_sold_count=VALUES(item_sold_count),
            video_split_sold_count=VALUES(video_split_sold_count),
            video_split_gmv=VALUES(video_split_gmv),
            related_item_id=VALUES(related_item_id),
            related_item_name=VALUES(related_item_name),
            snapshot_json=VALUES(snapshot_json),
            crawled_at=CURRENT_TIMESTAMP
        """,
        params,
    )


def upsert_goods(goods: Mapping[str, Any], *, execute_fn: ExecuteFn = execute) -> Any:
    params = [
        goods.get("item_id"),
        goods.get("region") or "US",
        goods.get("item_name"),
        goods.get("item_pic_url"),
        goods.get("category_id"),
        goods.get("category_name"),
        goods.get("category_l1_id"),
        goods.get("category_l1_name"),
        goods.get("category_l2_id"),
        goods.get("category_l2_name"),
        goods.get("category_l3_id"),
        goods.get("category_l3_name"),
        goods.get("seller_id"),
        goods.get("seller_name"),
        goods.get("seller_type"),
        _json(goods.get("raw")),
    ]
    return execute_fn(
        """
        INSERT INTO tabcut_goods (
            item_id, region, item_name, item_pic_url, category_id, category_name,
            category_l1_id, category_l1_name, category_l2_id, category_l2_name,
            category_l3_id, category_l3_name, seller_id, seller_name, seller_type,
            raw_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            region=VALUES(region),
            item_name=VALUES(item_name),
            item_pic_url=VALUES(item_pic_url),
            category_id=VALUES(category_id),
            category_name=VALUES(category_name),
            category_l1_id=VALUES(category_l1_id),
            category_l1_name=VALUES(category_l1_name),
            category_l2_id=VALUES(category_l2_id),
            category_l2_name=VALUES(category_l2_name),
            category_l3_id=VALUES(category_l3_id),
            category_l3_name=VALUES(category_l3_name),
            seller_id=VALUES(seller_id),
            seller_name=VALUES(seller_name),
            seller_type=VALUES(seller_type),
            raw_json=VALUES(raw_json),
            last_seen_at=CURRENT_TIMESTAMP
        """,
        params,
    )


def upsert_goods_snapshot(goods: Mapping[str, Any], *, execute_fn: ExecuteFn = execute) -> Any:
    params = [
        goods.get("biz_date"),
        goods.get("region") or "US",
        goods.get("item_id"),
        goods.get("source") or "goods_ranking",
        goods.get("rank_position"),
        goods.get("price_min"),
        goods.get("price_max"),
        goods.get("commission_rate"),
        goods.get("sold_count_1d"),
        goods.get("sold_count_7d"),
        goods.get("sold_count_30d"),
        goods.get("sold_count_total"),
        goods.get("sold_count_period"),
        goods.get("sold_growth_rate_1d"),
        goods.get("sold_growth_rate_7d"),
        goods.get("sold_growth_rate_30d"),
        goods.get("sold_growth_rate_period"),
        goods.get("gmv_1d"),
        goods.get("gmv_7d"),
        goods.get("gmv_30d"),
        goods.get("gmv_total"),
        goods.get("gmv_period"),
        goods.get("related_video_count"),
        goods.get("related_creator_count"),
        goods.get("related_live_count"),
        goods.get("discover_time"),
        _json(goods.get("raw")),
    ]
    return execute_fn(
        """
        INSERT INTO tabcut_goods_snapshots (
            biz_date, region, item_id, source, rank_position, price_min, price_max,
            commission_rate, sold_count_1d, sold_count_7d, sold_count_30d,
            sold_count_total, sold_count_period, sold_growth_rate_1d,
            sold_growth_rate_7d, sold_growth_rate_30d, sold_growth_rate_period,
            gmv_1d, gmv_7d, gmv_30d, gmv_total, gmv_period,
            related_video_count, related_creator_count, related_live_count,
            discover_time, snapshot_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s
        )
        ON DUPLICATE KEY UPDATE
            rank_position=VALUES(rank_position),
            price_min=VALUES(price_min),
            price_max=VALUES(price_max),
            commission_rate=VALUES(commission_rate),
            sold_count_1d=VALUES(sold_count_1d),
            sold_count_7d=VALUES(sold_count_7d),
            sold_count_30d=VALUES(sold_count_30d),
            sold_count_total=VALUES(sold_count_total),
            sold_count_period=VALUES(sold_count_period),
            sold_growth_rate_1d=VALUES(sold_growth_rate_1d),
            sold_growth_rate_7d=VALUES(sold_growth_rate_7d),
            sold_growth_rate_30d=VALUES(sold_growth_rate_30d),
            sold_growth_rate_period=VALUES(sold_growth_rate_period),
            gmv_1d=VALUES(gmv_1d),
            gmv_7d=VALUES(gmv_7d),
            gmv_30d=VALUES(gmv_30d),
            gmv_total=VALUES(gmv_total),
            gmv_period=VALUES(gmv_period),
            related_video_count=VALUES(related_video_count),
            related_creator_count=VALUES(related_creator_count),
            related_live_count=VALUES(related_live_count),
            discover_time=VALUES(discover_time),
            snapshot_json=VALUES(snapshot_json),
            crawled_at=CURRENT_TIMESTAMP
        """,
        params,
    )


def upsert_video_candidate(candidate: Mapping[str, Any], *, execute_fn: ExecuteFn = execute) -> Any:
    params = [
        candidate.get("biz_date"),
        candidate.get("region") or "US",
        candidate.get("video_id"),
        candidate.get("primary_item_id"),
        candidate.get("score") or 0,
        _json(candidate.get("score_parts")),
        candidate.get("play_count"),
        candidate.get("item_sold_count"),
        candidate.get("video_split_sold_count"),
        candidate.get("video_split_gmv"),
        candidate.get("goods_sold_count_7d"),
        candidate.get("goods_gmv_7d"),
        candidate.get("goods_sold_count_total"),
        candidate.get("goods_gmv_total"),
        candidate.get("goods_growth_rate_7d"),
        candidate.get("category_l1_name"),
        candidate.get("category_l2_name"),
        candidate.get("category_l3_name"),
        _json(candidate.get("candidate_json")),
    ]
    return execute_fn(
        """
        INSERT INTO tabcut_video_candidates (
            biz_date, region, video_id, primary_item_id, score, score_parts_json,
            play_count, item_sold_count, video_split_sold_count, video_split_gmv,
            goods_sold_count_7d, goods_gmv_7d, goods_sold_count_total, goods_gmv_total,
            goods_growth_rate_7d, category_l1_name, category_l2_name, category_l3_name,
            candidate_json
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s
        )
        ON DUPLICATE KEY UPDATE
            primary_item_id=VALUES(primary_item_id),
            score=VALUES(score),
            score_parts_json=VALUES(score_parts_json),
            play_count=VALUES(play_count),
            item_sold_count=VALUES(item_sold_count),
            video_split_sold_count=VALUES(video_split_sold_count),
            video_split_gmv=VALUES(video_split_gmv),
            goods_sold_count_7d=VALUES(goods_sold_count_7d),
            goods_gmv_7d=VALUES(goods_gmv_7d),
            goods_sold_count_total=VALUES(goods_sold_count_total),
            goods_gmv_total=VALUES(goods_gmv_total),
            goods_growth_rate_7d=VALUES(goods_growth_rate_7d),
            category_l1_name=VALUES(category_l1_name),
            category_l2_name=VALUES(category_l2_name),
            category_l3_name=VALUES(category_l3_name),
            candidate_json=VALUES(candidate_json),
            crawled_at=CURRENT_TIMESTAMP
        """,
        params,
    )
