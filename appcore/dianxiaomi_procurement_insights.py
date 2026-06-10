from __future__ import annotations

"""Dianxiaomi procurement helper insights.

Docs-anchor:
docs/superpowers/specs/2026-06-09-dianxiaomi-procurement-insights-extension-design.md
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from appcore.db import query, query_one
from appcore import media_product_ad_orders_report
from appcore import media_product_ad_status_cache
from appcore import media_product_order_stats
from appcore.order_analytics import current_meta_business_date
from appcore.order_analytics import data_quality as dq


_MAX_TEXT_LEN = 500
_MAX_TOKENS = 24

_PERIODS: tuple[tuple[str, str], ...] = (
    ("today", "今天"),
    ("yesterday", "昨天"),
    ("last_7d", "7天"),
    ("last_30d", "30天"),
)

_MARKET_GROUPS: list[tuple[str, list[str], str]] = [
    ("en", ["US", "GB", "AU", "CA", "IE", "NZ"], "英语市场"),
    ("de", ["DE", "AT"], "德国市场"),
    ("fr", ["FR"], "法国市场"),
    ("es", ["ES"], "西班牙市场"),
    ("it", ["IT"], "意大利市场"),
    ("nl", ["NL"], "荷兰市场"),
    ("sv", ["SE"], "瑞典市场"),
    ("fi", ["FI"], "芬兰市场"),
    ("ja", ["JP"], "日本市场"),
    ("ko", ["KR"], "韩国市场"),
    ("pt-br", ["BR"], "巴西市场"),
    ("pt", ["PT"], "葡萄牙市场"),
]

_MARKET_LABELS = {lang: label for lang, _countries, label in _MARKET_GROUPS}
_MARKET_COUNTRIES = {lang: countries for lang, countries, _label in _MARKET_GROUPS}

_STATUS_LABELS = {
    media_product_ad_status_cache.STATUS_ACTIVE: "投放中",
    media_product_ad_status_cache.STATUS_STOPPED: "已停投",
    media_product_ad_status_cache.STATUS_NEVER: "从未投放",
}


def _strip_text(value: Any, *, max_len: int = _MAX_TEXT_LEN) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(text.split())[:max_len]


def _split_tokens(*values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _strip_text(value)
        if not text:
            continue
        for raw in text.replace("，", ",").replace("；", ",").replace(";", ",").split(","):
            token = raw.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= _MAX_TOKENS:
                return out
    return out


def _normalize_lang(value: Any) -> str:
    return str(value or "").strip().lower()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_value(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


def _first_float_or_none(*values: Any) -> float | None:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _first_float_value(*values: Any) -> float:
    parsed = _first_float_or_none(*values)
    return parsed if parsed is not None else 0.0


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = _strip_text(value, max_len=80)
    return text or None


def _placeholders(values: list[Any]) -> str:
    return ",".join(["%s"] * len(values))


def _product_payload(row: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "name": row.get("name") or "",
        "product_code": row.get("product_code") or "",
        "shopifyid": row.get("shopifyid") or "",
        "shopify_title": row.get("shopify_title") or "",
        "match_method": match.get("method") or "",
        "match_confidence": match.get("confidence") or "low",
        "match_score": int(match.get("score") or 0),
        "match_value": match.get("value") or "",
        "evidence_count": int(match.get("evidence_count") or 0),
        "ambiguous": bool(match.get("ambiguous")),
    }


def normalize_clues(raw: dict[str, Any]) -> dict[str, Any]:
    skus = _split_tokens(raw.get("sku"), raw.get("skus"))
    product_skus = _split_tokens(raw.get("product_sku"), raw.get("product_skus"))
    sku_codes = _split_tokens(raw.get("sku_code"), raw.get("sku_codes"))
    shopify_product_ids = [
        token for token in _split_tokens(raw.get("shopify_product_id"), raw.get("shopify_product_ids"))
        if token.isdigit()
    ]
    return {
        "product_id": _strip_text(raw.get("product_id"), max_len=30),
        "skus": skus,
        "product_skus": product_skus,
        "sku_codes": sku_codes,
        "shopify_product_ids": shopify_product_ids,
        "product_code": _strip_text(raw.get("product_code"), max_len=160),
        "product_name": _strip_text(raw.get("product_name"), max_len=240),
        "page_url": _strip_text(raw.get("page_url"), max_len=500),
    }


def _confidence(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _candidate(
    row: dict[str, Any],
    *,
    method: str,
    score: int,
    value: str = "",
    evidence_count: int = 1,
) -> dict[str, Any]:
    return {
        "row": row,
        "method": method,
        "score": score,
        "confidence": _confidence(score),
        "value": value,
        "evidence_count": evidence_count,
    }


def _find_by_product_id(product_id: str) -> list[dict[str, Any]]:
    if not product_id or not product_id.isdigit():
        return []
    row = query_one(
        "SELECT id, name, product_code, shopifyid, shopify_title "
        "FROM media_products WHERE id=%s AND deleted_at IS NULL",
        (int(product_id),),
    )
    if not row:
        return []
    return [_candidate(row, method="media_products.id", score=100, value=product_id)]


def _find_by_shopify_product_id(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = _placeholders(ids)
    rows = query(
        "SELECT p.id, p.name, p.product_code, p.shopifyid, p.shopify_title, "
        "       MAX(mpsi.shopify_product_id) AS matched_shopify_product_id "
        "FROM media_products p "
        "LEFT JOIN media_product_shopify_ids mpsi ON mpsi.product_id=p.id "
        f"WHERE p.deleted_at IS NULL AND (p.shopifyid IN ({placeholders}) "
        f"OR mpsi.shopify_product_id IN ({placeholders})) "
        "GROUP BY p.id, p.name, p.product_code, p.shopifyid, p.shopify_title "
        "ORDER BY p.id ASC LIMIT 10",
        tuple(ids + ids),
    )
    return [
        _candidate(
            row,
            method="shopify_product_id",
            score=95,
            value=str(row.get("matched_shopify_product_id") or row.get("shopifyid") or ""),
        )
        for row in rows or []
    ]


def _matched_sku_value(row: dict[str, Any], tokens: set[str]) -> str:
    for field in ("dianxiaomi_sku", "dianxiaomi_product_sku", "dianxiaomi_sku_code", "shopify_sku"):
        value = _strip_text(row.get(field), max_len=160)
        if value and value in tokens:
            return value
    return ""


def _find_by_media_product_skus(tokens: list[str]) -> list[dict[str, Any]]:
    if not tokens:
        return []
    placeholders = _placeholders(tokens)
    rows = query(
        "SELECT p.id, p.name, p.product_code, p.shopifyid, p.shopify_title, "
        "       mps.dianxiaomi_sku, mps.dianxiaomi_product_sku, "
        "       mps.dianxiaomi_sku_code, mps.shopify_sku "
        "FROM media_product_skus mps "
        "JOIN media_products p ON p.id=mps.product_id AND p.deleted_at IS NULL "
        f"WHERE mps.dianxiaomi_sku IN ({placeholders}) "
        f"   OR mps.dianxiaomi_product_sku IN ({placeholders}) "
        f"   OR mps.dianxiaomi_sku_code IN ({placeholders}) "
        f"   OR mps.shopify_sku IN ({placeholders}) "
        "ORDER BY p.id ASC LIMIT 20",
        tuple(tokens * 4),
    )
    token_set = set(tokens)
    by_product: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        pid = int(row.get("id") or 0)
        if not pid:
            continue
        existing = by_product.get(pid)
        matched_value = _matched_sku_value(row, token_set)
        if existing:
            existing["evidence_count"] += 1
            if not existing["value"] and matched_value:
                existing["value"] = matched_value
            continue
        by_product[pid] = _candidate(
            row,
            method="media_product_skus",
            score=90,
            value=matched_value,
            evidence_count=1,
        )
    return list(by_product.values())


def _find_by_order_skus(tokens: list[str]) -> list[dict[str, Any]]:
    if not tokens:
        return []
    placeholders = _placeholders(tokens)
    rows = query(
        "SELECT p.id, p.name, p.product_code, p.shopifyid, p.shopify_title, "
        "       COUNT(*) AS evidence_count, "
        "       MAX(COALESCE(d.product_display_sku, d.product_sku, d.product_sub_sku)) AS matched_sku "
        "FROM dianxiaomi_order_lines d "
        "JOIN media_products p ON p.id=d.product_id AND p.deleted_at IS NULL "
        "WHERE d.product_id IS NOT NULL AND ("
        f" d.product_display_sku IN ({placeholders}) "
        f" OR d.product_sku IN ({placeholders}) "
        f" OR d.product_sub_sku IN ({placeholders}) "
        ") "
        "GROUP BY p.id, p.name, p.product_code, p.shopifyid, p.shopify_title "
        "ORDER BY evidence_count DESC, p.id ASC LIMIT 10",
        tuple(tokens * 3),
    )
    return [
        _candidate(
            row,
            method="dianxiaomi_order_lines.sku",
            score=76,
            value=str(row.get("matched_sku") or ""),
            evidence_count=int(row.get("evidence_count") or 0),
        )
        for row in rows or []
    ]


def _find_by_product_code(product_code: str) -> list[dict[str, Any]]:
    if not product_code:
        return []
    row = query_one(
        "SELECT id, name, product_code, shopifyid, shopify_title "
        "FROM media_products WHERE deleted_at IS NULL AND LOWER(product_code)=LOWER(%s) "
        "ORDER BY id ASC LIMIT 1",
        (product_code,),
    )
    if not row:
        return []
    return [_candidate(row, method="media_products.product_code", score=70, value=product_code)]


def _find_by_product_name(product_name: str) -> list[dict[str, Any]]:
    if len(product_name) < 4:
        return []
    like = f"%{product_name[:80]}%"
    rows = query(
        "SELECT id, name, product_code, shopifyid, shopify_title "
        "FROM media_products "
        "WHERE deleted_at IS NULL AND (name LIKE %s OR shopify_title LIKE %s) "
        "ORDER BY updated_at DESC, id ASC LIMIT 5",
        (like, like),
    )
    return [
        _candidate(row, method="media_products.name", score=35, value=product_name)
        for row in rows or []
    ]


def resolve_product(clues: dict[str, Any]) -> dict[str, Any] | None:
    tokens = _split_tokens(
        ",".join(clues.get("skus") or []),
        ",".join(clues.get("product_skus") or []),
        ",".join(clues.get("sku_codes") or []),
    )
    candidate_groups = [
        _find_by_product_id(str(clues.get("product_id") or "")),
        _find_by_shopify_product_id(clues.get("shopify_product_ids") or []),
        _find_by_media_product_skus(tokens),
        _find_by_order_skus(tokens),
        _find_by_product_code(clues.get("product_code") or ""),
        _find_by_product_name(clues.get("product_name") or ""),
    ]
    candidates = [item for group in candidate_groups for item in group]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-int(item.get("score") or 0), -int(item.get("evidence_count") or 0), int(item["row"].get("id") or 0)))
    top = candidates[0]
    if len(candidates) > 1 and int(candidates[1].get("score") or 0) == int(top.get("score") or 0):
        top["ambiguous"] = True
        if top["confidence"] == "high":
            top["confidence"] = "medium"
    return top


def _delivery_label(status: str) -> str:
    normalized = media_product_ad_status_cache.normalize_delivery_status_filter(status)
    if normalized == media_product_ad_status_cache.STATUS_ALL:
        normalized = media_product_ad_status_cache.STATUS_NEVER
    return _STATUS_LABELS.get(normalized, "未知")


def _market_delivery_status(row: dict[str, Any]) -> str:
    if _float_value(row.get("total_spend")) <= 0:
        return media_product_ad_status_cache.STATUS_NEVER
    if _float_value(row.get("today_spend")) > 0:
        return media_product_ad_status_cache.STATUS_ACTIVE
    return media_product_ad_status_cache.STATUS_STOPPED


def _build_periods(ad_total: dict[str, Any], order_counts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    periods: dict[str, dict[str, Any]] = {}
    for key, label in _PERIODS:
        ad_orders = _int_value(ad_total.get(f"{key}_orders"))
        fallback_orders = _int_value(order_counts.get(key))
        periods[key] = {
            "label": label,
            "orders": ad_orders or fallback_orders,
            "ad_spend_usd": _float_value(ad_total.get(f"{key}_spend")),
            "roas": _float_or_none(ad_total.get(f"{key}_roas")),
        }
    return periods


def _build_markets(ad_report: dict[str, Any]) -> list[dict[str, Any]]:
    by_lang = {
        _normalize_lang(lang): row
        for lang, row in (ad_report.get("by_lang") or {}).items()
        if _normalize_lang(lang)
    }
    ordered_langs = [lang for lang, _countries, _label in _MARKET_GROUPS]
    for lang in by_lang:
        if lang not in ordered_langs:
            ordered_langs.append(lang)

    markets: list[dict[str, Any]] = []
    for lang in ordered_langs:
        row = by_lang.get(lang) or {}
        status = _market_delivery_status(row)
        markets.append({
            "lang": lang,
            "label": _MARKET_LABELS.get(lang, lang.upper()),
            "countries": _MARKET_COUNTRIES.get(lang, []),
            "delivery_status": status,
            "delivery_label": _delivery_label(status),
            "orders": {
                "today": _int_value(row.get("today_orders")),
                "yesterday": _int_value(row.get("yesterday_orders")),
                "last_7d": _int_value(row.get("last_7d_orders")),
                "last_30d": _int_value(row.get("last_30d_orders")),
            },
            "ad_spend_usd": _float_value(row.get("total_spend")),
            "today_spend_usd": _float_value(row.get("today_spend")),
            "last_7d_spend_usd": _float_value(row.get("last_7d_spend")),
            "ad_roas": _float_or_none(row.get("total_roas")),
            "last_7d_ad_roas": _float_or_none(row.get("last_7d_roas")),
        })
    return markets


def _build_data_quality(
    *,
    today: date,
    matched: bool,
    product: dict[str, Any] | None,
    ad_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if not matched:
        checks.append({
            "code": "product_match",
            "status": dq.STATUS_WARNING,
            "message": "未根据店小秘页面线索匹配到素材库产品",
        })
    elif product and product.get("match_confidence") == "low":
        checks.append({
            "code": "low_confidence_match",
            "status": dq.STATUS_WARNING,
            "message": "当前产品仅通过商品名兜底匹配，建议人工确认",
        })
    if matched and not ad_summary:
        checks.append({
            "code": "ad_status_cache_missing",
            "status": dq.STATUS_WARNING,
            "message": "产品投放状态缓存缺失，投放状态可能显示为默认值",
        })
    return dq.build_data_quality(
        business_date_from=today - timedelta(days=6),
        business_date_to=today,
        source_mode=dq.SOURCE_MODE_DERIVED_CACHE if matched else dq.SOURCE_MODE_UNKNOWN,
        checks=checks,
    )


def build_insights_response(raw_clues: dict[str, Any]) -> dict[str, Any]:
    clues = normalize_clues(raw_clues)
    match = resolve_product(clues)
    today = current_meta_business_date()
    if not match:
        periods = _build_periods({}, {})
        return {
            "ok": True,
            "matched": False,
            "query": clues,
            "product": None,
            "summary": {
                "delivery_status": media_product_ad_status_cache.STATUS_NEVER,
                "delivery_label": _delivery_label(media_product_ad_status_cache.STATUS_NEVER),
                "orders": {"today": 0, "yesterday": 0, "last_7d": 0, "last_30d": 0},
                "total_orders": 0,
                "true_roas": None,
                "ad_spend_usd": 0.0,
                "total_revenue_usd": 0.0,
                "periods": periods,
                "computed_at": None,
            },
            "markets": _build_markets({}),
            "data_quality": _build_data_quality(
                today=today,
                matched=False,
                product=None,
                ad_summary=None,
            ),
        }

    product = _product_payload(match["row"], match)
    product_id = int(product["id"])
    ad_summary = media_product_ad_status_cache.get_product_ad_summary_cache([product_id]).get(product_id, {})
    order_stats = media_product_order_stats.get_product_order_stats([product_id], today=today).get(product_id, {})
    ad_report = media_product_ad_orders_report.get_product_ad_orders_report(product_id, today=today)

    total_orders = (order_stats.get("total") or {}) if isinstance(order_stats, dict) else {}
    ad_total = (ad_report.get("total") or {}) if isinstance(ad_report, dict) else {}
    periods = _build_periods(ad_total, total_orders)
    status = media_product_ad_status_cache.normalize_delivery_status_filter(
        ad_summary.get("delivery_status")
    )
    if status == media_product_ad_status_cache.STATUS_ALL:
        status = media_product_ad_status_cache.STATUS_NEVER

    summary = {
        "delivery_status": status,
        "delivery_label": _delivery_label(status),
        "is_active": status == media_product_ad_status_cache.STATUS_ACTIVE,
        "orders": {
            "today": _int_value(total_orders.get("today")),
            "yesterday": _int_value(total_orders.get("yesterday")),
            "last_7d": _int_value(total_orders.get("last_7d")),
            "last_30d": _int_value(total_orders.get("last_30d")),
        },
        "total_orders": _int_value(ad_total.get("total_orders")) or _int_value(total_orders.get("last_30d")),
        "true_roas": _first_float_or_none(ad_total.get("total_roas"), ad_summary.get("overall_roas")),
        "ad_spend_usd": _first_float_value(ad_total.get("total_spend"), ad_summary.get("ad_spend_usd")),
        "active_spend_usd": _float_value(ad_summary.get("active_7d_ad_spend_usd")),
        "order_revenue_usd": _float_value(ad_summary.get("order_revenue_usd")),
        "shipping_revenue_usd": _float_value(ad_summary.get("shipping_revenue_usd")),
        "total_revenue_usd": _float_value(ad_summary.get("total_revenue_usd")),
        "periods": periods,
        "delivery_start_time": _iso(ad_summary.get("delivery_start_time")),
        "delivery_end_time": _iso(ad_summary.get("delivery_end_time")),
        "active_days": _int_value(ad_summary.get("active_days")),
        "computed_at": _iso(ad_summary.get("computed_at")),
    }

    return {
        "ok": True,
        "matched": True,
        "query": clues,
        "product": product,
        "summary": summary,
        "markets": _build_markets(ad_report if isinstance(ad_report, dict) else {}),
        "ad_orders_report": {
            "computed_at": (ad_report or {}).get("computed_at") if isinstance(ad_report, dict) else None,
        },
        "data_quality": _build_data_quality(
            today=today,
            matched=True,
            product=product,
            ad_summary=ad_summary,
        ),
    }
