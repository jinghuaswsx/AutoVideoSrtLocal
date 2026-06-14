"""退款核验：解析 Shopify Payments/订单退款、关联店小秘订单、批次状态机与核算覆盖。

设计：docs/superpowers/specs/2026-06-13-refund-verification-design.md
DB 入口走 module-level facade（与 shopify_payments_import.py 同款），
让测试 monkeypatch.setattr(oa, "query", ...) 透传到本模块。
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


_REFUND_TYPES = ("refund", "chargeback")
_REFUND_FIN_STATUSES = ("refunded", "partially_refunded")


def _normalize_order_name(name: Any) -> str:
    return str(name or "").strip().lstrip("#").strip()


def aggregate_payment_refunds(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Payments 行按订单号聚合真实退款额（refund/chargeback 取绝对值求和）。"""
    out: dict[str, float] = {}
    for r in rows:
        if (r.get("type") or "").lower() not in _REFUND_TYPES:
            continue
        order = _normalize_order_name(r.get("order_name"))
        amount = r.get("amount_usd")
        if not order or amount in (None, ""):
            continue
        out[order] = round(out.get(order, 0.0) + abs(float(amount)), 4)
    return out


def extract_order_refund_statuses(rows: list[dict[str, Any]]) -> dict[str, str]:
    """订单 CSV 行取退款状态（refunded/partially_refunded）。"""
    out: dict[str, str] = {}
    for r in rows:
        status = (r.get("financial_status") or "").strip().lower()
        if status not in _REFUND_FIN_STATUSES:
            continue
        order = _normalize_order_name(r.get("order_name"))
        if order:
            out[order] = status
    return out


def aggregate_refunds_from_db(*, site_code: str | None = None) -> dict[str, float]:
    """从 shopify_payments_transactions 查询累计退款总额（按订单号聚合）。"""
    where = "WHERE type IN ('refund','chargeback') AND order_name IS NOT NULL"
    args: tuple = ()
    if site_code:
        where += " AND source_csv LIKE %s"
        args = (f"%{site_code}%",)
    rows = query(
        "SELECT order_name, SUM(ABS(COALESCE(amount_usd, 0))) AS total_refund "
        f"FROM shopify_payments_transactions {where} "
        "GROUP BY order_name",
        args,
    ) or []
    return {_normalize_order_name(r["order_name"]): round(float(r["total_refund"]), 4)
            for r in rows if r.get("order_name")}


def _load_order_packages(order_ids: list[str], *, site_code: str | None = None) -> dict[str, dict[str, Any]]:
    if not order_ids:
        return {}
    placeholders = ",".join(["%s"] * len(order_ids))
    args: list[Any] = list(order_ids)
    site_filter = ""
    if site_code:
        site_filter = " AND d.site_code = %s"
        args.append(site_code)
    rows = query(
        "SELECT d.extended_order_id, d.dxm_package_id, d.site_code, "
        "       (COALESCE(SUM(d.line_amount),0) + COALESCE(MAX(d.ship_amount),0)) AS revenue "
        "FROM dianxiaomi_order_lines d "
        f"WHERE d.extended_order_id IN ({placeholders}){site_filter} "
        "GROUP BY d.extended_order_id, d.dxm_package_id, d.site_code",
        tuple(args),
    ) or []
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        oid = str(r.get("extended_order_id") or "")
        if not oid:
            continue
        entry = out.setdefault(oid, {"packages": [], "site_code": r.get("site_code"), "revenue": 0.0})
        entry["packages"].append(str(r.get("dxm_package_id")))
        entry["revenue"] += float(r.get("revenue") or 0.0)
    return out


def build_verification_rows(
    refunds: dict[str, float],
    statuses: dict[str, str],
    *,
    site_code: str | None = None,
) -> list[dict[str, Any]]:
    order_ids = sorted(set(refunds) | set(statuses))
    pkg_map = _load_order_packages(order_ids, site_code=site_code)
    rows: list[dict[str, Any]] = []
    for oid in order_ids:
        amount = refunds.get(oid)
        fin_status = statuses.get(oid)
        source = ("both" if amount is not None and fin_status else
                  "payments" if amount is not None else "order_status")
        entry = pkg_map.get(oid)
        if not entry:
            match_status, note, packages, site = "unmatched", "订单号不在店小秘库", [], None
        else:
            packages = entry["packages"]
            site = entry["site_code"]
            revenue = entry["revenue"]
            if amount is None:
                match_status, note = "anomaly", "有退款状态但缺 Payments 金额"
            elif amount > revenue:
                match_status, note = "anomaly", f"退款 {amount} 大于订单营收 {round(revenue,2)}"
            else:
                match_status, note = "matched", None
        rows.append({
            "extended_order_id": oid, "site_code": site,
            "refund_amount_usd": amount, "refund_source": source,
            "order_financial_status": fin_status,
            "matched_package_ids": packages, "match_status": match_status, "note": note,
        })
    return rows
