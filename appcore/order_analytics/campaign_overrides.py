"""campaign-product 人工配对兜底（plan 阶段 5 扩展）。

业务流程：
  1. 业务方在 /order-profit 看板看到 unallocated_ad_spend > 0
  2. 列出未匹配的 campaign（按 spend 降序）
  3. 对每个 campaign 选一个 product_id 做手工映射
  4. create_override() 写入 campaign_product_overrides 表 + 立即 UPDATE
     历史 meta_ad_daily_campaign_metrics（回填 product_id）
  5. 后续利润核算重跑就能把这些广告费分摊进 SKU 行
  6. meta_ads.resolve_ad_product_match 在自动匹配失败时优先查 override 表，
     避免新同步进来的数据再次未匹配
"""
from __future__ import annotations

import sys
from typing import Any


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def list_unmatched_campaigns(
    *,
    lookback_days: int = 90,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列出近 N 天未匹配 product_id 的 campaign（按 spend 降序）。"""
    rows = query(
        "SELECT normalized_campaign_code, "
        "       COUNT(*) AS days, "
        "       ROUND(SUM(spend_usd), 2) AS spend, "
        "       MAX(campaign_name) AS sample_campaign_name, "
        "       MAX(report_date) AS last_seen "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE product_id IS NULL "
        "  AND report_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY) "
        "GROUP BY normalized_campaign_code "
        "ORDER BY spend DESC "
        "LIMIT %s",
        (lookback_days, limit),
    )
    return list(rows or [])


def resolve_override(normalized_campaign_code: str) -> dict[str, Any] | None:
    """同步流程用：查 override 表，命中则返回 {id, product_code, name}。

    返回结构跟 meta_ads.resolve_ad_product_match 一致，方便 fallback。
    """
    if not normalized_campaign_code:
        return None
    row = query_one(
        "SELECT o.product_id AS id, o.product_code, m.name "
        "FROM campaign_product_overrides o "
        "LEFT JOIN media_products m ON m.id = o.product_id AND m.deleted_at IS NULL "
        "WHERE o.normalized_campaign_code = %s",
        (normalized_campaign_code,),
    )
    if not row:
        return None
    return {"id": row["id"], "product_code": row.get("product_code"), "name": row.get("name")}


def list_overrides() -> list[dict[str, Any]]:
    rows = query(
        "SELECT id, normalized_campaign_code, product_id, product_code, "
        "       reason, created_by, created_at, updated_at "
        "FROM campaign_product_overrides "
        "ORDER BY created_at DESC"
    )
    return list(rows or [])


def apply_override_to_history(
    *,
    normalized_campaign_code: str,
    product_id: int,
    product_code: str | None,
) -> int:
    """把 override 应用到历史 meta_ad_daily_campaign_metrics
       （UPDATE product_id + matched_product_code）。

    Returns:
        受影响行数（无法可靠拿到，返回 0 表示已执行）
    """
    execute(
        "UPDATE meta_ad_daily_campaign_metrics "
        "SET product_id = %s, matched_product_code = %s "
        "WHERE normalized_campaign_code = %s "
        "  AND (product_id IS NULL OR product_id = %s)",
        (product_id, product_code, normalized_campaign_code, product_id),
    )
    return 0


def create_override(
    *,
    normalized_campaign_code: str,
    product_id: int,
    reason: str = "",
    created_by: str = "admin",
) -> dict[str, Any]:
    """创建/更新人工配对，并立即应用到历史数据。"""
    if not normalized_campaign_code or not isinstance(normalized_campaign_code, str):
        raise ValueError("normalized_campaign_code is required")

    product = query_one(
        "SELECT id, product_code FROM media_products "
        "WHERE id = %s AND deleted_at IS NULL",
        (int(product_id),),
    )
    if not product:
        raise ValueError(f"product_id={product_id} 不存在或已删除")
    product_code = product["product_code"]

    execute(
        "INSERT INTO campaign_product_overrides "
        "(normalized_campaign_code, product_id, product_code, reason, created_by) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  product_id = VALUES(product_id), "
        "  product_code = VALUES(product_code), "
        "  reason = VALUES(reason), "
        "  created_by = VALUES(created_by)",
        (normalized_campaign_code, int(product_id), product_code, reason, created_by),
    )
    apply_override_to_history(
        normalized_campaign_code=normalized_campaign_code,
        product_id=int(product_id),
        product_code=product_code,
    )
    return {
        "normalized_campaign_code": normalized_campaign_code,
        "product_id": int(product_id),
        "product_code": product_code,
    }


def remove_override(*, override_id: int) -> dict[str, Any]:
    """删除一条人工配对，并把 meta_ad_daily_campaign_metrics 里对应的 product_id 清空。

    清空之后，如果 normalized_campaign_code 还能自动匹配（meta_ads.resolve_ad_product_match）
    会在下次跑 match_meta_ads_to_products 时被重填。
    """
    row = query_one(
        "SELECT normalized_campaign_code FROM campaign_product_overrides WHERE id = %s",
        (int(override_id),),
    )
    if not row:
        return {"removed": 0}

    code = row["normalized_campaign_code"]
    execute(
        "DELETE FROM campaign_product_overrides WHERE id = %s",
        (int(override_id),),
    )
    execute(
        "UPDATE meta_ad_daily_campaign_metrics "
        "SET product_id = NULL, matched_product_code = NULL "
        "WHERE normalized_campaign_code = %s",
        (code,),
    )
    return {"removed": 1, "normalized_campaign_code": code}
