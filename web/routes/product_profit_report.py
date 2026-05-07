"""产品盈亏报表 + Shopify Payments 导入入口（数据分析页面右上角）。

挂在 /order-analytics/product-profit/ 路径下，作为"数据分析"页面的右上角操作。
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required

from web.auth import admin_required

from appcore.order_analytics import product_profit_report as ppr
from appcore.order_analytics.shopify_payments_import import import_payments_csv
from web.upload_util import client_filename_basename

log = logging.getLogger(__name__)

bp = Blueprint("product_profit_report", __name__, url_prefix="/order-analytics/product-profit")


_STORE_CODE_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(value: str | None, default: date) -> date:
    if not value or not _DATE_RE.match(value):
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return default


@bp.route("/products", methods=["GET"])
@login_required
@admin_required
def api_list_products():
    """产品下拉数据。"""
    return jsonify({"products": ppr.list_products()})


@bp.route("/payments_csv/import", methods=["POST"])
@login_required
@admin_required
def api_import_payments_csv():
    """上传 Shopify Payments CSV → 解析 + 反推 + 入库 shopify_payments_transactions。

    Form 字段：
      - file: CSV 文件（必填）
      - store_code: 店铺标识（newjoyloo / Omurio）（必填，拼到 source_csv 前缀）
    """
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "missing file"}), 400

    store_code = (request.form.get("store_code") or "").strip()
    if not _STORE_CODE_RE.match(store_code):
        return jsonify({
            "error": "invalid store_code",
            "hint": "需要 1-32 个字母/数字/下划线，例如 newjoyloo / Omurio",
        }), 400

    raw_content = f.read()
    try:
        content = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw_content.decode("gbk")
        except Exception:
            return jsonify({"error": "csv encoding not utf-8 or gbk"}), 400

    filename = client_filename_basename(f.filename)
    source_label = f"{store_code}__{filename}"
    try:
        stats = import_payments_csv(io.StringIO(content), source_csv=source_label)
    except Exception as exc:  # noqa: BLE001 - bubble structured error to UI
        log.exception("payments csv import failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    return jsonify({
        "store_code": store_code,
        "filename": filename,
        "source_csv": source_label,
        **stats,
    })


@bp.route("/report.xlsx", methods=["GET"])
@login_required
@admin_required
def api_download_xlsx():
    """生成产品盈亏 Excel 报表（4 sheet）。

    Query:
      product_id (int, required)
      date_from (YYYY-MM-DD, default = today - 30d)
      date_to   (YYYY-MM-DD, default = today)
    """
    try:
        product_id = int(request.args.get("product_id", "0"))
    except ValueError:
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "missing product_id"}), 400

    today = date.today()
    date_to = _parse_date(request.args.get("date_to"), today)
    date_from = _parse_date(request.args.get("date_from"), today - timedelta(days=30))
    if date_from > date_to:
        return jsonify({"error": "date_from > date_to"}), 400

    report = ppr.generate_report(product_id=product_id, date_from=date_from, date_to=date_to)
    xlsx_bytes = ppr.generate_xlsx(report)

    code = report["total"].get("product_code") or f"product-{product_id}"
    filename = f"profit_{code}_{date_from.isoformat()}_{date_to.isoformat()}.xlsx"

    return send_file(
        io.BytesIO(xlsx_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/report.json", methods=["GET"])
@login_required
@admin_required
def api_report_json():
    """同 report.xlsx，但返回 JSON（便于前端预览或调试）。"""
    try:
        product_id = int(request.args.get("product_id", "0"))
    except ValueError:
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "missing product_id"}), 400

    today = date.today()
    date_to = _parse_date(request.args.get("date_to"), today)
    date_from = _parse_date(request.args.get("date_from"), today - timedelta(days=30))
    if date_from > date_to:
        return jsonify({"error": "date_from > date_to"}), 400

    report = ppr.generate_report(product_id=product_id, date_from=date_from, date_to=date_to)

    # JSON 友好：日期/datetime 转字符串
    def _iso(v):
        if isinstance(v, datetime):
            return v.isoformat(sep=" ")
        if isinstance(v, date):
            return v.isoformat()
        return v

    def _iso_dict(d):
        if isinstance(d, dict):
            return {k: _iso_dict(v) for k, v in d.items()}
        if isinstance(d, list):
            return [_iso_dict(x) for x in d]
        return _iso(d)

    return jsonify(_iso_dict(report))
