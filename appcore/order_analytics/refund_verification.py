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


def _load_current_reserve(order_ids: list[str]) -> dict[str, float]:
    if not order_ids:
        return {}
    ph = ",".join(["%s"] * len(order_ids))
    rows = query(
        "SELECT d.extended_order_id, SUM(p.return_reserve_usd) AS reserve "
        "FROM order_profit_lines p JOIN dianxiaomi_order_lines d ON d.id=p.dxm_order_line_id "
        f"WHERE d.extended_order_id IN ({ph}) GROUP BY d.extended_order_id",
        tuple(order_ids),
    ) or []
    return {str(r["extended_order_id"]): float(r.get("reserve") or 0) for r in rows}


def create_batch(*, refunds, statuses, source_files, created_by, site_code=None):
    rows = build_verification_rows(refunds, statuses, site_code=site_code)
    matched_ids = [r["extended_order_id"] for r in rows if r["match_status"] == "matched"]
    reserve_map = _load_current_reserve(matched_ids)
    matched = [r for r in rows if r["match_status"] == "matched"]
    total_refund = round(sum(r["refund_amount_usd"] or 0 for r in matched), 4)
    cur_reserve = round(sum(reserve_map.get(r["extended_order_id"], 0) for r in matched), 4)
    counts = {s: sum(1 for r in rows if r["match_status"] == s)
              for s in ("matched", "unmatched", "anomaly")}
    execute(
        "INSERT INTO refund_verification_batches "
        "(status,source_files,site_code,matched_count,unmatched_count,anomaly_count,"
        "total_refund_usd,current_reserve_usd,delta_usd,created_by) "
        "VALUES ('pending',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (json.dumps(source_files, ensure_ascii=False), site_code,
         counts["matched"], counts["unmatched"], counts["anomaly"],
         total_refund, cur_reserve, round(total_refund - cur_reserve, 4), created_by),
    )
    bid = int((query("SELECT LAST_INSERT_ID() AS id") or [{}])[0].get("id"))
    for r in rows:
        execute(
            "INSERT INTO refund_verifications "
            "(batch_id,extended_order_id,site_code,refund_amount_usd,refund_source,"
            "order_financial_status,matched_package_ids,match_status,note,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
            (bid, r["extended_order_id"], r["site_code"], r["refund_amount_usd"],
             r["refund_source"], r["order_financial_status"],
             json.dumps(r["matched_package_ids"], ensure_ascii=False),
             r["match_status"], r["note"]),
        )
    return {"batch_id": bid, "total_refund_usd": total_refund,
            "current_reserve_usd": cur_reserve,
            "delta_usd": round(total_refund - cur_reserve, 4),
            "matched_count": counts["matched"], "unmatched_count": counts["unmatched"],
            "anomaly_count": counts["anomaly"]}


def apply_batch(batch_id):
    execute("UPDATE refund_verification_batches SET status='applied',applied_at=NOW() WHERE id=%s AND status='pending'", (batch_id,))
    execute("UPDATE refund_verifications SET status='applied' WHERE batch_id=%s", (batch_id,))

def discard_batch(batch_id):
    execute("UPDATE refund_verification_batches SET status='discarded' WHERE id=%s AND status='pending'", (batch_id,))
    execute("UPDATE refund_verifications SET status='discarded' WHERE batch_id=%s", (batch_id,))

def revert_batch(batch_id):
    execute("UPDATE refund_verification_batches SET status='discarded' WHERE id=%s AND status='applied'", (batch_id,))
    execute("UPDATE refund_verifications SET status='discarded' WHERE batch_id=%s", (batch_id,))


def load_refund_verification_adjustments() -> dict[str, float]:
    rows = query(
        "SELECT v.extended_order_id, v.refund_amount_usd "
        "FROM refund_verifications v "
        "JOIN (SELECT extended_order_id, MAX(id) AS mid FROM refund_verifications "
        "      WHERE status='applied' AND refund_amount_usd IS NOT NULL "
        "      GROUP BY extended_order_id) latest ON latest.mid = v.id",
    ) or []
    return {str(r["extended_order_id"]): float(r["refund_amount_usd"]) for r in rows}


