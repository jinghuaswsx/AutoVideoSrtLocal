"""AI素材军师项目服务。

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from appcore import db, llm_client

log = logging.getLogger(__name__)

RANK_USE_CASE = "medias.ai_material_strategist_rank_products"
PRODUCT_ANALYSIS_USE_CASE = "medias.ai_material_strategist_product_analysis"
PROVIDER_CODE = "google_wj"
MODEL_ID = "gemini-3.5-flash"

_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.IGNORECASE)
_MAX_AI_CANDIDATES = 60
_PROJECT_TOP_N = 20

TARGET_COUNTRIES: tuple[dict[str, str], ...] = (
    {"country_code": "DE", "country_name": "德国", "lang": "de", "lang_name": "德语", "tier": "tier_1"},
    {"country_code": "FR", "country_name": "法国", "lang": "fr", "lang_name": "法语", "tier": "tier_1"},
    {"country_code": "IT", "country_name": "意大利", "lang": "it", "lang_name": "意大利语", "tier": "tier_2"},
    {"country_code": "ES", "country_name": "西班牙", "lang": "es", "lang_name": "西班牙语", "tier": "tier_2"},
    {"country_code": "JP", "country_name": "日本", "lang": "ja", "lang_name": "日语", "tier": "tier_2"},
    {"country_code": "SE", "country_name": "瑞典", "lang": "sv", "lang_name": "瑞典语", "tier": "tier_3"},
    {"country_code": "NL", "country_name": "荷兰", "lang": "nl", "lang_name": "荷兰语", "tier": "tier_3"},
    {"country_code": "PT", "country_name": "葡萄牙", "lang": "pt", "lang_name": "葡萄牙语", "tier": "tier_3"},
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":"))


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _roas(numerator: Any, denominator: Any) -> float | None:
    denom = _safe_float(denominator)
    if denom <= 0:
        return None
    return round(_safe_float(numerator) / denom, 4)


def strip_rjc(product_code: str | None) -> str:
    """去掉本地产品 code 末尾的 -rjc / _rjc，用于匹配明空快照。"""
    return _RJC_SUFFIX_RE.sub("", str(product_code or "").strip()).strip()


def _current_meta_business_date(now: datetime | None = None) -> date:
    current = now or datetime.now()
    if current.hour < 16:
        return current.date() - timedelta(days=1)
    return current.date()


def _placeholders(values: Iterable[Any]) -> str:
    return ",".join(["%s"] * len(list(values)))


def _chunked(items: list[dict], size: int) -> Iterable[list[dict]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _query_one_safe(sql: str, args: tuple[Any, ...] = ()) -> dict | None:
    try:
        return db.query_one(sql, args)
    except Exception:
        log.exception("AI material strategist data quality query failed")
        return None


def _data_quality() -> dict[str, Any]:
    daily = _query_one_safe(
        "SELECT MAX(DATE(COALESCE(meta_business_date, report_date))) AS value "
        "FROM meta_ad_daily_campaign_metrics"
    )
    realtime = _query_one_safe(
        "SELECT MAX(snapshot_at) AS value FROM meta_ad_realtime_daily_campaign_metrics"
    )
    orders = _query_one_safe(
        "SELECT MAX(updated_at) AS value FROM dianxiaomi_order_lines"
    )
    mk = _query_one_safe(
        "SELECT MAX(snapshot_at) AS value FROM mingkong_material_daily_snapshots"
    )
    return {
        "meta_daily_max_business_date": _iso((daily or {}).get("value")),
        "meta_realtime_max_snapshot_at": _iso((realtime or {}).get("value")),
        "orders_max_updated_at": _iso((orders or {}).get("value")),
        "mingkong_max_snapshot_at": _iso((mk or {}).get("value")),
    }


def _load_products() -> list[dict]:
    return db.query(
        """
        SELECT
          p.id, p.user_id, p.name, p.product_code, p.source, p.ai_score,
          p.ai_evaluation_result, p.ai_evaluation_detail, p.created_at,
          c.delivery_status, c.ad_spend_usd AS cached_ad_spend_usd,
          c.active_7d_ad_spend_usd AS cached_active_7d_ad_spend_usd,
          c.overall_roas AS cached_overall_roas
        FROM media_products p
        LEFT JOIN media_product_ad_summary_cache c ON c.product_id = p.id
        WHERE p.deleted_at IS NULL
          AND COALESCE(p.archived, 0) = 0
        ORDER BY p.id DESC
        """
    )


def _load_ad_rows(date_from: date, current_day: date) -> list[dict]:
    rows: list[dict] = []
    daily_to = current_day - timedelta(days=1)
    if daily_to >= date_from:
        rows.extend(db.query(
            """
            SELECT
              product_id,
              DATE(COALESCE(meta_business_date, report_date)) AS business_date,
              SUM(COALESCE(spend_usd, 0)) AS spend_usd,
              SUM(COALESCE(purchase_value_usd, 0)) AS purchase_value_usd,
              SUM(COALESCE(result_count, 0)) AS result_count,
              COUNT(DISTINCT NULLIF(campaign_name, '')) AS ad_count
            FROM meta_ad_daily_campaign_metrics
            WHERE product_id IS NOT NULL
              AND DATE(COALESCE(meta_business_date, report_date)) BETWEEN %s AND %s
            GROUP BY product_id, DATE(COALESCE(meta_business_date, report_date))
            """,
            (date_from, daily_to),
        ))

    rows.extend(db.query(
        """
        SELECT
          p.id AS product_id,
          m.business_date,
          SUM(COALESCE(m.spend_usd, 0)) AS spend_usd,
          SUM(COALESCE(m.purchase_value_usd, 0)) AS purchase_value_usd,
          SUM(COALESCE(m.result_count, 0)) AS result_count,
          COUNT(DISTINCT NULLIF(m.campaign_name, '')) AS ad_count
        FROM (
          SELECT m.*
          FROM (
            SELECT latest_day.business_date, latest_day.ad_account_id, MAX(rt.snapshot_at) AS max_snapshot_at
            FROM meta_ad_realtime_daily_campaign_metrics rt
            INNER JOIN (
              SELECT ad_account_id, MAX(business_date) AS business_date
              FROM meta_ad_realtime_daily_campaign_metrics
              WHERE data_completeness = 'realtime_partial'
                AND business_date BETWEEN %s AND %s
              GROUP BY ad_account_id
            ) latest_day
              ON rt.business_date = latest_day.business_date
             AND (rt.ad_account_id <=> latest_day.ad_account_id)
            WHERE rt.data_completeness = 'realtime_partial'
            GROUP BY latest_day.business_date, latest_day.ad_account_id
          ) latest
          STRAIGHT_JOIN meta_ad_realtime_daily_campaign_metrics m
            ON m.business_date = latest.business_date
           AND (m.ad_account_id <=> latest.ad_account_id)
           AND m.snapshot_at = latest.max_snapshot_at
          WHERE m.data_completeness = 'realtime_partial'
        ) m
        JOIN media_products p
          ON p.deleted_at IS NULL
         AND p.product_code IS NOT NULL
         AND p.product_code <> ''
         AND (
           LOWER(COALESCE(m.normalized_campaign_code, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
           OR LOWER(COALESCE(m.campaign_name, '')) LIKE CONCAT(LOWER(p.product_code), '%%')
         )
        GROUP BY p.id, m.business_date
        """,
        (date_from, current_day),
    ))
    return rows


def _load_order_rows(date_from: date, current_day: date) -> list[dict]:
    return db.query(
        """
        SELECT
          op.product_id,
          op.business_date,
          COUNT(DISTINCT d.dxm_package_id) AS order_count,
          SUM(COALESCE(
              op.revenue_usd,
              COALESCE(op.line_amount_usd, d.line_amount, 0)
              + COALESCE(op.shipping_allocated_usd, d.ship_amount, 0)
          )) AS revenue_usd,
          SUM(COALESCE(op.profit_usd, 0)) AS profit_usd,
          SUM(COALESCE(op.ad_cost_usd, 0)) AS attributed_ad_cost_usd
        FROM order_profit_lines op
        JOIN dianxiaomi_order_lines d ON d.id = op.dxm_order_line_id
        WHERE op.product_id IS NOT NULL
          AND op.business_date BETWEEN %s AND %s
        GROUP BY op.product_id, op.business_date
        """,
        (date_from, current_day),
    )


def _load_local_counts() -> dict[int, dict[str, Any]]:
    rows = db.query(
        """
        SELECT product_id, LOWER(COALESCE(lang, 'en')) AS lang, COUNT(*) AS item_count
        FROM media_items
        WHERE deleted_at IS NULL
        GROUP BY product_id, LOWER(COALESCE(lang, 'en'))
        """
    )
    out: dict[int, dict[str, Any]] = defaultdict(lambda: {"item_count": 0, "langs": {}})
    for row in rows:
        pid = _safe_int(row.get("product_id"))
        lang = str(row.get("lang") or "en").strip().lower()
        count = _safe_int(row.get("item_count"))
        out[pid]["item_count"] += count
        out[pid]["langs"][lang] = count
    return dict(out)


def _add_window_metrics(target: dict, row: Mapping[str, Any], current_day: date, prefix: str) -> None:
    business_date = _to_date(row.get("business_date"))
    if business_date is None:
        return
    windows = {
        "today": current_day,
        "yesterday": current_day - timedelta(days=1),
        "7d": current_day - timedelta(days=6),
        "30d": current_day - timedelta(days=29),
    }
    if business_date == windows["today"]:
        suffixes = ("today", "7d", "30d")
    elif business_date == windows["yesterday"]:
        suffixes = ("yesterday", "7d", "30d")
    elif business_date >= windows["7d"]:
        suffixes = ("7d", "30d")
    elif business_date >= windows["30d"]:
        suffixes = ("30d",)
    else:
        return
    if prefix == "ad":
        mapping = {
            "spend": "spend_usd",
            "purchase_value": "purchase_value_usd",
            "results": "result_count",
            "ad_count": "ad_count",
        }
    else:
        mapping = {
            "orders": "order_count",
            "revenue": "revenue_usd",
            "profit": "profit_usd",
            "attributed_ad_cost": "attributed_ad_cost_usd",
        }
    for suffix in suffixes:
        for key, source_key in mapping.items():
            target[f"{key}_{suffix}"] = target.get(f"{key}_{suffix}", 0.0) + _safe_float(row.get(source_key))


def _empty_metrics() -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for suffix in ("today", "yesterday", "7d", "30d"):
        for key in (
            "spend", "purchase_value", "results", "ad_count",
            "orders", "revenue", "profit", "attributed_ad_cost",
        ):
            metrics[f"{key}_{suffix}"] = 0.0
    return metrics


def build_data_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """读取当前素材、广告、订单和明空数据，返回一次项目运行的输入快照。"""
    current_day = _current_meta_business_date(now)
    date_from = current_day - timedelta(days=29)
    products = _load_products()
    metrics_by_product: dict[int, dict[str, Any]] = defaultdict(_empty_metrics)
    for row in _load_ad_rows(date_from, current_day):
        _add_window_metrics(metrics_by_product[_safe_int(row.get("product_id"))], row, current_day, "ad")
    for row in _load_order_rows(date_from, current_day):
        _add_window_metrics(metrics_by_product[_safe_int(row.get("product_id"))], row, current_day, "order")
    local_counts = _load_local_counts()

    product_rows: list[dict[str, Any]] = []
    for product in products:
        pid = _safe_int(product.get("id"))
        metrics = dict(metrics_by_product.get(pid) or _empty_metrics())
        metrics["true_roas_30d"] = _roas(metrics.get("revenue_30d"), metrics.get("spend_30d"))
        metrics["meta_roas_30d"] = _roas(metrics.get("purchase_value_30d"), metrics.get("spend_30d"))
        metrics["profit_margin_30d"] = _roas(metrics.get("profit_30d"), metrics.get("revenue_30d"))
        local = local_counts.get(pid) or {"item_count": 0, "langs": {}}
        row = {
            "product_id": pid,
            "product_name": product.get("name") or "",
            "product_code": product.get("product_code") or "",
            "user_id": product.get("user_id"),
            "source": product.get("source") or "",
            "ai_score": _safe_float(product.get("ai_score")),
            "ai_evaluation_result": product.get("ai_evaluation_result") or "",
            "delivery_status": product.get("delivery_status") or "never",
            "cached_overall_roas": _safe_float(product.get("cached_overall_roas")),
            "cached_ad_spend_usd": _safe_float(product.get("cached_ad_spend_usd")),
            "cached_active_7d_ad_spend_usd": _safe_float(product.get("cached_active_7d_ad_spend_usd")),
            "local_material_count": _safe_int(local.get("item_count")),
            "local_material_langs": dict(local.get("langs") or {}),
            **metrics,
        }
        product_rows.append(row)

    return {
        "generated_at": datetime.now().isoformat(sep=" "),
        "window": {
            "current_meta_business_date": current_day.isoformat(),
            "yesterday": (current_day - timedelta(days=1)).isoformat(),
            "last_7d_from": (current_day - timedelta(days=6)).isoformat(),
            "last_30d_from": date_from.isoformat(),
        },
        "data_quality": _data_quality(),
        "products": product_rows,
    }


def _selection_reasons(row: Mapping[str, Any]) -> list[str]:
    reasons = []
    if _safe_float(row.get("spend_30d")) >= 50:
        reasons.append("30天消耗有量")
    if _safe_float(row.get("orders_30d")) >= 8:
        reasons.append("30天订单有量")
    if _safe_float(row.get("spend_7d")) >= 25:
        reasons.append("近7天仍有消耗")
    if _safe_float(row.get("spend_yesterday")) >= 10:
        reasons.append("昨天仍在投放")
    roas = row.get("true_roas_30d")
    if roas is not None and _safe_float(roas) >= 1.5:
        reasons.append("真实ROAS较好")
    if _safe_float(row.get("local_material_count")) <= 2:
        reasons.append("本地素材可补空间大")
    return reasons


def _score_product(row: Mapping[str, Any]) -> float:
    spend30 = _safe_float(row.get("spend_30d"))
    orders30 = _safe_float(row.get("orders_30d"))
    results30 = _safe_float(row.get("results_30d"))
    adcnt30 = _safe_float(row.get("ad_count_30d"))
    spend7 = _safe_float(row.get("spend_7d"))
    spend_y = _safe_float(row.get("spend_yesterday"))
    spend_t = _safe_float(row.get("spend_today"))
    orders7 = _safe_float(row.get("orders_7d"))
    true_roas = _safe_float(row.get("true_roas_30d"))
    meta_roas = _safe_float(row.get("meta_roas_30d"))
    profit30 = _safe_float(row.get("profit_30d"))

    volume = 2.2 * math.log1p(spend30) + 3.0 * math.log1p(orders30)
    volume += 0.9 * math.log1p(results30) + 0.8 * math.log1p(adcnt30)
    efficiency = min(true_roas, 4.0) * 4.0 + min(meta_roas, 4.0) * 2.0
    freshness = min(spend7 / 250.0, 1.5) * 4.0
    freshness += min(spend_y / 80.0, 1.5) * 3.0
    freshness += min(spend_t / 80.0, 1.2) * 2.0
    freshness += min(orders7 / 20.0, 2.0) * 3.0
    profit_bonus = _clamp(profit30 / 500.0, -4.0, 3.0)
    return round(volume + efficiency + freshness + profit_bonus, 4)


def score_product_rows(rows: list[dict], limit: int = _MAX_AI_CANDIDATES) -> list[dict]:
    candidates: list[dict] = []
    for row in rows:
        if not (
            _safe_float(row.get("spend_30d")) >= 50
            or _safe_float(row.get("orders_30d")) >= 8
            or _safe_float(row.get("spend_7d")) >= 25
            or _safe_float(row.get("spend_yesterday")) >= 10
        ):
            continue
        scored = dict(row)
        scored["score"] = _score_product(row)
        scored["selection_reasons"] = _selection_reasons(row)
        candidates.append(scored)
    candidates.sort(
        key=lambda item: (
            -_safe_float(item.get("score")),
            -_safe_float(item.get("spend_30d")),
            -_safe_float(item.get("orders_30d")),
            str(item.get("product_code") or ""),
        )
    )
    return candidates[:limit]


def _llm_json(result: dict) -> dict:
    if isinstance(result.get("json"), dict):
        return result["json"]
    text = str(result.get("text") or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return _json_loads(text, {})


def _rank_input(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "product_id", "product_code", "product_name", "score", "selection_reasons",
        "spend_today", "spend_yesterday", "spend_7d", "spend_30d",
        "orders_today", "orders_yesterday", "orders_7d", "orders_30d",
        "revenue_30d", "profit_30d", "true_roas_30d", "meta_roas_30d",
        "ad_count_30d", "results_30d", "local_material_count", "local_material_langs",
        "delivery_status",
    )
    return {key: row.get(key) for key in keys}


RANKING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranked_products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "product_code": {"type": "string"},
                    "rank": {"type": "integer"},
                    "score": {"type": "number"},
                    "volume_reason": {"type": "string"},
                    "efficiency_reason": {"type": "string"},
                    "risk_reason": {"type": "string"},
                    "why_selected": {"type": "string"},
                },
                "required": ["product_id", "rank", "score", "why_selected"],
            },
        },
        "rejected_high_roas_low_volume": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ranked_products"],
}


PRODUCT_ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_id": {"type": "integer"},
        "product_code": {"type": "string"},
        "overall_judgement": {"type": "string"},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
        "primary_action": {
            "type": "string",
            "enum": ["expand_country", "same_country_new_material", "weak_country_retest", "hold", "investigate"],
        },
        "country_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "country_code": {"type": "string"},
                    "lang": {"type": "string"},
                    "action": {"type": "string"},
                    "priority": {"type": "string"},
                    "reason": {"type": "string"},
                    "material_key": {"type": "string"},
                    "video_path": {"type": "string"},
                },
                "required": ["country_code", "lang", "action", "priority", "reason"],
            },
        },
        "material_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "material_key": {"type": "string"},
                    "video_path": {"type": "string"},
                    "target_langs": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["action", "reason"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "next_check": {"type": "string"},
    },
    "required": ["product_id", "product_code", "overall_judgement", "priority", "primary_action"],
}


def _ranking_prompt(payload: dict) -> str:
    return (
        "你是跨境电商素材投放军师。只根据输入 JSON 判断，不编造数据。\n"
        "表现好必须同时有量和效率；1-2 单高 ROAS 不得排前。优先选择有持续消耗、订单、广告数、"
        "并且有可补素材空间的产品。输出严格 JSON，字段符合 response_schema。\n"
        f"输入数据：\n{_json_dumps(payload)}"
    )


def _product_prompt(payload: dict) -> str:
    return (
        "你是跨境电商 AI素材军师。只分析当前一个产品，不编造输入中没有的数据。\n"
        "请先判断产品阶段，再给补素材操作建议。建议必须落到国家、语言、素材或明空 video_path；"
        "如果数据不足，明确缺什么。输出严格 JSON，字段符合 response_schema。\n"
        f"当前产品数据：\n{_json_dumps(payload)}"
    )


def _fallback_ranking(candidates: list[dict], error: str | None = None) -> dict[str, Any]:
    top = candidates[:_PROJECT_TOP_N]
    return {
        "selected_product_ids": [_safe_int(item.get("product_id")) for item in top],
        "ranking_result": {
            "mode": "deterministic_fallback",
            "error": error or "",
            "ranked_products": [
                {
                    "product_id": item.get("product_id"),
                    "product_code": item.get("product_code"),
                    "rank": index + 1,
                    "score": item.get("score"),
                    "why_selected": " / ".join(item.get("selection_reasons") or []) or "规则打分靠前",
                }
                for index, item in enumerate(top)
            ],
        },
        "prompt_debug": {"mode": "deterministic_fallback"},
    }


def _run_ai_ranking(candidates: list[dict], *, project_id: int, user_id: int | None, run_ai: bool) -> dict[str, Any]:
    if not run_ai:
        return _fallback_ranking(candidates)
    try:
        batch_results: list[dict[str, Any]] = []
        merged_candidates: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(_chunked(candidates, 20), start=1):
            payload = {
                "batch_index": batch_index,
                "rule": "本批最多输出 Top10，剔除高ROAS低量产品。",
                "products": [_rank_input(row) for row in batch],
            }
            result = llm_client.invoke_generate(
                RANK_USE_CASE,
                prompt=_ranking_prompt(payload),
                user_id=user_id,
                project_id=str(project_id),
                response_schema=RANKING_RESPONSE_SCHEMA,
                temperature=0.15,
                max_output_tokens=4096,
                provider_override=PROVIDER_CODE,
                model_override=MODEL_ID,
                billing_extra={"stage": "batch_rank", "batch_index": batch_index},
                timeout_seconds=180,
            )
            parsed = _llm_json(result)
            batch_results.append({"input": payload, "output": parsed})
            ids = {_safe_int(item.get("product_id")) for item in parsed.get("ranked_products") or []}
            by_id = {_safe_int(item.get("product_id")): item for item in batch}
            merged_candidates.extend(by_id[pid] for pid in ids if pid in by_id)

        if not merged_candidates:
            return _fallback_ranking(candidates, "AI batch ranking returned empty")
        merged_candidates = score_product_rows(merged_candidates, limit=len(merged_candidates))
        final_payload = {
            "rule": "从所有批次候选里输出最终 Top20，仍然坚持有量 + 效率。",
            "products": [_rank_input(row) for row in merged_candidates],
        }
        final = llm_client.invoke_generate(
            RANK_USE_CASE,
            prompt=_ranking_prompt(final_payload),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=RANKING_RESPONSE_SCHEMA,
            temperature=0.1,
            max_output_tokens=4096,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={"stage": "final_rank"},
            timeout_seconds=180,
        )
        parsed_final = _llm_json(final)
        ordered_ids = [
            _safe_int(item.get("product_id"))
            for item in sorted(parsed_final.get("ranked_products") or [], key=lambda item: _safe_int(item.get("rank")))
        ]
        ordered_ids = [pid for pid in ordered_ids if pid]
        if not ordered_ids:
            return _fallback_ranking(candidates, "AI final ranking returned empty")
        return {
            "selected_product_ids": ordered_ids[:_PROJECT_TOP_N],
            "ranking_result": {
                "mode": "ai",
                "batch_results": batch_results,
                "final_input": final_payload,
                "final_output": parsed_final,
            },
            "prompt_debug": {
                "provider": PROVIDER_CODE,
                "model": MODEL_ID,
                "use_case": RANK_USE_CASE,
                "batch_count": len(batch_results),
            },
        }
    except Exception as exc:
        log.exception("AI material strategist ranking failed")
        return _fallback_ranking(candidates, str(exc))


def _load_country_summaries(product_ids: list[int]) -> dict[int, list[dict]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = db.query(
        f"""
        SELECT product_id, LOWER(lang) AS lang, item_count, pushed_video_count,
               ad_spend_usd, purchase_value_usd, ad_roas, active_7d_ad_spend_usd
        FROM media_product_lang_ad_summary_cache
        WHERE product_id IN ({placeholders})
        """,
        tuple(product_ids),
    )
    by_product: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        pid = _safe_int(row.get("product_id"))
        lang = str(row.get("lang") or "").lower()
        by_product[pid][lang] = {
            "lang": lang,
            "item_count": _safe_int(row.get("item_count")),
            "pushed_video_count": _safe_int(row.get("pushed_video_count")),
            "ad_spend_usd": _safe_float(row.get("ad_spend_usd")),
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": row.get("ad_roas") if row.get("ad_roas") is not None else None,
            "active_7d_ad_spend_usd": _safe_float(row.get("active_7d_ad_spend_usd")),
        }

    out: dict[int, list[dict]] = {}
    for pid in product_ids:
        items = []
        existing = by_product.get(pid) or {}
        for country in TARGET_COUNTRIES:
            lang = country["lang"]
            row = dict(existing.get(lang) or {})
            row.update(country)
            row.setdefault("item_count", 0)
            row.setdefault("pushed_video_count", 0)
            row.setdefault("ad_spend_usd", 0.0)
            row.setdefault("purchase_value_usd", 0.0)
            row.setdefault("ad_roas", None)
            row.setdefault("active_7d_ad_spend_usd", 0.0)
            row["delivery_status"] = (
                "active" if _safe_float(row["active_7d_ad_spend_usd"]) > 0
                else "stopped" if _safe_float(row["ad_spend_usd"]) > 0
                else "never"
            )
            items.append(row)
        out[pid] = items
    return out


def _local_video_url(object_key: str) -> str:
    return f"/medias/object?object_key={quote(object_key, safe='')}"


def _load_local_materials(product_ids: list[int]) -> dict[int, list[dict]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = db.query(
        f"""
        SELECT
          i.id, i.product_id, LOWER(COALESCE(i.lang, 'en')) AS lang,
          i.filename, i.display_name, i.object_key, i.task_id,
          i.duration_seconds, i.created_at,
          b.mk_video_path,
          COUNT(DISTINCT CASE WHEN l.status = 'success' THEN l.id END) AS push_count
        FROM media_items i
        LEFT JOIN media_item_mk_bindings b ON b.media_item_id = i.id
        LEFT JOIN media_push_logs l ON l.item_id = i.id
        WHERE i.deleted_at IS NULL
          AND i.product_id IN ({placeholders})
        GROUP BY i.id, i.product_id, i.lang, i.filename, i.display_name,
                 i.object_key, i.task_id, i.duration_seconds, i.created_at, b.mk_video_path
        ORDER BY i.product_id, i.created_at DESC, i.id DESC
        """,
        tuple(product_ids),
    )
    out: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        object_key = str(row.get("object_key") or "").strip()
        item = {
            "id": _safe_int(row.get("id")),
            "product_id": _safe_int(row.get("product_id")),
            "lang": row.get("lang") or "en",
            "filename": row.get("filename") or "",
            "display_name": row.get("display_name") or row.get("filename") or "",
            "object_key": object_key,
            "task_id": row.get("task_id") or "",
            "duration_seconds": _safe_float(row.get("duration_seconds")),
            "created_at": _iso(row.get("created_at")),
            "mk_video_path": row.get("mk_video_path") or "",
            "push_count": _safe_int(row.get("push_count")),
            "video_url": _local_video_url(object_key) if object_key else "",
        }
        out[item["product_id"]].append(item)
    return dict(out)


def _mk_search_codes(product_codes: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for code in product_codes:
        raw = str(code or "").strip().lower()
        stripped = strip_rjc(raw).lower()
        terms = []
        if stripped:
            terms.append(stripped)
        if raw and raw not in terms:
            terms.append(raw)
        if terms:
            out[raw] = terms
    return out


def _mk_video_url(video_path: str) -> str:
    return f"/medias/api/mk-video?path={quote(video_path, safe='')}"


def _mk_import_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    meta = _json_loads(row.get("mk_video_metadata_json"), {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("product_code", row.get("product_code") or "")
    meta.setdefault("product_name", row.get("product_name") or "")
    meta.setdefault("product_link", row.get("product_url") or row.get("mk_product_link") or "")
    meta.setdefault("video_name", row.get("video_name") or "")
    meta.setdefault("video_path", row.get("video_path") or "")
    meta.setdefault("cover_path", row.get("video_image_path") or "")
    meta.setdefault("video_image_path", row.get("video_image_path") or "")
    meta.setdefault("spends", str(row.get("cumulative_90_spend") or ""))
    meta.setdefault("ads_count", row.get("video_ads_count") or 0)
    return meta


def _load_mingkong_materials(product_codes: list[str], per_product_limit: int = 6) -> dict[str, list[dict]]:
    search_map = _mk_search_codes(product_codes)
    terms = sorted({term for values in search_map.values() for term in values})
    if not terms:
        return {}
    placeholders = ",".join(["%s"] * len(terms))
    rows = db.query(
        f"""
        SELECT
          s.material_key, s.product_code, s.product_name, s.product_url,
          s.mk_product_link, s.video_name, s.video_path, s.video_image_path,
          s.cumulative_90_spend, s.video_ads_count, s.video_author,
          s.video_upload_time, s.video_duration_seconds, s.mk_video_metadata_json,
          COALESCE(t.yesterday_spend_delta, 0) AS yesterday_spend_delta,
          t.display_position AS top100_display_position,
          s.snapshot_at
        FROM mingkong_material_daily_snapshots s
        JOIN mingkong_material_sync_runs r ON r.id = s.run_id AND r.status = 'success'
        JOIN (
          SELECT s2.material_key, MAX(s2.snapshot_at) AS latest_snapshot_at
          FROM mingkong_material_daily_snapshots s2
          JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id AND r2.status = 'success'
          WHERE LOWER(s2.product_code) IN ({placeholders})
          GROUP BY s2.material_key
        ) latest
          ON latest.material_key = s.material_key
         AND latest.latest_snapshot_at = s.snapshot_at
        LEFT JOIN mingkong_material_daily_top100 t
          ON t.material_key = s.material_key
         AND t.snapshot_at = s.snapshot_at
        ORDER BY s.product_code, s.cumulative_90_spend DESC, s.video_ads_count DESC
        """,
        tuple(terms),
    )
    reverse: dict[str, str] = {}
    for local_code, values in search_map.items():
        for term in values:
            reverse[term] = local_code
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        local_code = reverse.get(str(row.get("product_code") or "").strip().lower())
        if not local_code or len(grouped[local_code]) >= per_product_limit:
            continue
        video_path = str(row.get("video_path") or "").strip()
        material = {
            "material_key": row.get("material_key") or "",
            "product_code": row.get("product_code") or "",
            "product_name": row.get("product_name") or "",
            "product_url": row.get("product_url") or "",
            "video_name": row.get("video_name") or "",
            "video_path": video_path,
            "video_image_path": row.get("video_image_path") or "",
            "video_url": _mk_video_url(video_path) if video_path else "",
            "cumulative_90_spend": _safe_float(row.get("cumulative_90_spend")),
            "video_ads_count": _safe_int(row.get("video_ads_count")),
            "yesterday_spend_delta": _safe_float(row.get("yesterday_spend_delta")),
            "top100_display_position": row.get("top100_display_position"),
            "snapshot_at": _iso(row.get("snapshot_at")),
            "mk_video_metadata": _mk_import_metadata(row),
        }
        grouped[local_code].append(material)
    return dict(grouped)


def _fallback_product_analysis(product: Mapping[str, Any], countries: list[dict], mk_materials: list[dict]) -> dict:
    active = [c for c in countries if _safe_float(c.get("active_7d_ad_spend_usd")) > 0]
    strong = [c for c in countries if _safe_float(c.get("ad_spend_usd")) >= 30 and _safe_float(c.get("ad_roas")) >= 1.5]
    never = [c for c in countries if c.get("delivery_status") == "never"]
    weak = [c for c in countries if c.get("delivery_status") == "stopped"]
    spend30 = _safe_float(product.get("spend_30d"))
    orders30 = _safe_float(product.get("orders_30d"))
    true_roas = _safe_float(product.get("true_roas_30d"))

    if spend30 >= 300 and orders30 >= 30 and true_roas >= 1.5:
        priority = "P0"
    elif spend30 >= 100 or orders30 >= 15:
        priority = "P1"
    elif spend30 >= 50 or orders30 >= 8:
        priority = "P2"
    else:
        priority = "P3"

    if strong and never:
        primary_action = "expand_country"
        judgement = "已有国家跑出量和效率，优先把同素材扩到未验证国家。"
    elif active and mk_materials:
        primary_action = "same_country_new_material"
        judgement = "当前仍有投放消耗，可在已跑国家补新明空素材继续测。"
    elif weak and mk_materials:
        primary_action = "weak_country_retest"
        judgement = "部分国家历史投放弱，可用新素材做二次确认。"
    else:
        primary_action = "investigate"
        judgement = "数据量或素材线索不足，先检查广告命名、订单归因和素材绑定。"

    picked_material = mk_materials[0] if mk_materials else {}
    country_actions = []
    for country in (never[:3] or weak[:2] or active[:2]):
        country_actions.append({
            "country_code": country.get("country_code"),
            "lang": country.get("lang"),
            "action": primary_action,
            "priority": priority,
            "reason": judgement,
            "material_key": picked_material.get("material_key", ""),
            "video_path": picked_material.get("video_path", ""),
        })
    material_actions = []
    if picked_material:
        target_langs = [item.get("lang") for item in country_actions if item.get("lang")]
        material_actions.append({
            "action": "import_or_translate",
            "material_key": picked_material.get("material_key", ""),
            "video_path": picked_material.get("video_path", ""),
            "target_langs": target_langs,
            "reason": "明空素材90天消耗/广告数靠前，适合作为补素材候选。",
        })
    return {
        "product_id": product.get("product_id"),
        "product_code": product.get("product_code"),
        "overall_judgement": judgement,
        "priority": priority,
        "primary_action": primary_action,
        "country_actions": country_actions,
        "material_actions": material_actions,
        "risks": [] if true_roas >= 1 else ["真实ROAS偏低或利润为负，需要小预算验证。"],
        "next_check": "补素材后观察 24-48 小时消耗、订单和国家 ROAS。",
        "mode": "deterministic_fallback",
    }


def _run_product_analysis(
    product: dict,
    countries: list[dict],
    local_materials: list[dict],
    mk_materials: list[dict],
    *,
    project_id: int,
    user_id: int | None,
    run_ai: bool,
) -> dict:
    fallback = _fallback_product_analysis(product, countries, mk_materials)
    if not run_ai:
        return fallback
    payload = {
        "identity": {
            "product_id": product.get("product_id"),
            "product_code": product.get("product_code"),
            "product_name": product.get("product_name"),
        },
        "performance_windows": _rank_input(product),
        "country_summary": countries,
        "local_materials": local_materials[:20],
        "mingkong_material_candidates": mk_materials,
        "target_country_tiers": list(TARGET_COUNTRIES),
    }
    try:
        result = llm_client.invoke_generate(
            PRODUCT_ANALYSIS_USE_CASE,
            prompt=_product_prompt(payload),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=PRODUCT_ANALYSIS_RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=4096,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={"stage": "product_analysis", "product_id": product.get("product_id")},
            timeout_seconds=180,
        )
        parsed = _llm_json(result)
        if not parsed:
            fallback["ai_error"] = "empty model response"
            return fallback
        parsed.setdefault("mode", "ai")
        return parsed
    except Exception as exc:
        log.exception("AI material strategist product analysis failed product_id=%s", product.get("product_id"))
        fallback["ai_error"] = str(exc)
        return fallback


def _build_action_items(product: Mapping[str, Any], ai_result: Mapping[str, Any], mk_materials: list[dict]) -> list[dict]:
    pid = _safe_int(product.get("product_id"))
    code = str(product.get("product_code") or "").strip()
    actions: list[dict] = [
        {
            "type": "supplement_workbench",
            "label": "补素材工作台",
            "url": f"/medias/product/addvideo/{pid}",
        },
        {
            "type": "translation_tasks",
            "label": "翻译任务",
            "url": f"/medias/products/{pid}/translation-tasks",
        },
        {
            "type": "product_materials",
            "label": "素材库反馈",
            "url": f"/medias/{quote(code, safe='')}" if code else f"/medias/product/addvideo/{pid}",
        },
    ]
    for material in mk_materials[:3]:
        video_path = str(material.get("video_path") or "").strip()
        if video_path:
            actions.append({
                "type": "view_mk_video",
                "label": "看明空视频",
                "url": material.get("video_url") or _mk_video_url(video_path),
                "material_key": material.get("material_key"),
            })
        actions.append({
            "type": "import_mk_video",
            "label": "加入素材库",
            "url": "/mk-import/video",
            "method": "POST",
            "material_key": material.get("material_key"),
            "payload": {
                "mk_video_metadata": material.get("mk_video_metadata") or {},
                "product_owner_id": product.get("user_id"),
            },
        })
    for country in ai_result.get("country_actions") or []:
        lang = str(country.get("lang") or "").strip()
        if not lang:
            continue
        actions.append({
            "type": "create_translation_task",
            "label": f"创建{country.get('country_code') or lang}翻译任务",
            "url": f"/medias/product/addvideo/{pid}?target_lang={quote(lang)}",
            "target_lang": lang,
            "country_code": country.get("country_code"),
        })
    return actions


def _summarize_project(products: list[dict], ranking: dict, snapshot: dict) -> dict:
    priority_counts: dict[str, int] = defaultdict(int)
    action_counts: dict[str, int] = defaultdict(int)
    for item in products:
        ai = item.get("ai_result") or {}
        priority_counts[str(ai.get("priority") or "P3")] += 1
        action_counts[str(ai.get("primary_action") or "investigate")] += 1
    return {
        "top_product_count": len(products),
        "priority_counts": dict(priority_counts),
        "action_counts": dict(action_counts),
        "data_window": snapshot.get("window") or {},
        "data_quality": snapshot.get("data_quality") or {},
        "ranking_mode": (ranking.get("ranking_result") or {}).get("mode") or "unknown",
    }


def create_project_record(user_id: int | None, project_name: str | None = None) -> dict:
    name = (project_name or "").strip() or f"AI素材军师 {datetime.now():%Y-%m-%d %H:%M}"
    project_id = db.execute(
        """
        INSERT INTO ai_material_strategist_projects
          (project_name, status, user_id, provider_code, model_id, started_at)
        VALUES (%s, 'running', %s, %s, %s, NOW())
        """,
        (name, user_id, PROVIDER_CODE, MODEL_ID),
    )
    return get_project(project_id) or {"id": project_id, "project_name": name, "status": "running"}


def run_project(project_id: int, *, user_id: int | None = None, run_ai: bool = True) -> dict:
    try:
        snapshot = build_data_snapshot()
        candidates = score_product_rows(snapshot["products"], limit=_MAX_AI_CANDIDATES)
        ranking = _run_ai_ranking(candidates, project_id=project_id, user_id=user_id, run_ai=run_ai)
        candidate_by_id = {_safe_int(item.get("product_id")): item for item in candidates}
        selected = [candidate_by_id[pid] for pid in ranking["selected_product_ids"] if pid in candidate_by_id]
        if len(selected) < _PROJECT_TOP_N:
            seen = {_safe_int(item.get("product_id")) for item in selected}
            selected.extend(item for item in candidates if _safe_int(item.get("product_id")) not in seen)
            selected = selected[:_PROJECT_TOP_N]

        product_ids = [_safe_int(item.get("product_id")) for item in selected]
        countries_by_product = _load_country_summaries(product_ids)
        local_by_product = _load_local_materials(product_ids)
        mk_by_code = _load_mingkong_materials([str(item.get("product_code") or "") for item in selected])

        results: list[dict] = []
        for rank_no, product in enumerate(selected, start=1):
            code_key = str(product.get("product_code") or "").strip().lower()
            countries = countries_by_product.get(_safe_int(product.get("product_id"))) or []
            local_materials = local_by_product.get(_safe_int(product.get("product_id"))) or []
            mk_materials = mk_by_code.get(code_key) or []
            ai_result = _run_product_analysis(
                product,
                countries,
                local_materials,
                mk_materials,
                project_id=project_id,
                user_id=user_id,
                run_ai=run_ai,
            )
            action_items = _build_action_items(product, ai_result, mk_materials)
            results.append({
                "rank_no": rank_no,
                "product": product,
                "country_summary": countries,
                "local_materials": local_materials,
                "mingkong_materials": mk_materials,
                "ai_result": ai_result,
                "action_items": action_items,
            })

        db.execute("DELETE FROM ai_material_strategist_product_results WHERE project_id = %s", (project_id,))
        for item in results:
            product = item["product"]
            db.execute(
                """
                INSERT INTO ai_material_strategist_product_results
                  (project_id, rank_no, product_id, product_code, product_name, score,
                   metrics_json, country_summary_json, local_materials_json,
                   mingkong_materials_json, ai_result_json, action_items_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
                    item["rank_no"],
                    product.get("product_id"),
                    product.get("product_code") or "",
                    product.get("product_name") or "",
                    _safe_float(product.get("score")),
                    _json_dumps(product),
                    _json_dumps(item["country_summary"]),
                    _json_dumps(item["local_materials"]),
                    _json_dumps(item["mingkong_materials"]),
                    _json_dumps(item["ai_result"]),
                    _json_dumps(item["action_items"]),
                ),
            )

        summary = _summarize_project(results, ranking, snapshot)
        db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET status = 'success',
                data_window_json = %s,
                data_snapshot_json = %s,
                ranking_prompt_json = %s,
                ranking_result_json = %s,
                summary_json = %s,
                error_message = NULL,
                finished_at = NOW()
            WHERE id = %s
            """,
            (
                _json_dumps(snapshot.get("window") or {}),
                _json_dumps({
                    "generated_at": snapshot.get("generated_at"),
                    "data_quality": snapshot.get("data_quality"),
                    "candidate_count": len(candidates),
                    "product_count": len(snapshot.get("products") or []),
                    "top_candidate_inputs": [_rank_input(item) for item in candidates[:_MAX_AI_CANDIDATES]],
                }),
                _json_dumps(ranking.get("prompt_debug") or {}),
                _json_dumps(ranking.get("ranking_result") or {}),
                _json_dumps(summary),
                project_id,
            ),
        )
    except Exception as exc:
        log.exception("AI material strategist project failed project_id=%s", project_id)
        db.execute(
            """
            UPDATE ai_material_strategist_projects
            SET status = 'failed', error_message = %s, finished_at = NOW()
            WHERE id = %s
            """,
            (str(exc), project_id),
        )
    return get_project(project_id) or {"id": project_id, "status": "failed"}


def create_and_run_project(
    user_id: int | None,
    *,
    project_name: str | None = None,
    run_ai: bool = True,
) -> dict:
    project = create_project_record(user_id, project_name)
    return run_project(_safe_int(project.get("id")), user_id=user_id, run_ai=run_ai)


def list_projects(limit: int = 30) -> list[dict]:
    rows = db.query(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               summary_json, error_message, started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        ORDER BY id DESC
        LIMIT %s
        """,
        (max(1, min(limit, 100)),),
    )
    return [_serialize_project_row(row, include_products=False) for row in rows]


