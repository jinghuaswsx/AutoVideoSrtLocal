"""AI 评估路由。

由 ``web.routes.medias`` package 在 PR 2.9 抽出；行为不变。
"""
from __future__ import annotations

from pathlib import Path

from flask import abort, request
from flask_login import login_required

from appcore import material_evaluation, medias
from web.auth import admin_required

from . import bp
from ._helpers import _can_access_product
from web.services.media_evaluation import (
    build_product_evaluation_country_rerun_response as _build_product_evaluation_country_rerun_response_impl,
    build_product_evaluation_payload_response as _build_product_evaluation_payload_response_impl,
    build_product_evaluation_preview_response as _build_product_evaluation_preview_response_impl,
    build_product_evaluation_response as _build_product_evaluation_response_impl,
    build_product_evaluation_start_response as _build_product_evaluation_start_response_impl,
    build_product_evaluation_status_response as _build_product_evaluation_status_response_impl,
    media_evaluation_flask_response as _media_evaluation_flask_response_impl,
)
from web.services.artifact_download import send_file_with_range


def _routes_module():
    from web.routes import medias as routes

    return routes


def _optional_media_item_id() -> int | None:
    raw = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        raw = payload.get("media_item_id")
    if raw is None:
        raw = request.args.get("media_item_id")
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _optional_product_url_override() -> str | None:
    payload = {}
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
    raw = (
        payload.get("product_url_override")
        or payload.get("product_link")
        or payload.get("source_product_url")
        or request.args.get("product_url_override")
        or request.args.get("product_link")
        or request.args.get("source_product_url")
    )
    text = str(raw or "").strip()
    return text or None


def _response_kwargs() -> dict:
    kwargs = {}
    media_item_id = _optional_media_item_id()
    product_url_override = _optional_product_url_override()
    if media_item_id:
        kwargs["media_item_id"] = media_item_id
    if product_url_override:
        kwargs["product_url_override"] = product_url_override
    return kwargs


def _wants_sync_evaluation() -> bool:
    payload = request.get_json(silent=True) or {} if request.method == "POST" else {}
    raw = request.args.get("sync")
    return str(raw or "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("sync"))


def _build_product_evaluation_response(
    pid: int,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
):
    return _build_product_evaluation_response_impl(
        pid,
        media_item_id=media_item_id,
        product_url_override=product_url_override,
        evaluate_product_fn=material_evaluation.evaluate_product_if_ready,
        material_evaluation_message_fn=_routes_module()._material_evaluation_message,
    )


def _build_product_evaluation_start_response(
    pid: int,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
):
    return _build_product_evaluation_start_response_impl(
        pid,
        media_item_id=media_item_id,
        product_url_override=product_url_override,
    )


def _build_product_evaluation_status_response(pid: int, run_id: str):
    return _build_product_evaluation_status_response_impl(pid, run_id)


def _build_product_evaluation_country_rerun_response(pid: int, run_id: str, country_code: str):
    return _build_product_evaluation_country_rerun_response_impl(pid, run_id, country_code)


def _build_product_evaluation_preview_response(
    pid: int,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
):
    return _build_product_evaluation_preview_response_impl(
        pid,
        media_item_id=media_item_id,
        product_url_override=product_url_override,
        build_request_debug_payload_fn=material_evaluation.build_request_debug_payload,
    )


def _build_product_evaluation_payload_response(
    pid: int,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
):
    return _build_product_evaluation_payload_response_impl(
        pid,
        media_item_id=media_item_id,
        product_url_override=product_url_override,
        build_request_debug_payload_fn=material_evaluation.build_request_debug_payload,
    )


def _media_evaluation_flask_response(result):
    return _media_evaluation_flask_response_impl(result)


@bp.route("/api/products/<int:pid>/evaluate", methods=["POST"])
@login_required
def api_product_evaluate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    kwargs = _response_kwargs()
    if _wants_sync_evaluation():
        result = routes._build_product_evaluation_response(pid, **kwargs) if kwargs else routes._build_product_evaluation_response(pid)
    else:
        result = routes._build_product_evaluation_start_response(pid, **kwargs) if kwargs else routes._build_product_evaluation_start_response(pid)
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/status", methods=["GET"])
@login_required
@admin_required
def api_product_evaluate_status(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    result = routes._build_product_evaluation_status_response(pid, str(request.args.get("run_id") or ""))
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/<run_id>/countries/<country_code>/rerun", methods=["POST"])
@login_required
@admin_required
def api_product_evaluate_country_rerun(pid: int, run_id: str, country_code: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    result = routes._build_product_evaluation_country_rerun_response(pid, run_id, country_code)
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/request-preview", methods=["GET"])
@login_required
def api_product_evaluate_request_preview(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    kwargs = _response_kwargs()
    result = routes._build_product_evaluation_preview_response(pid, **kwargs) if kwargs else routes._build_product_evaluation_preview_response(pid)
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/request-payload", methods=["GET"])
@login_required
def api_product_evaluate_request_payload(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes_module()
    kwargs = _response_kwargs()
    result = routes._build_product_evaluation_payload_response(pid, **kwargs) if kwargs else routes._build_product_evaluation_payload_response(pid)
    return routes._media_evaluation_flask_response(result)


@bp.route("/api/products/<int:pid>/evaluate/clip", methods=["GET"])
@login_required
@admin_required
def api_product_evaluate_clip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    try:
        path = material_evaluation.evaluation_clip_preview_file(
            pid,
            media_item_id=_optional_media_item_id(),
        )
    except ValueError:
        abort(404)
    clip_path = Path(path)
    if not clip_path.is_absolute():
        clip_path = clip_path.resolve()
    if not clip_path.is_file():
        abort(404)
    return send_file_with_range(str(clip_path))
