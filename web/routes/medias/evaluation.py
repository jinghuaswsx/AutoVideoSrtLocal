"""AI 评估路由。

由 ``web.routes.medias`` package 在 PR 2.9 抽出；行为不变。
"""
from __future__ import annotations

from flask import abort, jsonify
from flask_login import login_required

from appcore import material_evaluation, medias

from . import bp
from ._helpers import _can_access_product, _material_evaluation_message


@bp.route("/api/products/<int:pid>/evaluate", methods=["POST"])
@login_required
def api_product_evaluate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    result = material_evaluation.evaluate_product_if_ready(pid, force=True, manual=True)
    message = _material_evaluation_message(result)
    payload = {"ok": result.get("status") == "evaluated", "message": message, "result": result}
    if result.get("status") == "evaluated":
        return jsonify(payload)
    return jsonify({**payload, "error": message}), 400


@bp.route("/api/products/<int:pid>/evaluate/request-preview", methods=["GET"])
@login_required
def api_product_evaluate_request_preview(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    try:
        payload = material_evaluation.build_request_debug_payload(pid, include_base64=False)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    payload["full_payload_url"] = f"/medias/api/products/{pid}/evaluate/request-payload"
    return jsonify({"ok": True, "payload": payload})


@bp.route("/api/products/<int:pid>/evaluate/request-payload", methods=["GET"])
@login_required
def api_product_evaluate_request_payload(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    try:
        payload = material_evaluation.build_request_debug_payload(pid, include_base64=True)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "payload": payload})