def get_project(project_id: int) -> dict | None:
    row = db.query_one(
        """
        SELECT id, project_name, status, user_id, provider_code, model_id,
               data_window_json, data_snapshot_json, ranking_prompt_json,
               ranking_result_json, summary_json, error_message,
               started_at, finished_at, created_at, updated_at
        FROM ai_material_strategist_projects
        WHERE id = %s
        """,
        (project_id,),
    )
    if not row:
        return None
    project = _serialize_project_row(row, include_products=True)
    product_rows = db.query(
        """
        SELECT id, project_id, rank_no, product_id, product_code, product_name, score,
               metrics_json, country_summary_json, local_materials_json,
               mingkong_materials_json, ai_result_json, action_items_json,
               created_at, updated_at
        FROM ai_material_strategist_product_results
        WHERE project_id = %s
        ORDER BY rank_no ASC, id ASC
        """,
        (project_id,),
    )
    project["products"] = [_serialize_product_result(row) for row in product_rows]
    return project


def _serialize_project_row(row: Mapping[str, Any], *, include_products: bool) -> dict:
    out = {
        "id": _safe_int(row.get("id")),
        "project_name": row.get("project_name") or "",
        "status": row.get("status") or "running",
        "user_id": row.get("user_id"),
        "provider_code": row.get("provider_code") or PROVIDER_CODE,
        "model_id": row.get("model_id") or MODEL_ID,
        "summary": _json_loads(row.get("summary_json"), {}) or {},
        "error_message": row.get("error_message") or "",
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if include_products:
        out.update({
            "data_window": _json_loads(row.get("data_window_json"), {}) or {},
            "data_snapshot": _json_loads(row.get("data_snapshot_json"), {}) or {},
            "ranking_prompt": _json_loads(row.get("ranking_prompt_json"), {}) or {},
            "ranking_result": _json_loads(row.get("ranking_result_json"), {}) or {},
        })
    return out


def _serialize_product_result(row: Mapping[str, Any]) -> dict:
    return {
        "id": _safe_int(row.get("id")),
        "project_id": _safe_int(row.get("project_id")),
        "rank_no": _safe_int(row.get("rank_no")),
        "product_id": _safe_int(row.get("product_id")),
        "product_code": row.get("product_code") or "",
        "product_name": row.get("product_name") or "",
        "score": _safe_float(row.get("score")),
        "metrics": _json_loads(row.get("metrics_json"), {}) or {},
        "country_summary": _json_loads(row.get("country_summary_json"), []) or [],
        "local_materials": _json_loads(row.get("local_materials_json"), []) or [],
        "mingkong_materials": _json_loads(row.get("mingkong_materials_json"), []) or [],
        "ai_result": _json_loads(row.get("ai_result_json"), {}) or {},
        "action_items": _json_loads(row.get("action_items_json"), []) or [],
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def build_preview() -> dict:
    snapshot = build_data_snapshot()
    candidates = score_product_rows(snapshot["products"], limit=_PROJECT_TOP_N)
    return {
        "window": snapshot.get("window") or {},
        "data_quality": snapshot.get("data_quality") or {},
        "product_count": len(snapshot.get("products") or []),
        "eligible_count": len(score_product_rows(snapshot["products"], limit=100000)),
        "top_candidates": [_rank_input(item) for item in candidates],
    }
