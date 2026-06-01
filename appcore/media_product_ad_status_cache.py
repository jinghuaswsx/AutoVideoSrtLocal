from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from appcore.db import get_conn, query

STATUS_ALL = "all"
STATUS_ACTIVE = "active"
STATUS_STOPPED = "stopped"
STATUS_NEVER = "never"
DELIVERY_STATUS_FILTERS = (STATUS_ALL, STATUS_ACTIVE, STATUS_STOPPED, STATUS_NEVER)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    return _safe_float(value)


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value else None


def _roas(numerator: Any, denominator: Any) -> float | None:
    spend = _safe_float(denominator)
    if spend <= 0:
        return None
    return round(_safe_float(numerator) / spend, 4)


def _delivery_status(total_spend: Any, active_spend: Any) -> str:
    if _safe_float(total_spend) <= 0:
        return STATUS_NEVER
    if _safe_float(active_spend) > 0:
        return STATUS_ACTIVE
    return STATUS_STOPPED


def normalize_delivery_status_filter(value: str | None) -> str:
    status = str(value or STATUS_ALL).strip().lower()
    return status if status in DELIVERY_STATUS_FILTERS else STATUS_ALL


def _placeholders(values: list[int]) -> str:
    return ",".join(["%s"] * len(values))


def _product_ids(product_ids: list[int] | tuple[int, ...] | set[int]) -> list[int]:
    return sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})