def _load_package_details_for_refund(order_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not order_ids:
        return {}
    ph = ",".join(["%s"] * len(order_ids))
    rows = query(
        "SELECT d.extended_order_id, d.dxm_package_id, "
        "       SUM(p.return_reserve_usd) AS reserve, SUM(p.revenue_usd) AS revenue "
        "FROM order_profit_lines p JOIN dianxiaomi_order_lines d ON d.id=p.dxm_order_line_id "
        f"WHERE d.extended_order_id IN ({ph}) GROUP BY d.extended_order_id, d.dxm_package_id",
        tuple(order_ids),
    ) or []
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["extended_order_id"]), []).append(r)
    return out


def _compute_contra_revenue_deltas() -> dict[str, dict[str, float]]:
    refund_map = load_refund_verification_adjustments()
    if not refund_map:
        return {}
    pkg_map = _load_package_details_for_refund(list(refund_map.keys()))
    out: dict[str, dict[str, float]] = {}
    for oid, pkgs in pkg_map.items():
        refund = refund_map.get(oid)
        if refund is None:
            continue
        revenue_sum = sum(float(p.get("revenue") or 0) for p in pkgs)
        refund_capped = min(refund, revenue_sum) if revenue_sum > 0 else 0.0
        if refund_capped <= 0:
            continue
        for p in pkgs:
            pid = str(p.get("dxm_package_id"))
            rev = float(p.get("revenue") or 0)
            share = rev / revenue_sum if revenue_sum > 0 else 1.0 / len(pkgs)
            reserve = float(p.get("reserve") or 0)
            out[pid] = {
                "revenue_deduct": round(refund_capped * share, 4),
                "reserve_release": round(reserve, 4),
            }
    return out


def _apply_contra_revenue(details: list[dict[str, Any]], deltas: dict[str, dict[str, float]]) -> float:
    total_deducted = 0.0
    for row in details:
        pid = str(row.get("dxm_package_id") or "")
        d = deltas.get(pid)
        if not d:
            continue
        rev_deduct = d["revenue_deduct"]
        reserve_release = d["reserve_release"]
        row["total_revenue"] = round(float(row.get("total_revenue") or 0) - rev_deduct, 2)
        row["return_reserve_usd"] = 0
        row["profit_deduction_usd"] = 0
        net_impact = rev_deduct - reserve_release
        for k in ("order_profit_usd", "order_profit_with_estimate_usd"):
            if row.get(k) is not None:
                row[k] = round(float(row.get(k) or 0) - net_impact, 2)
        total_deducted += rev_deduct
    return round(total_deducted, 2)


def apply_refund_adjustments_to_details(details: list[dict[str, Any]]) -> float:
    if not details:
        return 0.0
    try:
        deltas = _compute_contra_revenue_deltas()
    except Exception:
        return 0.0
    return _apply_contra_revenue(details, deltas)


def apply_refund_adjustments_to_details_aggregation(details: list[dict[str, Any]]) -> float:
    if not details:
        return 0.0
    try:
        deltas = _compute_contra_revenue_deltas()
    except Exception:
        return 0.0
    total = 0.0
    for row in details:
        pid = str(row.get("dxm_package_id") or "")
        d = deltas.get(pid)
        if not d:
            continue
        rev_deduct = d["revenue_deduct"]
        reserve_release = d["reserve_release"]
        row["total_revenue"] = round(float(row.get("total_revenue") or 0) - rev_deduct, 2)
        row["return_reserve"] = 0
        net_impact = rev_deduct - reserve_release
        for k in ("profit", "profit_with_estimate"):
            if row.get(k) is not None:
                row[k] = round(float(row.get(k) or 0) - net_impact, 2)
        total += rev_deduct
    return round(total, 2)
