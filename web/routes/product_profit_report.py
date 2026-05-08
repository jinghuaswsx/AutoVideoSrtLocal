"""产品盈亏报表 + Shopify Payments 导入入口（数据分析页面右上角）。

挂在 /order-analytics/product-profit/ 路径下，作为"数据分析"页面的右上角操作。
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, request, send_file
from flask_login import login_required

from web.auth import permission_required

from appcore import medias
from appcore.order_analytics import current_meta_business_date
from appcore.order_analytics import product_profit_ads as ppa
from appcore.order_analytics import product_profit_list as ppl
from appcore.order_analytics import product_profit_report as ppr
from appcore.order_analytics._constants import COUNTRY_TO_LANG, LANG_PRIORITY_COUNTRIES
from appcore.order_analytics.campaign_overrides import remove_override
from appcore.order_analytics.meta_ads import manual_match_meta_ad_campaign
from appcore.order_analytics.shopify_payments_import import import_payments_csv
from web.services.product_profit_report import (
    build_product_profit_report_error_response,
    build_product_profit_report_payload_response,
    product_profit_report_flask_response,
)
from web.upload_util import client_filename_basename

log = logging.getLogger(__name__)

bp = Blueprint("product_profit_report", __name__, url_prefix="/order-analytics/product-profit")


_STORE_CODE_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PRODUCT_PROFIT_COUNTRY_PILL_LIMIT = 9
_COUNTRY_LABELS = {
    "GB": "英国",
    "US": "美国",
    "DE": "德国",
    "AT": "奥地利",
    "FR": "法国",
    "ES": "西班牙",
    "IT": "意大利",
    "JP": "日本",
    "PT": "葡萄牙",
    "BR": "巴西",
    "NL": "荷兰",
    "SE": "瑞典",
    "FI": "芬兰",
}


def _default_business_today() -> date:
    return current_meta_business_date()


def _parse_date(value: str | None, default: date) -> date:
    if not value or not _DATE_RE.match(value):
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return default


def _primary_country_for_lang(lang: str) -> str | None:
    normalized = (lang or "").strip().lower()
    if not normalized:
        return None
    if normalized == "en":
        return "US"

    priority = LANG_PRIORITY_COUNTRIES.get(normalized) or ()
    if priority:
        return priority[0]

    for country, mapped_lang in COUNTRY_TO_LANG.items():
        if str(mapped_lang).strip().lower() == normalized:
            return country
    return None


def _product_profit_country_pills() -> list[dict]:
    """国家看板胶囊：US 固定第一、GB 固定第二，其余按启用小语种主国家补足。"""
    pills: list[dict] = []
    seen: set[str] = set()

    def add(country: str, lang: str) -> None:
        code = (country or "").strip().upper()
        if not code or code in seen or len(pills) >= _PRODUCT_PROFIT_COUNTRY_PILL_LIMIT:
            return
        seen.add(code)
        pills.append({
            "country": code,
            "lang": (lang or "").strip().lower(),
            "label": _COUNTRY_LABELS.get(code, code),
        })

    add("US", "en")
    add("GB", "en")
    for lang, _name in medias.list_enabled_languages_kv():
        normalized = (lang or "").strip().lower()
        if normalized == "en":
            continue
        country = _primary_country_for_lang(normalized)
        if country:
            add(country, normalized)
    return pills


@bp.route("/products", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_list_products():
    """产品下拉数据。"""
    return product_profit_report_flask_response(
        build_product_profit_report_payload_response({"products": ppr.list_products()})
    )


@bp.route("/countries.json", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_country_pills():
    """国家看板胶囊列表（英国 + 启用小语种主国家，最多 9 个）。"""
    return product_profit_report_flask_response(
        build_product_profit_report_payload_response(
            {"countries": _product_profit_country_pills()}
        )
    )


@bp.route("/payments_csv/import", methods=["POST"])
@login_required
@permission_required("product_profit")
def api_import_payments_csv():
    """上传 Shopify Payments CSV → 解析 + 反推 + 入库 shopify_payments_transactions。

    Form 字段：
      - file: CSV 文件（必填）
      - store_code: 店铺标识（newjoyloo / Omurio）（必填，拼到 source_csv 前缀）
    """
    f = request.files.get("file")
    if f is None or not f.filename:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("missing file", 400)
        )

    store_code = (request.form.get("store_code") or "").strip()
    if not _STORE_CODE_RE.match(store_code):
        return product_profit_report_flask_response(
            build_product_profit_report_error_response(
                "invalid store_code",
                400,
                hint="需要 1-32 个字母/数字/下划线，例如 newjoyloo / Omurio",
            )
        )

    raw_content = f.read()
    try:
        content = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw_content.decode("gbk")
        except Exception:
            return product_profit_report_flask_response(
                build_product_profit_report_error_response("csv encoding not utf-8 or gbk", 400)
            )

    filename = client_filename_basename(f.filename)
    source_label = f"{store_code}__{filename}"
    try:
        stats = import_payments_csv(io.StringIO(content), source_csv=source_label)
    except Exception as exc:  # noqa: BLE001 - bubble structured error to UI
        log.exception("payments csv import failed")
        return product_profit_report_flask_response(
            build_product_profit_report_error_response(f"{type(exc).__name__}: {exc}", 500)
        )

    return product_profit_report_flask_response(
        build_product_profit_report_payload_response(
            {
                "store_code": store_code,
                "filename": filename,
                "source_csv": source_label,
                **stats,
            }
        )
    )


@bp.route("/report.xlsx", methods=["GET"])
@login_required
@permission_required("product_profit")
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
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("invalid product_id", 400)
        )
    if product_id <= 0:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("missing product_id", 400)
        )

    today = _default_business_today()
    date_to = _parse_date(request.args.get("date_to"), today)
    date_from = _parse_date(request.args.get("date_from"), today - timedelta(days=30))
    if date_from > date_to:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("date_from > date_to", 400)
        )

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
@permission_required("product_profit")
def api_report_json():
    """同 report.xlsx，但返回 JSON（便于前端预览或调试）。"""
    try:
        product_id = int(request.args.get("product_id", "0"))
    except ValueError:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("invalid product_id", 400)
        )
    if product_id <= 0:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("missing product_id", 400)
        )

    today = _default_business_today()
    date_to = _parse_date(request.args.get("date_to"), today)
    date_from = _parse_date(request.args.get("date_from"), today - timedelta(days=30))
    if date_from > date_to:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("date_from > date_to", 400)
        )

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

    return product_profit_report_flask_response(
        build_product_profit_report_payload_response(_iso_dict(report))
    )


# ---------------------------------------------------------------------------
# Tab ① 全产品聚合列表
# ---------------------------------------------------------------------------
@bp.route("/list.json", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_list_json():
    """全产品聚合列表（Tab ① 数据源）。

    Query:
      date_from (YYYY-MM-DD, default = month-start)
      date_to   (YYYY-MM-DD, default = today)
      country   (大写国家代码，可选；空 / "all" = 全部)
    """
    today = _default_business_today()
    month_start = today.replace(day=1)
    date_from = _parse_date(request.args.get("date_from"), month_start)
    date_to = _parse_date(request.args.get("date_to"), today)
    if date_from > date_to:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("date_from > date_to", 400)
        )

    country = (request.args.get("country") or "").strip() or None
    result = ppl.generate_list(date_from=date_from, date_to=date_to, country=country)
    return product_profit_report_flask_response(
        build_product_profit_report_payload_response(result)
    )


@bp.route("/list.xlsx", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_list_xlsx():
    """全产品聚合列表的 xlsx 导出（2 sheet：summary + products）。"""
    today = _default_business_today()
    month_start = today.replace(day=1)
    date_from = _parse_date(request.args.get("date_from"), month_start)
    date_to = _parse_date(request.args.get("date_to"), today)
    if date_from > date_to:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("date_from > date_to", 400)
        )

    country = (request.args.get("country") or "").strip() or None
    report = ppl.generate_list(date_from=date_from, date_to=date_to, country=country)
    xlsx_bytes = ppl.generate_list_xlsx(
        report, date_from=date_from, date_to=date_to, country=country,
    )

    # 选具体国家时把大写国家代码拼进文件名，避免「越南 / 全部」下载同名混淆。
    if country and country.lower() != "all":
        country_part = f"{country.upper()}_"
    else:
        country_part = ""
    filename = (
        f"product_profit_list_{country_part}"
        f"{date_from.isoformat()}_{date_to.isoformat()}.xlsx"
    )
    return send_file(
        io.BytesIO(xlsx_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Tab ④ 广告明细
# ---------------------------------------------------------------------------
@bp.route("/ads.json", methods=["GET"])
@login_required
@permission_required("product_profit")
def api_ads_json():
    """单产品广告明细（Tab ④ 数据源）。

    Query:
      product_id (int, required)
      date_from (YYYY-MM-DD, default = today - 30d)
      date_to   (YYYY-MM-DD, default = today)
      country   (大写国家代码，可选；空 / "all" = 全部)
    """
    try:
        product_id = int(request.args.get("product_id", "0"))
    except ValueError:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("invalid product_id", 400)
        )
    if product_id <= 0:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("missing product_id", 400)
        )

    today = _default_business_today()
    date_to = _parse_date(request.args.get("date_to"), today)
    date_from = _parse_date(request.args.get("date_from"), today - timedelta(days=30))
    if date_from > date_to:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("date_from > date_to", 400)
        )

    country = (request.args.get("country") or "").strip() or None
    try:
        report = ppa.generate_ads_report(
            product_id=product_id,
            date_from=date_from,
            date_to=date_to,
            country=country,
        )
    except Exception as exc:  # noqa: BLE001 - bubble structured error to UI
        log.exception("generate_ads_report failed")
        return product_profit_report_flask_response(
            build_product_profit_report_error_response(f"{type(exc).__name__}: {exc}", 500)
        )

    return product_profit_report_flask_response(
        build_product_profit_report_payload_response(report)
    )


@bp.route("/ads/manual-match", methods=["POST"])
@login_required
@permission_required("product_profit")
def api_ads_manual_match():
    """手动把 normalized_campaign_code 配对到 media_products 产品。

    JSON Body:
      campaign_code (str, required) — normalized_campaign_code
      product_id    (int, required) — media_products.id
      reason        (str, optional)
    """
    payload = request.get_json(silent=True) or {}

    campaign_code = (payload.get("campaign_code") or "").strip()
    if not campaign_code:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("missing campaign_code", 400)
        )

    raw_product_id = payload.get("product_id")
    try:
        product_id = int(raw_product_id) if raw_product_id is not None else 0
    except (TypeError, ValueError):
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("invalid product_id", 400)
        )
    if product_id <= 0:
        return product_profit_report_flask_response(
            build_product_profit_report_error_response("missing product_id", 400)
        )

    reason = (payload.get("reason") or "").strip()
    try:
        result = manual_match_meta_ad_campaign(
            campaign_code,
            product_id,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - bubble structured error to UI
        log.exception("manual_match_meta_ad_campaign failed")
        return product_profit_report_flask_response(
            build_product_profit_report_error_response(f"{type(exc).__name__}: {exc}", 500)
        )

    return product_profit_report_flask_response(
        build_product_profit_report_payload_response({"ok": True, **result})
    )


@bp.route("/ads/manual-match/<int:override_id>", methods=["DELETE"])
@login_required
@permission_required("product_profit")
def api_ads_manual_match_delete(override_id):
    """解绑一条 campaign_product_overrides 人工配对。"""
    try:
        result = remove_override(override_id=override_id)
    except Exception as exc:  # noqa: BLE001 - bubble structured error to UI
        log.exception("remove_override failed")
        return product_profit_report_flask_response(
            build_product_profit_report_error_response(f"{type(exc).__name__}: {exc}", 500)
        )

    return product_profit_report_flask_response(
        build_product_profit_report_payload_response({"ok": True, **result})
    )