def get_product_ad_summary_cache(product_ids: list[int] | tuple[int, ...] | set[int]) -> dict[int, dict[str, Any]]:
    ids = _product_ids(product_ids)
    if not ids:
        return {}
    rows = query(
        "SELECT product_id, order_revenue_usd, shipping_revenue_usd, total_revenue_usd, "
        "ad_spend_usd, active_7d_ad_spend_usd, overall_roas, delivery_status, computed_at "
        f"FROM media_product_ad_summary_cache WHERE product_id IN ({_placeholders(ids)})",
        tuple(ids),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        pid = int(row.get("product_id") or 0)
        if not pid:
            continue
        out[pid] = {
            "product_id": pid,
            "order_revenue_usd": _safe_float(row.get("order_revenue_usd")),
            "shipping_revenue_usd": _safe_float(row.get("shipping_revenue_usd")),
            "total_revenue_usd": _safe_float(row.get("total_revenue_usd")),
            "ad_spend_usd": _safe_float(row.get("ad_spend_usd")),
            "active_7d_ad_spend_usd": _safe_float(row.get("active_7d_ad_spend_usd")),
            "overall_roas": _nullable_float(row.get("overall_roas")),
            "delivery_status": normalize_delivery_status_filter(row.get("delivery_status")) or STATUS_NEVER,
            "computed_at": _iso(row.get("computed_at")),
        }
    return out


def get_product_lang_ad_summary_cache(product_ids: list[int] | tuple[int, ...] | set[int]) -> dict[int, dict[str, dict[str, Any]]]:
    ids = _product_ids(product_ids)
    if not ids:
        return {}
    rows = query(
        "SELECT product_id, lang, item_count, pushed_video_count, ad_spend_usd, "
        "purchase_value_usd, ad_roas, active_7d_ad_spend_usd, computed_at "
        f"FROM media_product_lang_ad_summary_cache WHERE product_id IN ({_placeholders(ids)})",
        tuple(ids),
    )
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        pid = int(row.get("product_id") or 0)
        lang = str(row.get("lang") or "").strip().lower()
        if not pid or not lang:
            continue
        out.setdefault(pid, {})[lang] = {
            "product_id": pid,
            "lang": lang,
            "item_count": int(row.get("item_count") or 0),
            "pushed_video_count": int(row.get("pushed_video_count") or 0),
            "ad_spend_usd": _safe_float(row.get("ad_spend_usd")),
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": _nullable_float(row.get("ad_roas")),
            "active_7d_ad_spend_usd": _safe_float(row.get("active_7d_ad_spend_usd")),
            "delivery_status": _delivery_status(row.get("ad_spend_usd"), row.get("active_7d_ad_spend_usd")),
            "computed_at": _iso(row.get("computed_at")),
        }
    return out


_PRODUCT_REFRESH_SQL = """
INSERT INTO media_product_ad_summary_cache (
  product_id, order_revenue_usd, shipping_revenue_usd, total_revenue_usd,
  ad_spend_usd, active_7d_ad_spend_usd, overall_roas, delivery_status, computed_at
)
SELECT
  p.id AS product_id,
  COALESCE(o.order_revenue_usd, 0) AS order_revenue_usd,
  COALESCE(o.shipping_revenue_usd, 0) AS shipping_revenue_usd,
  COALESCE(o.order_revenue_usd, 0) + COALESCE(o.shipping_revenue_usd, 0) AS total_revenue_usd,
  COALESCE(a.ad_spend_usd, 0) AS ad_spend_usd,
  COALESCE(a.active_7d_ad_spend_usd, 0) AS active_7d_ad_spend_usd,
  CASE
    WHEN COALESCE(a.ad_spend_usd, 0) > 0
    THEN ROUND((COALESCE(o.order_revenue_usd, 0) + COALESCE(o.shipping_revenue_usd, 0)) / a.ad_spend_usd, 4)
    ELSE NULL
  END AS overall_roas,
  CASE
    WHEN COALESCE(a.ad_spend_usd, 0) <= 0 THEN 'never'
    WHEN COALESCE(a.active_7d_ad_spend_usd, 0) > 0 THEN 'active'
    ELSE 'stopped'
  END AS delivery_status,
  NOW() AS computed_at
FROM media_products p
LEFT JOIN (
  SELECT
    d.product_id,
    SUM(COALESCE(op.line_amount_usd, d.line_amount, 0)) AS order_revenue_usd,
    SUM(COALESCE(op.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue_usd
  FROM dianxiaomi_order_lines d
  LEFT JOIN order_profit_lines op ON op.dxm_order_line_id = d.id
  WHERE d.product_id IS NOT NULL
  GROUP BY d.product_id
) o ON o.product_id = p.id
LEFT JOIN (
  SELECT
    product_id,
    SUM(COALESCE(spend_usd, 0)) AS ad_spend_usd,
    SUM(
      CASE
        WHEN DATE(COALESCE(meta_business_date, report_date)) BETWEEN DATE_SUB(CURDATE(), INTERVAL 2 DAY) AND CURDATE()
        THEN COALESCE(spend_usd, 0)
        ELSE 0
      END
    ) AS active_7d_ad_spend_usd
  FROM meta_ad_daily_campaign_metrics
  WHERE product_id IS NOT NULL AND COALESCE(spend_usd, 0) > 0
  GROUP BY product_id
) a ON a.product_id = p.id
WHERE p.deleted_at IS NULL
ON DUPLICATE KEY UPDATE
  order_revenue_usd=VALUES(order_revenue_usd),
  shipping_revenue_usd=VALUES(shipping_revenue_usd),
  total_revenue_usd=VALUES(total_revenue_usd),
  ad_spend_usd=VALUES(ad_spend_usd),
  active_7d_ad_spend_usd=VALUES(active_7d_ad_spend_usd),
  overall_roas=VALUES(overall_roas),
  delivery_status=VALUES(delivery_status),
  computed_at=VALUES(computed_at)
"""


_LANG_REFRESH_SQL = """
INSERT INTO media_product_lang_ad_summary_cache (
  product_id, lang, item_count, pushed_video_count, ad_spend_usd,
  purchase_value_usd, ad_roas, active_7d_ad_spend_usd, computed_at
)
SELECT
  ic.product_id,
  ic.lang,
  ic.item_count,
  ic.pushed_video_count,
  COALESCE(ad.ad_spend_usd, 0) AS ad_spend_usd,
  COALESCE(ad.purchase_value_usd, 0) AS purchase_value_usd,
  CASE
    WHEN COALESCE(ad.ad_spend_usd, 0) > 0
    THEN ROUND(COALESCE(ad.purchase_value_usd, 0) / ad.ad_spend_usd, 4)
    ELSE NULL
  END AS ad_roas,
  COALESCE(ad.active_7d_ad_spend_usd, 0) AS active_7d_ad_spend_usd,
  NOW() AS computed_at
FROM (
  SELECT
    i.product_id,
    i.lang,
    COUNT(DISTINCT i.id) AS item_count,
    COUNT(DISTINCT CASE WHEN l.status = 'success' THEN i.id END) AS pushed_video_count
  FROM media_items i
  JOIN media_products p ON p.id = i.product_id AND p.deleted_at IS NULL
  JOIN media_languages ml ON ml.code = i.lang AND ml.enabled = 1
  LEFT JOIN media_push_logs l ON l.item_id = i.id AND l.status = 'success'
  WHERE i.deleted_at IS NULL
  GROUP BY i.product_id, i.lang
) ic
LEFT JOIN (
  SELECT
    matched.product_id,
    matched.lang,
    SUM(matched.spend_usd) AS ad_spend_usd,
    SUM(matched.purchase_value_usd) AS purchase_value_usd,
    SUM(
      CASE
        WHEN DATE(matched.activity_date) BETWEEN DATE_SUB(CURDATE(), INTERVAL 2 DAY) AND CURDATE()
        THEN matched.spend_usd
        ELSE 0
      END
    ) AS active_7d_ad_spend_usd
  FROM (
    SELECT DISTINCT
      i.product_id,
      i.lang,
      m.id AS metric_id,
      COALESCE(m.spend_usd, 0) AS spend_usd,
      COALESCE(m.purchase_value_usd, 0) AS purchase_value_usd,
      COALESCE(m.meta_business_date, m.report_date) AS activity_date
    FROM media_items i
    JOIN media_products p ON p.id = i.product_id AND p.deleted_at IS NULL
    JOIN media_languages ml ON ml.code = i.lang AND ml.enabled = 1
    JOIN meta_ad_daily_ad_metrics m
      ON m.product_id = i.product_id
     AND COALESCE(m.spend_usd, 0) > 0
     AND (
       m.ad_name LIKE CONCAT('%%', i.filename, '%%')
       OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
       OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
       OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
       OR (
         m.market_country IS NOT NULL
         AND m.market_country <> ''
         AND LOWER(i.lang) = CASE UPPER(m.market_country)
           WHEN 'US' THEN 'en'
           WHEN 'GB' THEN 'en'
           WHEN 'UK' THEN 'en'
           WHEN 'AU' THEN 'en'
           WHEN 'CA' THEN 'en'
           WHEN 'IE' THEN 'en'
           WHEN 'NZ' THEN 'en'
           WHEN 'DE' THEN 'de'
           WHEN 'AT' THEN 'de'
           WHEN 'FR' THEN 'fr'
           WHEN 'ES' THEN 'es'
           WHEN 'IT' THEN 'it'
           WHEN 'NL' THEN 'nl'
           WHEN 'SE' THEN 'sv'
           WHEN 'FI' THEN 'fi'
           WHEN 'JP' THEN 'ja'
           WHEN 'KR' THEN 'ko'
           WHEN 'BR' THEN 'pt-br'
           WHEN 'PT' THEN 'pt'
           ELSE NULL
         END
       )
     )
    WHERE i.deleted_at IS NULL
  ) matched
  GROUP BY matched.product_id, matched.lang
) ad ON ad.product_id = ic.product_id AND ad.lang = ic.lang
ON DUPLICATE KEY UPDATE
  item_count=VALUES(item_count),
  pushed_video_count=VALUES(pushed_video_count),
  ad_spend_usd=VALUES(ad_spend_usd),
  purchase_value_usd=VALUES(purchase_value_usd),
  ad_roas=VALUES(ad_roas),
  active_7d_ad_spend_usd=VALUES(active_7d_ad_spend_usd),
  computed_at=VALUES(computed_at)
"""


def refresh_all() -> dict[str, int]:
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM media_product_ad_summary_cache")
            cur.execute(_PRODUCT_REFRESH_SQL)
            product_rows = int(cur.rowcount or 0)
            cur.execute("DELETE FROM media_product_lang_ad_summary_cache")
            cur.execute(_LANG_REFRESH_SQL)
            lang_rows = int(cur.rowcount or 0)
        conn.commit()
        return {"product_rows": product_rows, "lang_rows": lang_rows}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
