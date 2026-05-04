"""SKU 成本完备性 gate。

业务方决策 Q11：
  完备 = purchase_price 已填(>0) + (packet_cost_actual 或 packet_cost_estimated 至少一个)

不完备的 SKU 在利润核算里返回 status='incomplete'，不出 profit 数字。
完备性看板 (get_completeness_overview) 让业务方看到各产品缺什么、按
订单影响排序优先补哪些产品。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


# DB 入口走 facade wrapper（与 dashboard.py 等同模式），方便 monkeypatch。
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def _safe_positive(value: Any) -> float | None:
    """转 float；None / 0 / 负数 / 不可解析 → None。"""
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if d <= 0:
        return None
    return float(d)


def check_sku_cost_completeness(product: dict[str, Any]) -> dict[str, Any]:
    """检查产品成本字段是否完备。

    Args:
        product: dict 含 purchase_price / packet_cost_actual / packet_cost_estimated
                 字段（可来自 media_products 表的行）

    Returns:
        {
          "ok": bool,
          "missing": list[str],                 # 缺的字段名（'purchase_price' / 'packet_cost'）
          "using_packet_cost": 'actual' | 'estimated' | None,
          "purchase_price": float | None,       # 完备时的实际值
          "packet_cost": float | None,          # 完备时的实际值（actual 优先）
        }
    """
    missing: list[str] = []
    purchase_price = _safe_positive(product.get("purchase_price"))
    if purchase_price is None:
        missing.append("purchase_price")

    actual = _safe_positive(product.get("packet_cost_actual"))
    estimated = _safe_positive(product.get("packet_cost_estimated"))

    if actual is not None:
        packet_cost = actual
        using = "actual"
    elif estimated is not None:
        packet_cost = estimated
        using = "estimated"
    else:
        packet_cost = None
        using = None
        missing.append("packet_cost")

    ok = not missing

    return {
        "ok": ok,
        "missing": missing,
        "using_packet_cost": using if ok else None,
        "purchase_price": purchase_price if ok else None,
        "packet_cost": packet_cost if ok else None,
    }


def get_completeness_overview(*, lookback_days: int = 30) -> list[dict[str, Any]]:
    """所有产品的成本完备性概览，按"待补录订单影响"排序：
       不完备产品按近 N 天 GMV 降序在前，完备产品在后。

    业务用途：让业务方先补热销产品，最大化利润核算覆盖率。

    Returns:
        [{
          "product_id": int,
          "product_code": str | None,
          "product_name": str,
          "completeness": {ok, missing, using_packet_cost, ...},
          "lookback_days": int,
          "order_lines": int,        # 近 N 天的订单行数
          "gmv_usd": float,          # 近 N 天的 GMV (USD)
        }, ...]
    """
    today = date.today()
    start = today - timedelta(days=lookback_days)

    products = query(
        "SELECT id, product_code, name, "
        "purchase_price, packet_cost_actual, packet_cost_estimated "
        "FROM media_products "
        "WHERE archived = 0 AND deleted_at IS NULL"
    )

    stats_rows = query(
        "SELECT product_id, COUNT(*) AS order_lines, "
        "SUM(line_amount) AS gmv "
        "FROM dianxiaomi_order_lines "
        "WHERE order_paid_at >= %s AND product_id IS NOT NULL "
        "GROUP BY product_id",
        (start,),
    )
    stats_by_pid: dict[int, dict[str, Any]] = {
        int(r["product_id"]): r for r in stats_rows if r.get("product_id") is not None
    }

    overview: list[dict[str, Any]] = []
    for product in products:
        pid = int(product["id"])
        completeness = check_sku_cost_completeness(product)
        stats = stats_by_pid.get(pid, {})
        overview.append({
            "product_id": pid,
            "product_code": product.get("product_code"),
            "product_name": product.get("name") or "",
            "completeness": completeness,
            "lookback_days": lookback_days,
            "order_lines": int(stats.get("order_lines") or 0),
            "gmv_usd": float(stats.get("gmv") or 0),
        })

    # 排序：不完备 (ok=False) 在前；同段内按 gmv_usd 降序
    overview.sort(key=lambda x: (x["completeness"]["ok"], -x["gmv_usd"]))
    return overview
