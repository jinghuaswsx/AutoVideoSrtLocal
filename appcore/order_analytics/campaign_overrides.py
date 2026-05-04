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
) -> dict[str, int]:
    """把 override 同时应用到两张广告事实表：
       meta_ad_campaign_metrics（月度/期间快照）+ meta_ad_daily_campaign_metrics（日度）。

    Returns:
        {"matched_periodic": N, "matched_daily": M}
        （execute 返回值取决于 driver；为兼容 mock 测试，None 视为 0）
    """
    # 安全策略：UPDATE 仅覆盖 product_id IS NULL 的行，不动已匹配的（避免误改）。
    # 改产品需要先 remove_override 再重新 create_override。
    matched_periodic = execute(
        "UPDATE meta_ad_campaign_metrics "
        "SET product_id = %s, matched_product_code = %s "
        "WHERE normalized_campaign_code = %s AND product_id IS NULL",
        (product_id, product_code, normalized_campaign_code),
    )
    matched_daily = execute(
        "UPDATE meta_ad_daily_campaign_metrics "
        "SET product_id = %s, matched_product_code = %s "
        "WHERE normalized_campaign_code = %s AND product_id IS NULL",
        (product_id, product_code, normalized_campaign_code),
    )
    return {
        "matched_periodic": int(matched_periodic or 0),
        "matched_daily": int(matched_daily or 0),
    }


def create_override(
    *,
    normalized_campaign_code: str,
    product_id: int,
    reason: str = "",
    created_by: str = "admin",
) -> dict[str, Any]:
    """创建/更新人工配对，并立即应用到历史数据。

    Source of truth：campaign_product_overrides 表。
    副作用：UPDATE meta_ad_campaign_metrics + meta_ad_daily_campaign_metrics
    两张事实表，让历史 dashboard / 利润核算立即看到匹配；同时
    meta_ads.resolve_ad_product_match 在自动同步时也查 override 表，
    未来同名 campaign 进来不用再手工配对。

    Returns:
        {normalized_campaign_code, product_id, product_code, product_name,
         matched_periodic, matched_daily}
    """
    code = (normalized_campaign_code or "").strip().lower()
    if not code:
        raise ValueError("normalized_campaign_code is required")
    try:
        pid = int(product_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("product_id must be an integer") from exc
    if pid <= 0:
        raise ValueError("product_id must be positive")

    product = query_one(
        "SELECT id, product_code, name FROM media_products "
        "WHERE id = %s AND deleted_at IS NULL",
        (pid,),
    )
    if not product:
        raise LookupError(f"product {pid} not found or deleted")
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
        (code, pid, product_code, reason, created_by),
    )
    applied = apply_override_to_history(
        normalized_campaign_code=code,
        product_id=pid,
        product_code=product_code,
    )
    return {
        "normalized_campaign_code": code,
        "product_id": pid,
        "product_code": product_code,
        "product_name": product.get("name"),
        "matched_periodic": applied["matched_periodic"],
        "matched_daily": applied["matched_daily"],
    }


def remove_override(*, override_id: int) -> dict[str, Any]:
    """删除一条人工配对，并把两张事实表里对应的 product_id 清空。

    清空之后，如果 normalized_campaign_code 还能自动匹配
    （meta_ads.resolve_ad_product_match）会在下次同步时重填。
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
        "UPDATE meta_ad_campaign_metrics "
        "SET product_id = NULL, matched_product_code = NULL "
        "WHERE normalized_campaign_code = %s",
        (code,),
    )
    execute(
        "UPDATE meta_ad_daily_campaign_metrics "
        "SET product_id = NULL, matched_product_code = NULL "
        "WHERE normalized_campaign_code = %s",
        (code,),
    )
    return {"removed": 1, "normalized_campaign_code": code}
