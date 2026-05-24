"""AI 自动上品模块 Flask 路由."""
from __future__ import annotations

import json
import logging
import uuid
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, abort
from flask_login import login_required, current_user

from web.auth import permission_required
from appcore import db
from appcore.ai_listing_service import translate_asset_image
from web.background import start_background_task

log = logging.getLogger(__name__)

bp = Blueprint("ai_listing", __name__, url_prefix="/ai-listing")


def _run_ai_listing_task(task_id: int, user_id: int | None = None):
    """后台线程执行二跳解析与AI素材包生成."""
    from appcore.ai_listing_service import parse_transit_link, generate_ai_listing_assets
    try:
        parse_transit_link(task_id, user_id=user_id)
        generate_ai_listing_assets(task_id, user_id=user_id)
    except Exception as e:
        log.exception("AI listing background task failed for task_id=%s", task_id)
        db.execute(
            "UPDATE ai_listing_tasks SET status = 'failed', error_message = %s WHERE id = %s",
            (str(e), task_id)
        )


@bp.route("/", methods=["GET"])
@login_required
@permission_required("ai_listing")
def index():
    """主看板页面."""
    tasks = db.query("SELECT * FROM ai_listing_tasks ORDER BY id DESC")
    return render_template("ai_listing_list.html", tasks=tasks)


@bp.route("/create", methods=["POST"])
@login_required
@permission_required("ai_listing")
def create_task():
    """新建上品任务."""
    source_link = request.form.get("source_link", "").strip()
    target_store_domain = request.form.get("target_store_domain", "").strip()
    pricing_ratio_str = request.form.get("pricing_ratio", "1.5").strip()
    pricing_offset_str = request.form.get("pricing_offset", "0.0").strip()
    source_type = request.form.get("source_type", "manual_input").strip()

    if not source_link or not target_store_domain:
        return jsonify({"ok": False, "error": "请填写源链接与目标店铺域名"}), 400

    try:
        pricing_ratio = float(pricing_ratio_str)
        pricing_offset = float(pricing_offset_str)
    except ValueError:
        return jsonify({"ok": False, "error": "定价比例与浮动值必须为数字"}), 400

    # 自动生成唯一 Shopify Product Code
    product_code = f"AL_{uuid.uuid4().hex[:8].upper()}"

    task_id = db.execute(
        "INSERT INTO ai_listing_tasks (product_code, source_type, source_link, target_store_domain, pricing_ratio, pricing_offset, status) VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
        (product_code, source_type, source_link, target_store_domain, pricing_ratio, pricing_offset)
    )

    # 启动后台任务解析引流博客与生成AI素材
    start_background_task(_run_ai_listing_task, task_id, current_user.id)

    return jsonify({"ok": True, "task_id": task_id, "product_code": product_code})


@bp.route("/task/<int:task_id>", methods=["GET"])
@login_required
@permission_required("ai_listing")
def task_detail(task_id: int):
    """上品工作台详情页."""
    task = db.query_one("SELECT * FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        abort(404)

    assets = db.query(
        "SELECT * FROM ai_listing_assets WHERE task_id = %s ORDER BY sort_order ASC, id ASC",
        (task_id,)
    )

    skus = []
    if task["generated_skus_json"]:
        try:
            skus = json.loads(task["generated_skus_json"])
        except Exception:
            skus = []

    return render_template("ai_listing_detail.html", task=task, assets=assets, skus=skus)


@bp.route("/task/<int:task_id>/edit", methods=["POST"])
@login_required
@permission_required("ai_listing")
def edit_task(task_id: int):
    """保存人机协同编辑后的文案与SKU价格结果."""
    task = db.query_one("SELECT * FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        return jsonify({"ok": False, "error": "任务不存在"}), 404

    data = request.get_json() or {}
    title = data.get("title", "").strip()
    html_desc = data.get("html_description", "").strip()
    pricing_ratio_str = str(data.get("pricing_ratio", "1.5")).strip()
    pricing_offset_str = str(data.get("pricing_offset", "0.0")).strip()
    skus_list = data.get("skus", [])

    if not title:
        return jsonify({"ok": False, "error": "标题不能为空"}), 400

    try:
        pricing_ratio = float(pricing_ratio_str)
        pricing_offset = float(pricing_offset_str)
    except ValueError:
        return jsonify({"ok": False, "error": "比例与浮动值必须为数字"}), 400

    db.execute(
        "UPDATE ai_listing_tasks SET generated_title = %s, generated_html_desc = %s, pricing_ratio = %s, pricing_offset = %s, generated_skus_json = %s WHERE id = %s",
        (title, html_desc, pricing_ratio, pricing_offset, json.dumps(skus_list), task_id)
    )

    return jsonify({"ok": True})


@bp.route("/task/<int:task_id>/asset/<int:asset_id>/toggle", methods=["POST"])
@login_required
@permission_required("ai_listing")
def toggle_asset(task_id: int, asset_id: int):
    """勾选或剔除不需要的详情插图."""
    asset = db.query_one("SELECT * FROM ai_listing_assets WHERE id = %s AND task_id = %s", (asset_id, task_id))
    if not asset:
        return jsonify({"ok": False, "error": "资产未找到"}), 404

    new_state = 0 if asset["is_selected"] else 1
    db.execute(
        "UPDATE ai_listing_assets SET is_selected = %s WHERE id = %s",
        (new_state, asset_id)
    )
    return jsonify({"ok": True, "is_selected": new_state})


@bp.route("/task/<int:task_id>/asset/<int:asset_id>/translate", methods=["POST"])
@login_required
@permission_required("ai_listing")
def translate_asset(task_id: int, asset_id: int):
    """手动触发单张图片翻译与AI重绘."""
    asset = db.query_one("SELECT * FROM ai_listing_assets WHERE id = %s AND task_id = %s", (asset_id, task_id))
    if not asset:
        return jsonify({"ok": False, "error": "资产未找到"}), 404

    prompt_text = request.json.get("prompt_text", "").strip() if request.json else ""

    try:
        new_key = translate_asset_image(asset_id, prompt_text=prompt_text, user_id=current_user.id)
        return jsonify({"ok": True, "transformed_url": new_key})
    except Exception as e:
        log.exception("AI 图片翻译失败")
        return jsonify({"ok": False, "error": f"AI 翻译失败: {e}"}), 500


@bp.route("/task/<int:task_id>/asset/reorder", methods=["POST"])
@login_required
@permission_required("ai_listing")
def reorder_assets(task_id: int):
    """拖拽更新轮播图与详情图的 sort_order 顺序."""
    data = request.get_json() or {}
    asset_ids = data.get("asset_ids", [])

    for idx, asset_id in enumerate(asset_ids):
        db.execute(
            "UPDATE ai_listing_assets SET sort_order = %s WHERE id = %s AND task_id = %s",
            (idx, asset_id, task_id)
        )
    return jsonify({"ok": True})


@bp.route("/task/<int:task_id>/rerun", methods=["POST"])
@login_required
@permission_required("ai_listing")
def rerun_task(task_id: int):
    """重新执行上品任务."""
    task = db.query_one("SELECT * FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        return jsonify({"ok": False, "error": "任务未找到"}), 404

    db.execute(
        "UPDATE ai_listing_tasks SET status = 'pending', error_message = NULL WHERE id = %s",
        (task_id,)
    )

    # 启动后台任务
    start_background_task(_run_ai_listing_task, task_id, current_user.id)
    return jsonify({"ok": True})

