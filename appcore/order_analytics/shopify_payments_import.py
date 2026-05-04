"""Shopify Payments CSV 导入 + 反推校验（策略 B 校验回路）。

业务流程：
  1. 业务方按月/周从 Shopify 后台 Payouts → Transactions 导出 CSV
  2. 调用 import_payments_csv() 解析 + 反推 + 写入 shopify_payments_transactions
  3. 调用 reconcile_against_estimates() 跟 order_profit_lines.shopify_fee_usd 对比，
     输出偏差报告（按 tier / country 分组），用来校准策略 C 参数

CSV 列约定（Shopify Payouts 标准导出）：
  Transaction Date, Type, Order, Card Brand, Amount, Fee, Net,
  Presentment Amount, Presentment Currency, Payout Date, Payout Status, ...
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from typing import Any, IO

from .shopify_fee import classify_tier, verify_fee

log = logging.getLogger(__name__)


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def get_conn(*args, **kwargs):
    return _facade().get_conn(*args, **kwargs)


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _row_id(row: dict[str, Any]) -> str:
    """没 transaction_id 时用 csv 行哈希作为唯一键。"""
    import hashlib
    raw = json.dumps(row, sort_keys=True, default=str)
    return "csv-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_payments_csv(stream: IO[str], *, source_csv: str = "") -> list[dict[str, Any]]:
    """解析 Shopify Payments CSV，返回结构化行（含反推结果）。"""
    reader = csv.DictReader(stream)
    out: list[dict[str, Any]] = []
    for raw in reader:
        # 兼容多种列名大小写
        norm = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        amount = _safe_float(norm.get("Amount"))
        fee = _safe_float(norm.get("Fee"))
        net = _safe_float(norm.get("Net"))
        presentment_currency = (norm.get("Presentment Currency") or "USD").upper()
        txn_type = (norm.get("Type") or "").lower()

        row = {
            "transaction_id": norm.get("Transaction ID") or _row_id(norm),
            "payout_id": norm.get("Payout ID") or norm.get("Payout"),
            "type": txn_type,
            "order_name": norm.get("Order"),
            "presentment_currency": presentment_currency,
            "amount_usd": amount,
            "fee_usd": fee,
            "net_usd": net,
            "card_brand": (norm.get("Card Brand") or "").lower() or None,
            "source_csv": source_csv,
            "raw_row_json": json.dumps(norm, ensure_ascii=False, default=str),
        }

        # 仅对正向 charge 跑反推（refund/chargeback 的 fee 经常为 0 或负数，跳过）
        if txn_type == "charge" and amount and amount > 0 and fee is not None:
            verify = verify_fee(
                amount=amount,
                actual_fee=fee,
                presentment_currency=presentment_currency,
            )
            origin = verify["card_origin"]
            row["inferred_card_origin"] = origin
            row["matches_standard"] = 1 if verify["matches_standard"] else 0
            if origin == "domestic":
                row["inferred_tier"] = classify_tier(presentment_currency, "US")
            elif origin == "international":
                row["inferred_tier"] = classify_tier(presentment_currency, "GB")  # 任意非 US
            else:
                row["inferred_tier"] = None
        else:
            row["inferred_card_origin"] = None
            row["matches_standard"] = None
            row["inferred_tier"] = None

        out.append(row)
    return out


def import_payments_csv(stream: IO[str], *, source_csv: str = "") -> dict[str, Any]:
    """解析 + 写入 shopify_payments_transactions 表。返回统计。"""
    rows = parse_payments_csv(stream, source_csv=source_csv)
    stats = {"total": len(rows), "inserted": 0, "updated": 0, "skipped": 0,
             "matches_standard": 0, "anomalies": 0}

    sql = (
        "INSERT INTO shopify_payments_transactions ("
        "  transaction_id, payout_id, type, order_name, presentment_currency, "
        "  amount_usd, fee_usd, net_usd, card_brand, "
        "  inferred_card_origin, inferred_tier, matches_standard, "
        "  source_csv, raw_row_json"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  payout_id=VALUES(payout_id), type=VALUES(type), "
        "  order_name=VALUES(order_name), presentment_currency=VALUES(presentment_currency), "
        "  amount_usd=VALUES(amount_usd), fee_usd=VALUES(fee_usd), net_usd=VALUES(net_usd), "
        "  card_brand=VALUES(card_brand), "
        "  inferred_card_origin=VALUES(inferred_card_origin), "
        "  inferred_tier=VALUES(inferred_tier), "
        "  matches_standard=VALUES(matches_standard), "
        "  source_csv=VALUES(source_csv), raw_row_json=VALUES(raw_row_json)"
    )

    for row in rows:
        if not row.get("amount_usd"):
            stats["skipped"] += 1
            continue
        execute(sql, (
            row["transaction_id"], row["payout_id"], row["type"],
            row["order_name"], row["presentment_currency"],
            row["amount_usd"], row["fee_usd"], row["net_usd"],
            row["card_brand"],
            row["inferred_card_origin"], row["inferred_tier"], row["matches_standard"],
            row["source_csv"], row["raw_row_json"],
        ))
        stats["inserted"] += 1
        if row.get("matches_standard") == 1:
            stats["matches_standard"] += 1
        elif row.get("matches_standard") == 0:
            stats["anomalies"] += 1

    return stats


def reconcile_against_estimates(*, payout_date_from: str, payout_date_to: str) -> dict[str, Any]:
    """对账：把 shopify_payments_transactions 的真实 fee
       跟 order_profit_lines 的估算 fee 比较，输出偏差报告。

    注：CSV 里的 Order 字段是 Shopify order name（如 #1001），跟
    dianxiaomi_order_lines 的 dxm_order_id 不直接对应。本版用日级聚合做粗对账：
      - 期间内 actual_fee 总和（CSV）
      - 期间内 estimated_fee 总和（order_profit_lines）
      - 差额、按 tier / 国家分组（如果想细化要做 order 名映射）
    """
    actual = query(
        "SELECT inferred_tier AS tier, "
        "       SUM(amount_usd) AS amount_total, "
        "       SUM(fee_usd) AS fee_total, "
        "       COUNT(*) AS n "
        "FROM shopify_payments_transactions "
        "WHERE type='charge' AND amount_usd > 0 "
        "GROUP BY inferred_tier"
    )
    estimated = query(
        "SELECT shopify_tier AS tier, "
        "       SUM(revenue_usd) AS amount_total, "
        "       SUM(shopify_fee_usd) AS fee_total, "
        "       COUNT(*) AS n "
        "FROM order_profit_lines "
        "WHERE status='ok' "
        "GROUP BY shopify_tier"
    )

    by_tier: dict[str, dict[str, Any]] = {}
    for row in actual:
        tier = row["tier"] or "unknown"
        by_tier.setdefault(tier, {})["actual_fee"] = float(row["fee_total"] or 0)
        by_tier[tier]["actual_amount"] = float(row["amount_total"] or 0)
        by_tier[tier]["actual_lines"] = int(row["n"])
    for row in estimated:
        tier = (row["tier"] or "unknown").rstrip("_estimated") or "unknown"
        by_tier.setdefault(tier, {})["estimated_fee"] = (
            by_tier.get(tier, {}).get("estimated_fee", 0) + float(row["fee_total"] or 0)
        )
        by_tier[tier]["estimated_amount"] = (
            by_tier.get(tier, {}).get("estimated_amount", 0) + float(row["amount_total"] or 0)
        )
        by_tier[tier]["estimated_lines"] = (
            by_tier.get(tier, {}).get("estimated_lines", 0) + int(row["n"])
        )

    for tier, agg in by_tier.items():
        actual_fee = agg.get("actual_fee", 0)
        est_fee = agg.get("estimated_fee", 0)
        agg["diff_usd"] = round(est_fee - actual_fee, 4)
        agg["estimated_over_actual_pct"] = (
            round(100 * (est_fee - actual_fee) / actual_fee, 2)
            if actual_fee > 0 else None
        )

    return {
        "payout_date_from": payout_date_from,
        "payout_date_to": payout_date_to,
        "by_tier": by_tier,
    }
