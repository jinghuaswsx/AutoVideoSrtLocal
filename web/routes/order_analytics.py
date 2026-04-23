"""订单分析模块：导入 Shopify 订单 CSV/Excel，按商品 × 国家统计单量。"""
from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

log = logging.getLogger(__name__)

bp = Blueprint("order_analytics", __name__)

# Shopify 导出文件中需要用到的列名
COL_PRODUCT = "Lineitem name"
COL_QUANTITY = "Lineitem quantity"
COL_COUNTRY = "Billing Country"


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/order-analytics")
@login_required
def page():
    return render_template("order_analytics.html")


# ── API ───────────────────────────────────────────────

@bp.route("/order-analytics/upload", methods=["POST"])
@login_required
def upload():
    """接收 CSV 或 Excel 文件，解析后返回按商品×国家聚合的数据。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="请选择文件"), 400

    filename = f.filename.lower()
    try:
        if filename.endswith(".csv"):
            text = f.read().decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
        elif filename.endswith((".xls", ".xlsx")):
            rows = _parse_excel(f.stream)
        else:
            return jsonify(error="仅支持 CSV / Excel (.xlsx) 文件"), 400
    except Exception as exc:
        log.warning("order_analytics upload parse error: %s", exc, exc_info=True)
        return jsonify(error=f"文件解析失败：{exc}"), 400

    if not rows:
        return jsonify(error="文件为空或格式不正确"), 400

    return jsonify(_aggregate(rows))


def _parse_excel(stream):
    """用 openpyxl 解析 .xlsx，返回 list[dict]。"""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("服务器未安装 openpyxl，无法解析 Excel 文件")

    wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter)
    if not headers:
        return []
    # 清理 None 和空白
    headers = [str(h).strip() if h else "" for h in headers]
    result = []
    for row in rows_iter:
        d = {}
        for i, val in enumerate(row):
            if i < len(headers) and headers[i]:
                d[headers[i]] = str(val) if val is not None else ""
        result.append(d)
    wb.close()
    return result


def _aggregate(rows: list[dict]) -> dict:
    """将原始行聚合为 {products, countries, matrix, totals} 结构。

    返回:
      products: [{"name": ..., "total": ...}, ...]  按 total 降序
      countries: [country_code, ...]  按出现频次降序
      matrix: {product_name: {country: quantity, ...}, ...}
    """
    # {product: {country: qty}}
    prod_country: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    country_freq: dict[str, int] = defaultdict(int)

    for row in rows:
        product = (row.get(COL_PRODUCT) or "").strip()
        country = (row.get(COL_COUNTRY) or "").strip()
        qty_str = (row.get(COL_QUANTITY) or "0").strip()

        if not product:
            continue

        try:
            qty = int(float(qty_str))
        except (ValueError, TypeError):
            qty = 1

        if country:
            prod_country[product][country] += qty
            country_freq[country] += qty
        else:
            prod_country[product]["未知"] += qty
            country_freq["未知"] += qty

    # 国家按总单量降序
    countries = sorted(country_freq.keys(), key=lambda c: country_freq[c], reverse=True)

    # 商品按总单量降序
    products = []
    for name, country_map in prod_country.items():
        total = sum(country_map.values())
        products.append({"name": name, "total": total})
    products.sort(key=lambda p: p["total"], reverse=True)

    # 矩阵
    matrix = {}
    for p in products:
        matrix[p["name"]] = {c: prod_country[p["name"]].get(c, 0) for c in countries}

    return {
        "products": products,
        "countries": countries,
        "matrix": matrix,
        "total_orders": sum(p["total"] for p in products),
        "total_rows": len(rows),
    }
