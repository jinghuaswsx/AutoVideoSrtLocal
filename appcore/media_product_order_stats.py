from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from appcore.db import query
from appcore.order_analytics import current_meta_business_date
from appcore.order_analytics._constants import COUNTRY_TO_LANG

WINDOW_KEYS = ("today", "yesterday", "last_7d", "last_30d")


def _product_ids(product_ids: list[int] | tuple[int, ...] | set[int]) -> list[int]:
    ids: set[int] = set()
    for value in product_ids or ():
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            ids.add(pid)
    return sorted(ids)


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in WINDOW_KEYS}


def _empty_stats(today: date) -> dict[str, Any]:
    return {
        "total": _empty_counts(),
        "by_lang": {},
        "computed_at": today.isoformat(),
    }


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _country_to_lang(country: Any) -> str | None:
    code = str(country or "").strip().upper()
    if not code:
        return None
    lang = COUNTRY_TO_LANG.get(code)
    if not lang:
        return None
    return str(lang).strip().lower()


def _add_counts(bucket: dict[str, int], business_date: date, count: int, today: date) -> None:
    if count <= 0:
        return
    yesterday = today - timedelta(days=1)
    last_7d_start = today - timedelta(days=6)
    last_30d_start = today - timedelta(days=29)
    if business_date == today:
        bucket["today"] += count
    if business_date == yesterday:
        bucket["yesterday"] += count
    if last_7d_start <= business_date <= today:
        bucket["last_7d"] += count
    if last_30d_start <= business_date <= today:
        bucket["last_30d"] += count


def get_product_order_stats(
    product_ids: list[int] | tuple[int, ...] | set[int],
    *,
    today: date | None = None,
) -> dict[int, dict[str, Any]]:
    """Return recent order-count windows for media products.

    Docs-anchor: docs/superpowers/specs/2026-06-05-medias-order-stats-column-design.md
    """
    ids = _product_ids(product_ids)
    if not ids:
        return {}
    business_today = today or current_meta_business_date()
    start_30d = business_today - timedelta(days=29)
    placeholders = ",".join(["%s"] * len(ids))
    rows = query(
        "SELECT "
        "  opl.product_id, "
        "  UPPER(TRIM(COALESCE(NULLIF(TRIM(opl.buyer_country), ''), NULLIF(TRIM(dol.buyer_country), ''), ''))) AS buyer_country, "
        "  dol.meta_business_date AS business_date, "
        "  COUNT(DISTINCT NULLIF(TRIM(dol.dxm_package_id), '')) AS order_count "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        f"WHERE opl.product_id IN ({placeholders}) "
        "  AND dol.meta_business_date BETWEEN %s AND %s "
        "GROUP BY opl.product_id, buyer_country, dol.meta_business_date",
        (*ids, start_30d, business_today),
    )
    out = {pid: _empty_stats(business_today) for pid in ids}
    for row in rows:
        try:
            pid = int(row.get("product_id") or 0)
        except (TypeError, ValueError):
            continue
        if pid not in out:
            continue
        business_date = _date_value(row.get("business_date"))
        if business_date is None:
            continue
        count = int(row.get("order_count") or 0)
        _add_counts(out[pid]["total"], business_date, count, business_today)
        lang = _country_to_lang(row.get("buyer_country"))
        if lang:
            lang_bucket = out[pid]["by_lang"].setdefault(lang, _empty_counts())
            _add_counts(lang_bucket, business_date, count, business_today)
    return out
