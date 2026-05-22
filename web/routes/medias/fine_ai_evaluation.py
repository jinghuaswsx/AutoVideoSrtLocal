"""Fine AI evaluation routes for one product and five countries."""

from __future__ import annotations

from flask import jsonify, render_template, request, url_for
from flask_login import login_required

from appcore import medias
from appcore.fine_ai_evaluation_service import (
    FineAiEvaluationError,
    FineAiEvaluationNotFound,
    ProductNotFoundError,
    get_service,
)

from . import bp
from ._helpers import _can_access_product, _is_admin


def _ok(data, status: int = 200):
    return jsonify({"success": True, "data": data, "error": None}), status


def _err(code: str, message: str, status: int = 400):
    return jsonify({"success": False, "data": None, "error": {"code": code, "message": message}}), status


def _require_product_or_error(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        return None, _err("PRODUCT_NOT_FOUND", "Product not found", 404)
    if not _is_admin():
        return None, _err("FORBIDDEN", "Admin permission required", 403)
    return product, None


def _payload() -> dict:
    return request.get_json(silent=True) or {}


@bp.route("/products/<int:pid>/ai-evaluation/<evaluation_run_id>", methods=["GET"])
@login_required
def product_fine_ai_evaluation_detail_page(pid: int, evaluation_run_id: str):
    _product, error = _require_product_or_error(pid)
    if error:
        return error
    return render_template(
        "fine_ai_evaluation_detail.html",
        page_config={
            "mode": "product",
            "product_id": str(pid),
            "evaluation_run_id": str(evaluation_run_id or ""),
            "status_url": url_for(
                "medias.api_product_fine_ai_evaluation_status",
                pid=pid,
                evaluation_run_id=evaluation_run_id,
            ),
            "result_url": url_for(
                "medias.api_product_fine_ai_evaluation_result",
                pid=pid,
                evaluation_run_id=evaluation_run_id,
            ),
            "rerun_url_template": url_for(
                "medias.api_product_fine_ai_evaluation_country_rerun",
                pid=pid,
                evaluation_run_id=evaluation_run_id,
                country_code="{country}",
            ).replace("%7Bcountry%7D", "{country}"),
            "return_url": url_for("medias.index"),
            "title": "AI精细评估独立页",
        },
    )


@bp.route("/api/products/<int:pid>/ai-evaluation", methods=["POST"])
@login_required
def api_product_fine_ai_evaluation_create(pid: int):
    _product, error = _require_product_or_error(pid)
    if error:
        return error
    payload = _payload()
    try:
        service = get_service()
        run = service.create_run(
            pid,
            force_refresh=bool(payload.get("force_refresh", False)),
            countries=payload.get("countries") or None,
            include_assets=payload.get("include_assets", True) is not False,
            include_videos=payload.get("include_videos", True) is not False,
            locale=str(payload.get("locale") or "zh-CN"),
            product_url_override=payload.get("product_link") or payload.get("product_url_override"),
        )
        service.start_run_async(run["evaluation_run_id"])
        return _ok(run, 202)
    except ProductNotFoundError:
        return _err("PRODUCT_NOT_FOUND", "Product not found", 404)
    except ValueError as exc:
        return _err("INVALID_REQUEST", str(exc), 400)
    except FineAiEvaluationError as exc:
        return _err(exc.code, str(exc), 400)


@bp.route("/api/products/<int:pid>/ai-evaluation/<evaluation_run_id>/status", methods=["GET"])
@login_required
def api_product_fine_ai_evaluation_status(pid: int, evaluation_run_id: str):
    _product, error = _require_product_or_error(pid)
    if error:
        return error
    try:
        return _ok(get_service().get_status(pid, evaluation_run_id))
    except FineAiEvaluationNotFound as exc:
        return _err(exc.code, "Evaluation run not found", 404)


@bp.route("/api/products/<int:pid>/ai-evaluation/<evaluation_run_id>", methods=["GET"])
@login_required
def api_product_fine_ai_evaluation_result(pid: int, evaluation_run_id: str):
    _product, error = _require_product_or_error(pid)
    if error:
        return error
    try:
        return _ok(get_service().get_result(pid, evaluation_run_id))
    except FineAiEvaluationNotFound as exc:
        return _err(exc.code, "Evaluation run not found", 404)


@bp.route("/api/products/<int:pid>/ai-evaluation/latest", methods=["GET"])
@login_required
def api_product_fine_ai_evaluation_latest(pid: int):
    _product, error = _require_product_or_error(pid)
    if error:
        return error
    try:
        return _ok(get_service().get_latest_result(pid))
    except FineAiEvaluationNotFound as exc:
        return _err(exc.code, "Evaluation run not found", 404)


@bp.route("/api/products/<int:pid>/ai-evaluation/<evaluation_run_id>/countries/<country_code>/rerun", methods=["POST"])
@login_required
def api_product_fine_ai_evaluation_country_rerun(pid: int, evaluation_run_id: str, country_code: str):
    _product, error = _require_product_or_error(pid)
    if error:
        return error
    payload = _payload()
    try:
        data = get_service().rerun_country(
            pid,
            evaluation_run_id,
            country_code,
            force_refresh=bool(payload.get("force_refresh", True)),
            include_assets=payload.get("include_assets", True) is not False,
            include_videos=payload.get("include_videos", True) is not False,
        )
        return _ok(data, 202)
    except FineAiEvaluationNotFound as exc:
        return _err(exc.code, "Evaluation run not found", 404)
    except ValueError as exc:
        return _err("INVALID_COUNTRY_CODE", str(exc), 400)
