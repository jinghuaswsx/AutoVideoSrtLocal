from __future__ import annotations

from flask import Blueprint, jsonify
from flask_login import login_required

bp = Blueprint("tos_upload", __name__, url_prefix="/api/tos-upload")


@bp.route("/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    return jsonify({"error": "新建任务已切换为本地上传，通用 TOS 直传入口已停用"}), 410


@bp.route("/complete", methods=["POST"])
@login_required
def complete_upload():
    return jsonify({"error": "新建任务已切换为本地上传，TOS complete 创建任务入口已停用"}), 410
