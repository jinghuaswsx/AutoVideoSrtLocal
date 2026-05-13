from __future__ import annotations

import argparse
import json
from typing import Any, Callable, Mapping

from appcore.db import execute, query
from appcore.tabcut_selection.models import extract_primary_item_price_fields


QueryFn = Callable[[str, list[Any]], list[dict]]
ExecuteFn = Callable[[str, list[Any]], Any]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def resolve_candidate_price_fields(
    *,
    candidate_json: Any,
    video_raw_json: Any,
    goods_price_min: Any,
    goods_price_max: Any,
) -> dict[str, Any]:
    candidate = _json_dict(candidate_json)
    video = candidate.get("video") if isinstance(candidate.get("video"), Mapping) else {}
    goods = candidate.get("goods") if isinstance(candidate.get("goods"), Mapping) else {}
    video_raw = _json_dict(video_raw_json)

    sources: list[Any] = [video]
    if isinstance(video, Mapping):
        sources.append(video.get("raw"))
    sources.extend([video_raw, goods])

    fields = extract_primary_item_price_fields(*sources)
    if fields["primary_item_price_min"] is not None or fields["primary_item_price_max"] is not None:
        return fields

    fallback_min = _float_or_none(goods_price_min)
    fallback_max = _float_or_none(goods_price_max)
    if fallback_min is None and fallback_max is None:
        return fields
    if fallback_min is None:
        fallback_min = fallback_max
    if fallback_max is None:
        fallback_max = fallback_min
    return {
        "primary_item_price_min": fallback_min,
        "primary_item_price_max": fallback_max,
        "price_currency": None,
    }


def _fetch_backfill_rows(limit: int, *, query_fn: QueryFn) -> list[dict]:
    return query_fn(
        """
        SELECT c.id, c.candidate_json, v.raw_json AS video_raw_json,
               gs.price_min AS goods_price_min, gs.price_max AS goods_price_max
        FROM tabcut_video_candidates c
        LEFT JOIN tabcut_videos v ON v.video_id = c.video_id
        LEFT JOIN (
            SELECT biz_date, region, item_id,
                   MIN(price_min) AS price_min,
                   MAX(price_max) AS price_max
            FROM tabcut_goods_snapshots
            GROUP BY biz_date, region, item_id
        ) gs ON gs.biz_date = c.biz_date
            AND gs.region = c.region
            AND gs.item_id = c.primary_item_id
        WHERE c.primary_item_price_min IS NULL
        ORDER BY c.id ASC
        LIMIT %s
        """,
        [limit],
    )


def _update_candidate_price(candidate_id: Any, fields: Mapping[str, Any], *, execute_fn: ExecuteFn) -> Any:
    return execute_fn(
        """
        UPDATE tabcut_video_candidates
           SET primary_item_price_min = %s,
               primary_item_price_max = %s,
               price_currency = %s
         WHERE id = %s
        """,
        [
            fields.get("primary_item_price_min"),
            fields.get("primary_item_price_max"),
            fields.get("price_currency"),
            candidate_id,
        ],
    )


def backfill_candidate_prices(
    *,
    batch_size: int = 500,
    limit: int | None = None,
    dry_run: bool = True,
    query_fn: QueryFn = query,
    execute_fn: ExecuteFn = execute,
) -> dict[str, Any]:
    scanned = 0
    updated = 0
    skipped = 0
    remaining = limit
    batch_size = max(1, min(batch_size, 5000))

    while remaining is None or remaining > 0:
        current_limit = min(batch_size, remaining) if remaining is not None else batch_size
        rows = _fetch_backfill_rows(current_limit, query_fn=query_fn)
        if not rows:
            break

        for row in rows:
            scanned += 1
            fields = resolve_candidate_price_fields(
                candidate_json=row.get("candidate_json"),
                video_raw_json=row.get("video_raw_json"),
                goods_price_min=row.get("goods_price_min"),
                goods_price_max=row.get("goods_price_max"),
            )
            if fields["primary_item_price_min"] is None and fields["primary_item_price_max"] is None:
                skipped += 1
                continue
            updated += 1
            if not dry_run:
                _update_candidate_price(row.get("id"), fields, execute_fn=execute_fn)

        if dry_run:
            break
        if remaining is not None:
            remaining -= len(rows)
        if len(rows) < current_limit:
            break

    return {"scanned": scanned, "updated": updated, "skipped": skipped, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Tabcut video candidate primary item prices.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    summary = backfill_candidate_prices(
        batch_size=args.batch_size,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
