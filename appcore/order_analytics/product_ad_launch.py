"""Product ad-launch date helpers for 新品投放分析.

Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ._constants import META_ATTRIBUTION_TIMEZONE

NEW_PRODUCT_WINDOW_DAYS = 7
VALID_PRODUCT_LAUNCH_SCOPES = frozenset({"new", "old", "unmatched"})
AD_MATCH_SOURCE = "ad_match"
FALLBACK_SOURCE = "created_at_fallback"

_LEVEL_PRIORITY = {"campaign": 0, "adset": 1, "ad": 2}


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def beijing_today(now: datetime | None = None) -> date:
    value = now or datetime.now(ZoneInfo(META_ATTRIBUTION_TIMEZONE))
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(META_ATTRIBUTION_TIMEZONE)).replace(tzinfo=None)
    return value.date()


def launch_cutoff(today: date | None = None) -> date:
    return (today or beijing_today()) - timedelta(days=NEW_PRODUCT_WINDOW_DAYS)


def classify_launch_date(ad_launch_date: date, *, today: date | None = None) -> str:
    return "new" if ad_launch_date >= launch_cutoff(today) else "old"


def normalize_product_launch_scope(value: Any) -> str | None:
    scope = str(value or "").strip().lower()
    if not scope:
        return None
    if scope not in VALID_PRODUCT_LAUNCH_SCOPES:
        raise ValueError("product_launch_scope must be one of new/old/unmatched")
    return scope


def _row_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def seed_missing_fallback_launch_dates() -> int:
    rows = query(
        "SELECT COUNT(*) AS missing_count "
        "FROM media_products p "
        "LEFT JOIN product_ad_launch_dates l ON l.product_id = p.id "
        "WHERE p.deleted_at IS NULL AND l.product_id IS NULL",
        (),
    ) or []
    missing_count = _row_int(rows[0], "missing_count") if rows else 0
    execute(
        "INSERT IGNORE INTO product_ad_launch_dates "
        "(product_id, ad_launch_date, source, source_level, source_table) "
        "SELECT p.id, "
        "DATE(COALESCE(p.created_at, CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+08:00'))), "
        "%s, %s, %s "
        "FROM media_products p "
        "LEFT JOIN product_ad_launch_dates l ON l.product_id = p.id "
        "WHERE p.deleted_at IS NULL AND l.product_id IS NULL",
        (FALLBACK_SOURCE, "product_created_at", "media_products"),
    )
    return missing_count


def _normalize_product_ids(product_ids: list[Any] | tuple[Any, ...]) -> tuple[int, ...]:
    normalized: set[int] = set()
    for product_id in product_ids:
        try:
            pid = int(product_id)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            normalized.add(pid)
    return tuple(sorted(normalized))


def _earliest_ad_matches_for_products(product_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    placeholders = ", ".join(["%s"] * len(product_ids))
    rows = query(
        "SELECT product_id, ad_launch_date, source_level, source_table, source_row_id "
        "FROM ("
        "  SELECT product_id, COALESCE(meta_business_date, report_date) AS ad_launch_date, "
        "         'campaign' AS source_level, 'meta_ad_daily_campaign_metrics' AS source_table, MIN(id) AS source_row_id "
        "  FROM meta_ad_daily_campaign_metrics "
        f"  WHERE product_id IN ({placeholders}) AND product_id IS NOT NULL "
        "  GROUP BY product_id, COALESCE(meta_business_date, report_date) "
        "  UNION ALL "
        "  SELECT product_id, COALESCE(meta_business_date, report_date) AS ad_launch_date, "
        "         'adset' AS source_level, 'meta_ad_daily_adset_metrics' AS source_table, MIN(id) AS source_row_id "
        "  FROM meta_ad_daily_adset_metrics "
        f"  WHERE product_id IN ({placeholders}) AND product_id IS NOT NULL "
        "  GROUP BY product_id, COALESCE(meta_business_date, report_date) "
        "  UNION ALL "
        "  SELECT product_id, COALESCE(meta_business_date, report_date) AS ad_launch_date, "
        "         'ad' AS source_level, 'meta_ad_daily_ad_metrics' AS source_table, MIN(id) AS source_row_id "
        "  FROM meta_ad_daily_ad_metrics "
        f"  WHERE product_id IN ({placeholders}) AND product_id IS NOT NULL "
        "  GROUP BY product_id, COALESCE(meta_business_date, report_date) "
        ") matches "
        "WHERE ad_launch_date IS NOT NULL "
        "ORDER BY product_id, ad_launch_date, FIELD(source_level, 'campaign', 'adset', 'ad')",
        product_ids + product_ids + product_ids,
    ) or []
    earliest: dict[int, dict[str, Any]] = {}
    for row in sorted(
        rows,
        key=lambda item: (
            int(item["product_id"]),
            item["ad_launch_date"],
            _LEVEL_PRIORITY.get(str(item["source_level"]), 99),
        ),
    ):
        pid = int(row["product_id"])
        if pid not in earliest:
            earliest[pid] = dict(row)
    return list(earliest.values())


def _launch_records_for_products(product_ids: tuple[int, ...]) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, ad_launch_date, source FROM product_ad_launch_dates WHERE product_id IN ({placeholders})",
        product_ids,
    ) or []
    records: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            pid = int(row.get("product_id") or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            records[pid] = dict(row)
    return records


def refresh_ad_match_launch_dates_for_products(
    product_ids: list[Any] | tuple[Any, ...],
) -> dict[str, int]:
    rows = _earliest_ad_matches_for_products(_normalize_product_ids(product_ids))
    existing_records = _launch_records_for_products(
        tuple(int(row["product_id"]) for row in rows if row.get("product_id") is not None)
    )
    updated_rows = 0
    for row in rows:
        pid = int(row["product_id"])
        existing = existing_records.get(pid) or {}
        existing_source = str(existing.get("source") or "")
        existing_date = existing.get("ad_launch_date")
        if (
            existing_source not in (None, "", FALLBACK_SOURCE)
            and existing_date is not None
            and existing_date <= row["ad_launch_date"]
        ):
            continue
        execute(
            "INSERT INTO product_ad_launch_dates "
            "(product_id, ad_launch_date, source, source_level, source_table, source_row_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "ad_launch_date = IF(product_ad_launch_dates.source = 'created_at_fallback' OR product_ad_launch_dates.ad_launch_date > VALUES(ad_launch_date), VALUES(ad_launch_date), product_ad_launch_dates.ad_launch_date), "
            "source_level = IF(product_ad_launch_dates.source = 'created_at_fallback' OR product_ad_launch_dates.ad_launch_date > VALUES(ad_launch_date), VALUES(source_level), product_ad_launch_dates.source_level), "
            "source_table = IF(product_ad_launch_dates.source = 'created_at_fallback' OR product_ad_launch_dates.ad_launch_date > VALUES(ad_launch_date), VALUES(source_table), product_ad_launch_dates.source_table), "
            "source_row_id = IF(product_ad_launch_dates.source = 'created_at_fallback' OR product_ad_launch_dates.ad_launch_date > VALUES(ad_launch_date), VALUES(source_row_id), product_ad_launch_dates.source_row_id), "
            "source = IF(product_ad_launch_dates.source = 'created_at_fallback' OR product_ad_launch_dates.ad_launch_date > VALUES(ad_launch_date), VALUES(source), product_ad_launch_dates.source)",
            (
                pid,
                row["ad_launch_date"],
                AD_MATCH_SOURCE,
                row["source_level"],
                row["source_table"],
                row["source_row_id"],
            ),
        )
        updated_rows += 1
    return {"matched_products": len(rows), "updated_rows": updated_rows}


def backfill_product_ad_launch_dates() -> dict[str, int]:
    seeded = seed_missing_fallback_launch_dates()
    rows = query("SELECT id FROM media_products WHERE deleted_at IS NULL", ()) or []
    product_ids = [int(row["id"]) for row in rows if row.get("id")]
    refreshed = refresh_ad_match_launch_dates_for_products(product_ids)
    return {
        "fallback_inserted": seeded,
        "matched_products": refreshed["matched_products"],
        "updated_rows": refreshed["updated_rows"],
    }


def get_product_ids_for_launch_scope(
    scope: str,
    *,
    today: date | None = None,
) -> tuple[int, ...]:
    normalized = normalize_product_launch_scope(scope)
    if normalized == "unmatched":
        return ()
    if normalized not in {"new", "old"}:
        raise ValueError("product_launch_scope must be one of new/old/unmatched")
    seed_missing_fallback_launch_dates()
    cutoff = launch_cutoff(today)
    op = ">=" if normalized == "new" else "<"
    rows = query(
        f"SELECT product_id FROM product_ad_launch_dates WHERE ad_launch_date {op} %s ORDER BY product_id",
        (cutoff,),
    ) or []
    return tuple(int(row["product_id"]) for row in rows if row.get("product_id") is not None)
